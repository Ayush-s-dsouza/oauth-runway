"""Two-phase research agent, tiered by model cost -- not a single loop on one
model. The routing is decided here, in code, never by the caller or the end
user:

  Phase 1 (research, cheap model): tool-calling loop (search/fetch/probe/
    composio) that only has to extract and cite facts -- compile_findings,
    not the final guide. Narrow, well-specified, the kind of task a cheap
    model handles fine.
  Phase 2 (synthesis, strong model): no tools, works only from Phase 1's
    findings. This is the genuinely inferential step -- picking a verdict,
    building the three gates, deciding step ownership -- which is where
    model capability actually matters.

If Phase 1 stalls (no compile_findings call within its turn budget), it is
retried once on the strong model before giving up -- a stall detector, not a
quality judgment; there isn't yet evidence to support a smarter trigger.
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
import os
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import anthropic
from pydantic import ValidationError

from . import probes
from .composio_lookup import composio_toolkit
from .prompts import (
    RESEARCH_SYSTEM_PROMPT,
    RESEARCH_USER_TEMPLATE,
    SYNTHESIS_SYSTEM_PROMPT,
    SYNTHESIS_USER_TEMPLATE,
)
from .schema import Evidence, GuideDraft, ResearchFindings

RESEARCH_MODEL = os.environ.get("RESEARCH_MODEL", "claude-haiku-5-5")
SYNTHESIS_MODEL = os.environ.get("SYNTHESIS_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("AGENT_EFFORT", "medium")
RESEARCH_MAX_TURNS = 20
SYNTHESIS_MAX_TURNS = 5
MAX_SECONDS = int(os.environ.get("AGENT_MAX_SECONDS", "480"))
MAX_INVALID_SUBMITS = 2
# Coverage gate (harness steering for cheaper research models): before
# compile_findings is accepted, code checks that the research actually read
# provider pages and covered the areas a guide can't be written without. Off
# by default since the eval showed it helps (v3-v7); see DECISIONS.md.
COVERAGE_GATE = os.environ.get("COVERAGE_GATE", "1") == "1"
MIN_PAGES = int(os.environ.get("RESEARCH_MIN_PAGES", "6"))
# Research plan: the harness supplies the searches instead of asking a cheap
# model to plan them. The eval showed Haiku runs 2-4 searches (Opus ~9) and so
# never discovers changelog pages -- where recent limits (HubSpot's install
# cap, Slack's non-Marketplace rate limits) are announced.
RESEARCH_PLAN = os.environ.get("RESEARCH_PLAN", "1") == "1"
PLAN_QUERIES = [
    "{app} OAuth app install limit OR user limit for unreviewed or unlisted apps",
    "{app} developer changelog app limits OR distribution policy changes {year}",
    "{app} marketplace OR app directory listing review requirements and review time",
    "{app} OAuth scopes that require review OR sensitive OR restricted scopes",
    "{app} create OAuth app redirect URI developer docs",
]
MIN_SEARCHES = len(PLAN_QUERIES) - 1
# Evidence lock: the synthesis step may only point at findings by id; code
# copies claim/quote/url/status from the findings verbatim. The eval showed a
# Haiku writer invents 16-21% of its evidence and alters quotes (Opus: 1-5%).
# "1" = lock everything (claim too); "quote" = lock quote/url/status but let the
# writer reword the claim -- the eval showed a full lock stops the strong writer
# tightening research's overreaching claims (quote support 0.67 -> 0.57).
EVIDENCE_LOCK = os.environ.get("EVIDENCE_LOCK", "quote")
EVIDENCE_LOCK = EVIDENCE_LOCK if EVIDENCE_LOCK in ("1", "quote") else ""
MAX_COVERAGE_BOUNCES = 2
REQUIRED_AREAS = {
    "gate2": "limits for test users / unreviewed apps (user caps, install caps, token expiry, dev-mode restrictions)",
    "gate3": "what it takes to serve anyone (verification, marketplace/app-store review, security assessment) -- or confirmation there is none",
    "scope": "which scopes/permissions exist and which trigger extra review",
    "requirement": "concrete requirements (accounts, privacy policy, domain verification, minimum installs, etc.)",
}

# Models whose refusals can be rescued server-side (per Anthropic's current
# docs). Haiku 5.5 is deliberately NOT in this list -- its refusals have no
# server-side fallback, and sending the param anyway is a 400 risk for no gain.
FALLBACK_ELIGIBLE = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}

# List prices (USD per token). Approximate and display-only -- check the
# console for exact figures. Cache ratios for models without a published
# cache rate are approximated from Opus 5.5's own ratio (read=5% of input,
# write=125% of input).
PRICING = {
    "claude-opus-5-5": dict(input=4 / 1e6, output=20 / 1e6, cache_read=0.20 / 1e6, cache_write=5 / 1e6),
    "claude-haiku-5-5": dict(input=0.10 / 1e6, output=0.50 / 1e6, cache_read=0.005 / 1e6, cache_write=0.125 / 1e6),
    "claude-sonnet-5-5": dict(input=2 / 1e6, output=10 / 1e6, cache_read=0.20 / 1e6, cache_write=2.5 / 1e6),
}
_DEFAULT_PRICE = PRICING["claude-opus-5-5"]  # unknown model: assume the expensive case rather than undercount
SEARCH_PRICE = 10 / 1000

Emit = Callable[[dict], Awaitable[None]]


class AgentFailed(Exception):
    pass


def _price(model: str) -> dict:
    return PRICING.get(model, _DEFAULT_PRICE)


def _schema(model_cls) -> dict:
    return copy.deepcopy(model_cls.model_json_schema())


def _submit_guide_schema() -> dict:
    schema = _schema(GuideDraft)
    for d in schema.get("$defs", {}).values():  # validator-only fields the model shouldn't try to fill
        for f in ("flags", "support"):
            d.get("properties", {}).pop(f, None)
    return schema


COMPOSIO_TOOL = {
    "name": "composio_toolkit",
    "description": "Look up the provider in Composio's toolkit catalog: managed OAuth availability, Composio's default scopes, authorize/token URLs.",
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "App name or domain, e.g. 'hubspot' or 'notion.so'."}},
        "required": ["name"],
    },
}
PROBE_URL_TOOL = {
    "name": "probe_url",
    "description": "GET a public URL (e.g. developer portal, app-creation page, review docs). Returns status, redirect chain, title, whether it hits a login wall, and a text excerpt.",
    "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
}
PROBE_AUTHORIZE_TOOL = {
    "name": "probe_oauth_authorize",
    "description": "Request the provider's OAuth authorize endpoint with a deliberately invalid client_id and the Composio callback as redirect_uri. Shows whether the endpoint is live and how it rejects unknown clients. Creates nothing.",
    "input_schema": {
        "type": "object",
        "properties": {"authorize_url": {"type": "string"}, "scope": {"type": "string", "description": "Optional scope string to include."}},
        "required": ["authorize_url"],
    },
}
PROBE_OIDC_TOOL = {
    "name": "probe_oidc_discovery",
    "description": "Fetch https://<domain>/.well-known/openid-configuration and return endpoints + scopes_supported.",
    "input_schema": {"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
}

RESEARCH_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 12},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 14, "max_content_tokens": 12000},
    COMPOSIO_TOOL,
    PROBE_URL_TOOL,
    PROBE_AUTHORIZE_TOOL,
    PROBE_OIDC_TOOL,
    {
        "name": "compile_findings",
        "description": "Hand off your research findings. Call exactly once, after research. Validation errors are returned to you to fix.",
        "input_schema": _schema(ResearchFindings),
    },
]
SYNTHESIS_TOOLS: list[dict[str, Any]] = [
    {
        "name": "submit_guide",
        "description": "Submit the finished guide. Call exactly once. Validation errors are returned to you to fix.",
        "input_schema": _submit_guide_schema(),
    },
]


def _collect_urls(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("url", "final_url", "requested_url", "source_url") and isinstance(v, str):
                out.add(v)
            else:
                _collect_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_urls(v, out)


def _sanitize_fallback(content: list) -> list:
    """After a mid-output fallback, drop model-internal blocks that precede the
    last fallback marker before echoing the turn back (per API rules)."""
    idx = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    if idx < 0:
        return content
    result_ids = {getattr(b, "tool_use_id", None) for b in content[:idx]}
    kept = []
    for b in content[:idx]:
        if b.type in ("thinking", "redacted_thinking", "tool_use"):
            continue
        if b.type == "server_tool_use" and b.id not in result_ids:
            continue
        kept.append(b)
    return kept + list(content[idx:])


class Research:
    def __init__(self, query: str, emit: Emit):
        self.query = query
        self.emit = emit
        self.client = anthropic.AsyncAnthropic()
        self.seen_urls: set[str] = set()
        self.probed_hosts: set[str] = set()
        self.probes_run = 0
        self.searches = 0
        self.fetches = 0
        self.fetched_urls: set[str] = set()
        self.coverage_bounces = 0
        self.lock_stats: dict | None = None
        self.usage: dict[str, dict[str, int]] = {}
        self.escalated = False
        self.escalation_reason: str | None = None
        self.served: list[dict] = []  # [{phase, requested, served}] -- lets an eval assert no silent model substitution
        self.findings: ResearchFindings | None = None
        self.composio_lookups: list[dict] = []  # raw composio_toolkit results; code, not the writer, owns guide.composio facts
        self.started = time.monotonic()

    def _usage_for(self, model: str) -> dict:
        return self.usage.setdefault(model, {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "search": 0})

    def _track_usage(self, model: str, u) -> None:
        bucket = self._usage_for(model)
        bucket["in"] += u.input_tokens or 0
        bucket["out"] += u.output_tokens or 0
        bucket["cache_read"] += u.cache_read_input_tokens or 0
        bucket["cache_write"] += u.cache_creation_input_tokens or 0
        stu = getattr(u, "server_tool_use", None)
        bucket["search"] += (getattr(stu, "web_search_requests", 0) or 0) if stu else 0

    def cost_breakdown(self) -> dict[str, float]:
        out = {}
        for model, u in self.usage.items():
            p = _price(model)
            out[model] = round(
                u["in"] * p["input"] + u["out"] * p["output"] + u["cache_read"] * p["cache_read"]
                + u["cache_write"] * p["cache_write"] + u["search"] * SEARCH_PRICE,
                4,
            )
        return out

    def total_cost(self) -> float:
        return round(sum(self.cost_breakdown().values()), 4)

    async def _on_block(self, block: Any) -> None:
        t = block.type
        if t == "server_tool_use":
            inp = block.input if isinstance(block.input, dict) else {}
            if block.name == "web_search":
                self.searches += 1
                await self.emit({"type": "search", "text": inp.get("query", "")})
            elif block.name == "web_fetch":
                self.fetches += 1
                if inp.get("url"):
                    self.seen_urls.add(inp["url"])
                    self.fetched_urls.add(inp["url"])
                await self.emit({"type": "fetch", "text": inp.get("url", "")})
        elif t in ("web_search_tool_result", "web_fetch_tool_result"):
            before = len(self.seen_urls)
            _collect_urls(block.model_dump(), self.seen_urls)
            if t == "web_search_tool_result":
                await self.emit({"type": "results", "text": f"{len(self.seen_urls) - before} new sources"})
        elif t == "thinking" and getattr(block, "thinking", ""):
            await self.emit({"type": "thinking", "text": block.thinking[:600]})
        elif t == "text" and block.text.strip():
            await self.emit({"type": "note", "text": block.text.strip()[:600]})

    async def _run_tool(self, name: str, inp: dict) -> dict:
        if name == "composio_toolkit":
            await self.emit({"type": "composio", "text": f"Composio catalog: {inp.get('name')}"})
            out = await probes.run_with_timeout(composio_toolkit(inp.get("name", self.query)))
            self.composio_lookups.append(out)
            if out.get("available"):
                await self.emit({"type": "composio", "text": f"managed OAuth: {'yes' if out['managed_oauth_available'] else 'no'} ({out['toolkit_slug']})"})
        elif name in ("probe_url", "probe_oauth_authorize", "probe_oidc_discovery"):
            self.probes_run += 1
            target = inp.get("url") or inp.get("authorize_url") or inp.get("domain") or ""
            await self.emit({"type": "probe", "text": f"{name.removeprefix('probe_')}: {target}"})
            fn = {"probe_url": probes.probe_url, "probe_oauth_authorize": probes.probe_oauth_authorize,
                  "probe_oidc_discovery": probes.probe_oidc_discovery}[name]
            try:
                out = await probes.run_with_timeout(fn(**inp))
            except TypeError as e:  # model sent arguments that don't match the tool: tell it, don't crash the run
                out = {"error": f"bad arguments for {name}: {e}"}
            host = urlparse(target if "://" in target else f"https://{target}").hostname or ""
            self.probed_hosts.add(host.lower().removeprefix("www."))
            for u in (out.get("final_url"), out.get("requested_url")):
                if u:
                    self.probed_hosts.add((urlparse(u).hostname or "").lower().removeprefix("www."))
            summary = out.get("blocked") or out.get("error") or f"HTTP {out.get('status')} {out.get('title') or ''}".strip()
            await self.emit({"type": "probe_result", "text": summary[:200]})
        else:
            return {"error": f"unknown tool {name}"}
        _collect_urls(out, self.seen_urls)
        return out

    def _coverage_gaps(self, f: ResearchFindings) -> list[str]:
        """Deterministic check that research read real pages and covered the
        areas the synthesis step can't invent. Empty list = accepted."""
        if not COVERAGE_GATE:
            return []
        from .validate import norm_url

        gaps = []
        if self.fetches < MIN_PAGES:
            gaps.append(f"You read {self.fetches} pages (need at least {MIN_PAGES}). Fetch the provider's own docs "
                        "instead of relying on search-result snippets.")
        if RESEARCH_PLAN and self.searches < MIN_SEARCHES:
            gaps.append(f"You ran {self.searches} searches (need at least {MIN_SEARCHES}). Run the planned searches you "
                        "skipped -- especially the changelog one -- and fetch the best provider-owned result of each.")
        fetched = {norm_url(u) for u in self.fetched_urls}
        covered = {x.category for x in f.findings if x.status in ("official_doc", "probed") and norm_url(x.source_url) in fetched}
        covered |= {x.category for x in f.findings if x.status == "probed"}
        missing = [f"{k}: {v}" for k, v in REQUIRED_AREAS.items() if k not in covered]
        if missing:
            gaps.append("No finding from a provider page you actually fetched covers: " + " | ".join(missing))
        return gaps

    async def _phase(self, *, model, system_prompt, tools, messages, finish_tool, finish_schema, max_turns, phase_label):
        """Run turns until `finish_tool` is called with input that validates
        against `finish_schema`, or max_turns is exhausted (-> None, stalled)."""
        invalid = 0
        for turn in range(max_turns):
            await self.emit({"type": "turn", "text": f"{phase_label} turn {turn + 1} ({model})"})
            kwargs = dict(
                model=model, max_tokens=32000, system=system_prompt, tools=tools, messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": EFFORT}, cache_control={"type": "ephemeral"},
            )
            if model in FALLBACK_ELIGIBLE:
                kwargs["betas"] = ["server-side-fallback-2026-07-01"]
                kwargs["fallbacks"] = "default"
            async with self.client.beta.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_stop":
                        await self._on_block(event.content_block)
                msg = await stream.get_final_message()

            self._track_usage(model, msg.usage)
            self.served.append({"phase": phase_label, "requested": model, "served": getattr(msg, "model", model)})
            if msg.stop_reason == "refusal":
                raise AgentFailed(f"{phase_label}: the model declined this request")
            content = _sanitize_fallback(list(msg.content))
            messages.append({"role": "assistant", "content": content})
            if msg.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in content if b.type == "tool_use"]
            if not tool_uses:
                messages.append({"role": "user", "content": f"Call {finish_tool} with your result now."})
                continue

            finished = None
            other = [b for b in tool_uses if b.name != finish_tool]
            outs = await asyncio.gather(*(self._run_tool(b.name, dict(b.input)) for b in other), return_exceptions=True)
            outs = [{"error": f"tool failed: {type(o).__name__}: {str(o)[:300]}"} if isinstance(o, BaseException) else o for o in outs]
            results = [{"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out)[:12000]} for b, out in zip(other, outs)]
            for b in tool_uses:
                if b.name != finish_tool:
                    continue
                try:
                    finished = finish_schema.model_validate(b.input)
                    gaps = self._coverage_gaps(finished) if finish_tool == "compile_findings" else []
                    if gaps and self.coverage_bounces < MAX_COVERAGE_BOUNCES:
                        self.coverage_bounces += 1
                        await self.emit({"type": "warning", "text": f"research sent back ({self.coverage_bounces}/{MAX_COVERAGE_BOUNCES}): " + "; ".join(g[:80] for g in gaps)})
                        feedback = (
                            "Not accepted yet: the research is too thin to write a reliable guide.\n- "
                            + "\n- ".join(gaps)
                            + "\nResearch these, then call compile_findings again with ALL findings (old and new). "
                            "If the provider genuinely documents nothing on an area, say what you checked in open_questions."
                        )
                        results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": feedback})
                        finished = None
                        continue
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": "accepted"})
                except ValidationError as e:
                    invalid += 1
                    if invalid > MAX_INVALID_SUBMITS:
                        raise AgentFailed(f"{phase_label}: schema failed repeatedly: {e.errors()[:3]}")
                    results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                    "content": f"Validation failed; fix and call {finish_tool} again: {str(e)[:3000]}"})
            if finished is not None:
                return finished, messages
            if time.monotonic() - self.started > MAX_SECONDS:
                results.append({"type": "text", "text": f"Time is up. Call {finish_tool} now with what you have."})
            messages.append({"role": "user", "content": results})
        return None, messages

    async def run(self) -> GuideDraft:
        today = dt.date.today().isoformat()

        def research_messages():
            text = RESEARCH_USER_TEMPLATE.format(query=self.query, today=today)
            if RESEARCH_PLAN:
                year = dt.date.today().year
                plan = "\n".join(f"{i}. {q.format(app=self.query, year=f'{year - 1} {year}')}" for i, q in enumerate(PLAN_QUERIES, 1))
                text += ("\nResearch plan -- run each of these searches (adjust wording if needed), and fetch the most "
                         "relevant provider-owned page from each before calling compile_findings. Changelog and "
                         "announcement pages matter: recent limits are often only documented there.\n" + plan + "\n")
            return [{"role": "user", "content": text}]

        findings, _ = await self._phase(
            model=RESEARCH_MODEL, system_prompt=RESEARCH_SYSTEM_PROMPT, tools=RESEARCH_TOOLS,
            messages=research_messages(), finish_tool="compile_findings", finish_schema=ResearchFindings,
            max_turns=RESEARCH_MAX_TURNS, phase_label="research",
        )
        if findings is None:
            self.escalated = True
            self.escalation_reason = f"research did not converge after {RESEARCH_MAX_TURNS} turns on {RESEARCH_MODEL}"
            await self.emit({"type": "escalate", "text": f"{RESEARCH_MODEL} didn't converge; retrying research on {SYNTHESIS_MODEL}"})
            findings, _ = await self._phase(
                model=SYNTHESIS_MODEL, system_prompt=RESEARCH_SYSTEM_PROMPT, tools=RESEARCH_TOOLS,
                messages=research_messages(), finish_tool="compile_findings", finish_schema=ResearchFindings,
                max_turns=RESEARCH_MAX_TURNS, phase_label="research (escalated)",
            )
            if findings is None:
                raise AgentFailed("research did not converge on either model")

        self.findings = findings
        await self.emit({"type": "findings", "text": f"{len(findings.findings)} cited findings compiled", "data": findings.model_dump()})
        await self.emit({"type": "drafting", "text": f"handing {len(findings.findings)} findings to {SYNTHESIS_MODEL}"})
        synth_messages = [{"role": "user", "content": SYNTHESIS_USER_TEMPLATE.format(
            query=self.query, today=today, findings_json=findings.model_dump_json(indent=2))}]
        draft, _ = await self._phase(
            model=SYNTHESIS_MODEL, system_prompt=SYNTHESIS_SYSTEM_PROMPT, tools=SYNTHESIS_TOOLS,
            messages=synth_messages, finish_tool="submit_guide", finish_schema=GuideDraft,
            max_turns=SYNTHESIS_MAX_TURNS, phase_label="synthesis",
        )
        if draft is None:
            raise AgentFailed(f"synthesis did not converge after {SYNTHESIS_MAX_TURNS} turns")
        if EVIDENCE_LOCK:
            draft = await self._lock_evidence(draft, findings)
        return draft

    async def _lock_evidence(self, draft: GuideDraft, findings: ResearchFindings) -> GuideDraft:
        """Rebuild the evidence list from research findings by id. Anything the
        writer invented (unknown id) is dropped and un-cited, so the validator
        marks the claims it propped up as unsupported. Quotes and URLs are the
        researcher's, verbatim -- the writer cannot alter them."""
        fmap = {f.id: f for f in findings.findings}
        referenced = {e.id for e in draft.evidence}
        for group in (draft.gates, draft.steps, draft.requirements, draft.scopes, draft.blockers):
            for item in group:
                referenced.update(item.evidence_ids)
        invented = sorted(i for i in referenced if i not in fmap)
        altered = sum(1 for e in draft.evidence if e.id in fmap and (e.quote or "") != (fmap[e.id].quote or ""))
        writer_claim = {e.id: e.claim for e in draft.evidence if e.id in fmap}
        claim_of = (lambda i: writer_claim.get(i, fmap[i].claim)) if EVIDENCE_LOCK == "quote" else (lambda i: fmap[i].claim)
        draft.evidence = [Evidence(id=i, claim=claim_of(i), source_url=fmap[i].source_url, quote=fmap[i].quote,
                                   status=fmap[i].status) for i in sorted(referenced & fmap.keys())]
        for group in (draft.gates, draft.steps, draft.requirements, draft.scopes, draft.blockers):
            for item in group:
                item.evidence_ids = [i for i in item.evidence_ids if i in fmap]
        self.lock_stats = {"invented": len(invented), "altered_quotes": altered}
        if invented or altered:
            await self.emit({"type": "warning", "text": f"evidence lock: dropped {len(invented)} invented evidence ids, restored {altered} altered quotes"})
        return draft
