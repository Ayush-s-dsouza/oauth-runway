"""FastAPI app: guide library, guide pages (HTML / Markdown / JSON) and live
generation streamed over Server-Sent Events.

Generation runs as a background job whose events are buffered, so the live
page can be reloaded or shared mid-run and replays from the start."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from . import store  # noqa: E402
from .agent import AgentFailed  # noqa: E402
from .pipeline import generate_guide  # noqa: E402
from .render_md import to_markdown  # noqa: E402
from .schema import DIFFICULTY_LABEL, OWNER_LABEL, STATUS_LABEL, slugify  # noqa: E402

RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "3"))
DAILY_CAP = int(os.environ.get("GENERATION_DAILY_CAP", "40"))
REPO_URL = os.environ.get("REPO_URL", "")
COMPOSIO_REDIRECT_DOCS = "https://backend.composio.dev/api/v3/toolkits/auth/callback"
COMPOSIO_REDIRECT_API_DEFAULT = "https://backend.composio.dev/api/v1/auth-apps/add"

HERE = Path(__file__).parent
app = FastAPI(title="OAuth Runway")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals.update(
    DIFFICULTY_LABEL=DIFFICULTY_LABEL, OWNER_LABEL=OWNER_LABEL, STATUS_LABEL=STATUS_LABEL, REPO_URL=REPO_URL,
    COMPOSIO_REDIRECT_DOCS=COMPOSIO_REDIRECT_DOCS, COMPOSIO_REDIRECT_API_DEFAULT=COMPOSIO_REDIRECT_API_DEFAULT,
)


@dataclass
class Job:
    id: str
    query: str
    slug: str
    events: list[dict] = field(default_factory=list)
    done: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    started: float = field(default_factory=time.monotonic)

    async def emit(self, ev: dict) -> None:
        ev = {**ev, "t": round(time.monotonic() - self.started, 1)}
        self.events.append(ev)
        self.changed.set()


JOBS: dict[str, Job] = {}
RUNNING_BY_SLUG: dict[str, str] = {}
_ip_hits: dict[str, deque] = defaultdict(deque)
_daily = {"day": dt.date.today(), "count": 0}

_QUERY_RE = re.compile(r"^[\w .:/&+\-]{2,100}$")


def _check_limits(ip: str) -> None:
    now = time.monotonic()
    dq = _ip_hits[ip]
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(429, "Too many generations from your address; try again in a minute.")
    if _daily["day"] != dt.date.today():
        _daily.update(day=dt.date.today(), count=0)
    if _daily["count"] >= DAILY_CAP:
        raise HTTPException(429, "Daily generation cap reached for this demo deployment. Library guides still work.")
    dq.append(now)
    _daily["count"] += 1


async def _run_job(job: Job) -> None:
    try:
        await generate_guide(job.query, job.emit, slug=job.slug)
    except AgentFailed as e:
        await job.emit({"type": "error", "text": str(e)})
    except Exception as e:  # surface, don't swallow: the live page must show failures
        await job.emit({"type": "error", "text": f"{type(e).__name__}: {str(e)[:300]}"})
    finally:
        job.done = True
        job.changed.set()
        RUNNING_BY_SLUG.pop(job.slug, None)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    curated, generated = store.list_all()
    order = list(DIFFICULTY_LABEL)
    curated.sort(key=lambda g: order.index(g.verdict.difficulty))
    return templates.TemplateResponse(request, "index.html", {"curated": curated, "generated": generated[:30]})


@app.get("/g/{slug}.md", response_class=PlainTextResponse)
def guide_md(slug: str):
    g = store.get(slug) or _404()
    return PlainTextResponse(to_markdown(g), headers={"Content-Disposition": f'inline; filename="{slug}-oauth-guide.md"'},
                             media_type="text/markdown; charset=utf-8")


@app.get("/g/{slug}.json")
def guide_json(slug: str):
    g = store.get(slug) or _404()
    return JSONResponse(json.loads(g.model_dump_json()))


@app.get("/g/{slug}", response_class=HTMLResponse)
def guide_page(request: Request, slug: str):
    g = store.get(slug) or _404()
    ev = {e.id: e for e in g.evidence}
    counts: dict[str, int] = {}
    for e in g.evidence:
        counts[e.status] = counts.get(e.status, 0) + 1
    owners = {k: sum(1 for s in g.steps if s.owner == k) for k in OWNER_LABEL}
    return templates.TemplateResponse(request, "guide.html", {"g": g, "ev": ev, "order": {e.id: n for n, e in enumerate(g.evidence, 1)}, "counts": counts, "owners": owners})


@app.post("/api/generate")
async def api_generate(request: Request):
    body = await request.json()
    query = (body.get("query") or "").strip()
    force = bool(body.get("force"))
    if not _QUERY_RE.match(query):
        raise HTTPException(400, "Enter an app name or website (letters, numbers, dots, slashes; max 100 chars).")
    slug = slugify(query)
    if not force and (g := store.fresh_generated(slug)):
        return {"slug": g.slug, "cached": True}
    if slug in RUNNING_BY_SLUG:
        return {"job_id": RUNNING_BY_SLUG[slug]}
    if force and (g := store.get(slug)) and g.provenance.kind == "verified":
        slug = f"{slug}-agent"  # never overwrite a hand-verified guide
    _check_limits(request.client.host if request.client else "unknown")
    job = Job(id=uuid.uuid4().hex[:10], query=query, slug=slug)
    JOBS[job.id] = job
    RUNNING_BY_SLUG[slug] = job.id
    asyncio.create_task(_run_job(job))
    return {"job_id": job.id}


@app.get("/generate/{job_id}", response_class=HTMLResponse)
def generate_page(request: Request, job_id: str):
    job = JOBS.get(job_id) or _404()
    return templates.TemplateResponse(request, "generate.html", {"job": job})


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = JOBS.get(job_id) or _404()

    async def stream():
        sent = 0
        while True:
            while sent < len(job.events):
                yield f"data: {json.dumps(job.events[sent])}\n\n"
                sent += 1
            if job.done:
                yield "event: end\ndata: {}\n\n"
                return
            job.changed.clear()
            try:
                await asyncio.wait_for(job.changed.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/status")
def status():
    curated, generated = store.list_all()
    return {"ok": True, "curated": len(curated), "generated": len(generated),
            "running": len(RUNNING_BY_SLUG), "generations_today": _daily["count"], "daily_cap": DAILY_CAP}


@app.get("/start")
def start_redirect(q: str = ""):
    return RedirectResponse(f"/?q={q}")


def _404():
    raise HTTPException(404, "Not found")
