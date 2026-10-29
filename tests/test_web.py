"""Web layer end to end with the research agent stubbed: POST /api/generate →
SSE events → saved guide → guide page / markdown."""

import json

import pytest
from fastapi.testclient import TestClient

from app import main, store
from app.schema import Guide, Provenance
from tests.test_core import draft


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "GENERATED_DIR", tmp_path / "generated")

    async def fake_generate(query, emit, slug=None):
        await emit({"type": "start", "text": f"Researching {query}"})
        await emit({"type": "search", "text": "acme oauth app review"})
        g = Guide(**draft().model_dump(), slug=slug,
                  provenance=Provenance(kind="agent", generated_at="2099-01-01T00:00:00+00:00", model="stub"))
        store.save_generated(g)
        await emit({"type": "done", "slug": slug, "text": "ready"})
        return g

    monkeypatch.setattr(main, "generate_guide", fake_generate)
    main._ip_hits.clear()
    main._daily["count"] = 0
    with TestClient(main.app) as c:
        yield c


def test_generate_stream_and_render(client):
    r = client.post("/api/generate", json={"query": "acme.com"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    events = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as s:
        for line in s.iter_lines():
            if line.startswith("data: ") and line != "data: {}":
                events.append(json.loads(line[6:]))
    assert [e["type"] for e in events] == ["start", "search", "done"]
    assert events[-1]["slug"] == "acme"

    assert client.get("/generate/" + job_id).status_code == 200
    page = client.get("/g/acme")
    assert page.status_code == 200 and "not been reviewed by a person" in page.text
    assert client.get("/g/acme.md").text.startswith("# Acme")

    # A second request for the same app is served from cache, not re-researched.
    again = client.post("/api/generate", json={"query": "Acme"})
    assert again.json() == {"slug": "acme", "cached": True}


def test_rejects_bad_input_and_rate_limits(client, monkeypatch):
    assert client.post("/api/generate", json={"query": "<script>"}).status_code == 400
    monkeypatch.setattr(main, "RATE_LIMIT_PER_MIN", 1)
    assert client.post("/api/generate", json={"query": "one.com"}).status_code == 200
    assert client.post("/api/generate", json={"query": "two.com"}).status_code == 429


def test_regenerate_never_overwrites_curated(client):
    r = client.post("/api/generate", json={"query": "GitHub", "force": True})
    job_id = r.json()["job_id"]
    assert main.JOBS[job_id].slug == "github-agent"


def test_library_lists_curated(client):
    html = client.get("/").text
    for name in ("GitHub", "Slack", "Shopify", "HubSpot", "Notion", "Gmail"):
        assert name in html


def test_citation_numbers_match_the_sources_list(client):
    """Each inline citation shows its source's position in the Sources list
    (a screenshot caught every claim numbering its own citations 1, 2, 3...)."""
    import re

    page = client.get("/g/gmail").text
    g = client.get("/g/gmail.json").json()
    pos = {e["id"]: n for n, e in enumerate(g["evidence"], 1)}
    cites = re.findall(r'href="#ev-([^"]+)"[^>]*>(\d+)</a>', page)
    assert cites and all(pos[i] == int(n) for i, n in cites)


def test_bundled_agent_samples_list_as_generated_not_curated(client, tmp_path, monkeypatch):
    from app import store as st

    sample_dir = tmp_path / "agent"
    sample_dir.mkdir()
    g = Guide(**draft().model_dump(), slug="zoomish",
              provenance=Provenance(kind="agent", generated_at="2099-01-01T00:00:00+00:00", model="haiku -> opus"))
    (sample_dir / "zoomish.json").write_text(g.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(st, "SAMPLES_DIR", sample_dir)
    curated, generated = st.list_all()
    assert "zoomish" in {x.slug for x in generated} and "zoomish" not in {x.slug for x in curated}
    page = client.get("/g/zoomish")
    assert page.status_code == 200 and "not been reviewed by a person" in page.text
