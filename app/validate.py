"""Deterministic post-checks on an agent-produced guide.

The model is asked to cite evidence for every claim; this module does not
trust that it did. It:
  * downgrades evidence whose source_url never appeared in this run's search
    results, fetches or probes (likely recalled or fabricated) to 'inferred';
  * downgrades 'probed' evidence with no matching probe, and any 'tested'
    (reserved for human hand-testing);
  * upgrades retrieved 'community' evidence that is actually first-party (the
    provider's own domain, or a GitHub org named after the provider, e.g.
    github.com/teamleadercrm for Teamleader) to 'official_doc';
  * labels every step / requirement / scope / blocker with a support level
    derived from the evidence it cites;
  * flags verdicts claiming review without any review evidence.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .schema import GuideDraft

STRENGTH = {"tested": 4, "probed": 3, "official_doc": 3, "community": 2, "inferred": 0}
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu"}
# Platforms many unrelated vendors share: identity is the full host, never the domain.
_SHARED_HOSTS = {"github.com", "github.io", "readme.io", "gitbook.io", "zendesk.com", "atlassian.net",
                 "notion.site", "medium.com", "postman.com", "apiary.io", "stoplight.io", "netlify.app",
                 "vercel.app", "herokuapp.com", "freshdesk.com", "helpscoutdocs.com", "intercom.help"}
# User posts stay 'community' even on the provider's own domain or GitHub org.
_FORUM_HOST_LABELS = {"community", "devcommunity", "devforum", "forum", "forums", "discuss", "discourse",
                      "answers", "stackoverflow", "reddit"}
_FORUM_PATH_SEGMENTS = {"community", "answers", "questions", "discussion", "discussions", "forum", "forums",
                        "t", "issues", "pull", "thread", "threads", "topic", "topics"}


def norm_url(u: str | None) -> str:
    if not u:
        return ""
    p = urlparse(u.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}".lower()


def _registrable(host: str) -> str:
    """'support.focus.teamleader.eu' -> 'teamleader.eu'; 'x.example.co.uk' -> 'example.co.uk'."""
    host = host.lower().removeprefix("www.")
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL:
        reg = ".".join(labels[-3:])
    else:
        reg = ".".join(labels[-2:])
    return host if reg in _SHARED_HOSTS else reg


def _provider_domains(draft: GuideDraft) -> set[str]:
    urls = (draft.app.website, draft.app.developer_portal_url, draft.app.oauth_docs_url)
    return {_registrable(h) for u in urls if u and (h := urlparse(u).hostname)}


def _is_forum(host: str, path: str) -> bool:
    if any(lbl in _FORUM_HOST_LABELS for lbl in host.split(".")):
        return True
    return any(seg in _FORUM_PATH_SEGMENTS for seg in path.lower().split("/"))


def is_first_party(url: str | None, provider_domains: set[str]) -> bool:
    """True if `url` is the provider's own documentation: on one of its own
    domains, or in a GitHub org whose name starts with the provider's brand
    (teamleader.eu -> github.com/teamleadercrm). Forums, Q&A, issues and
    shared doc hosts never count; brands under 4 chars never match an org."""
    p = urlparse(url or "")
    host = (p.hostname or "").lower().removeprefix("www.")
    if not host or _is_forum(host, p.path):
        return False
    if _registrable(host) in provider_domains:
        return True
    if host == "github.com":
        org = p.path.strip("/").split("/")[0].lower()
        brands = {d.split(".")[0] for d in provider_domains} - {"github"}
        return any(len(b) >= 4 and org.startswith(b) for b in brands)
    return False


def validate(draft: GuideDraft, seen_urls: set[str], probed_hosts: set[str]) -> tuple[GuideDraft, list[str]]:
    notes: list[str] = []
    seen = {norm_url(u) for u in seen_urls}
    provider_domains = _provider_domains(draft)
    by_id = {}
    upgraded = 0
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
            elif ev.status == "community" and is_first_party(ev.source_url, provider_domains):
                ev.status = "official_doc"  # not a flag: flags mark weakened evidence
                upgraded += 1
        by_id[ev.id] = ev

    support_for = label_support(draft)

    unsupported_steps = sum(1 for s in draft.steps if s.support == "unsupported")
    if unsupported_steps:
        notes.append(f"{unsupported_steps} of {len(draft.steps)} steps have no retrieved source")
    downgraded = sum(1 for e in draft.evidence if e.flags)
    if downgraded:
        notes.append(f"{downgraded} evidence items downgraded by validator")
    if upgraded:
        notes.append(f"{upgraded} community-labelled sources were first-party and upgraded to official docs")

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
