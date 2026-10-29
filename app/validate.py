"""Deterministic post-checks on an agent-produced guide.

The model is asked to cite evidence for every claim; this module does not
trust that it did. It:
  * downgrades evidence whose source_url never appeared in this run's search
    results, fetches or probes (likely recalled or fabricated) to 'inferred';
  * downgrades 'probed' evidence with no matching probe, and any 'tested'
    (reserved for human hand-testing);
  * labels every step / requirement / scope / blocker with a support level
    derived from the evidence it cites;
  * flags verdicts claiming review without any review evidence.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .schema import GuideDraft

STRENGTH = {"tested": 4, "probed": 3, "official_doc": 3, "community": 2, "inferred": 0}


def norm_url(u: str | None) -> str:
    if not u:
        return ""
    p = urlparse(u.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}".lower()


def validate(draft: GuideDraft, seen_urls: set[str], probed_hosts: set[str]) -> tuple[GuideDraft, list[str]]:
    notes: list[str] = []
    seen = {norm_url(u) for u in seen_urls}
    by_id = {}
    for ev in draft.evidence:
        n = norm_url(ev.source_url)
        if ev.status == "tested":
            ev.status = "inferred"
            ev.flags.append("agent cannot hand-test; downgraded")
        if ev.status == "probed":
            host = (urlparse(ev.source_url or "").hostname or "").lower().removeprefix("www.")
            if host not in probed_hosts:
                ev.status = "inferred"
                ev.flags.append("no probe was run against this host")
        if ev.status in ("official_doc", "community"):
            if not ev.source_url:
                ev.status = "inferred"
                ev.flags.append("no source URL")
            elif n not in seen:
                ev.status = "inferred"
                ev.flags.append("cited URL was not retrieved during this research run")
        by_id[ev.id] = ev

    support_for = label_support(draft)

    unsupported_steps = sum(1 for s in draft.steps if s.support == "unsupported")
    if unsupported_steps:
        notes.append(f"{unsupported_steps} of {len(draft.steps)} steps have no retrieved source")
    downgraded = sum(1 for e in draft.evidence if e.flags)
    if downgraded:
        notes.append(f"{downgraded} evidence items downgraded by validator")

    if draft.verdict.difficulty in ("light_review", "heavy_review"):
        gate3 = [g for g in draft.gates if g.level == 3]
        if not gate3 or support_for(gate3[0].evidence_ids) == "unsupported":
            notes.append("verdict claims a review step but gate 3 has no retrieved source")

    levels = [g.level for g in draft.gates]
    if levels != [1, 2, 3]:
        notes.append(f"expected gates [1,2,3], got {levels}")
    return draft, notes


def label_support(draft: GuideDraft):
    """Label every step / requirement / scope / blocker with the strength of
    the evidence it cites. Returns the scoring function for reuse."""
    by_id = {e.id: e for e in draft.evidence}

    def support_for(ids: list[str]) -> str:
        evs = [by_id[i] for i in ids if i in by_id]
        best = max((STRENGTH[e.status] for e in evs), default=0)
        if any(e.status in ("tested", "probed") for e in evs):
            return "verified"
        if best >= 3:
            return "sourced"
        if best == 2:
            return "community"
        return "unsupported"

    for group in (draft.steps, draft.requirements, draft.scopes, draft.blockers):
        for item in group:
            item.support = support_for(item.evidence_ids)
    return support_for


def evidence_stats(draft: GuideDraft) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in draft.evidence:
        out[e.status] = out.get(e.status, 0) + 1
    return out
