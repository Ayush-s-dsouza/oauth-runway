"""Markdown export of a guide, for pasting into a ticket, README or Slack."""

from __future__ import annotations

from .schema import DIFFICULTY_LABEL, OWNER_LABEL, STATUS_LABEL, Guide

GATE_TITLES = {1: "Works for you", 2: "Works for invited/test users", 3: "Works for anyone"}


def _cite(ids: list[str], order: dict[str, int]) -> str:
    nums = [f"[{order[i]}]" for i in ids if i in order]
    return " " + "".join(nums) if nums else ""


def to_markdown(g: Guide) -> str:
    order = {e.id: n for n, e in enumerate(g.evidence, 1)}
    v = g.verdict
    p = g.provenance
    out: list[str] = []
    w = out.append
    w(f"# {g.app.name}: OAuth app setup and approval")
    w("")
    if p.kind == "verified":
        tested = any(e.status == "tested" for e in g.evidence)
        w(f"> {'Hand-tested and source-checked' if tested else 'Source-checked (not yet hand-tested)'} on "
          f"{p.verified_on or p.generated_at[:10]}. Evidence status is shown per source.")
    else:
        w(f"> Agent-generated on {p.generated_at[:10]} ({p.model}); not human-reviewed. "
          f"{p.sources_consulted} sources consulted, {p.probes_run} live probes.")
    w("")
    w(f"**Difficulty:** {DIFFICULTY_LABEL[v.difficulty]}  ")
    w(f"**Hands-on effort:** {v.hands_on_effort}  ")
    w(f"**First connection (your own account):** {v.time_to_first_connection}  ")
    w(f"**Approval wait before public use:** {v.approval_wait}")
    w("")
    w(v.summary)
    w("")
    if g.app.developer_portal_url:
        w(f"Developer portal: {g.app.developer_portal_url}  ")
    if g.app.oauth_docs_url:
        w(f"OAuth docs: {g.app.oauth_docs_url}")
    w("")
    w("## The three gates")
    for gate in g.gates:
        w(f"### Gate {gate.level}: {GATE_TITLES[gate.level]} ({gate.name})")
        w(f"- **Unlocks:** {gate.unlocks}{_cite(gate.evidence_ids, order)}")
        w(f"- **Time to reach:** {gate.time_to_reach}")
        w(f"- **Review required:** {'yes' if gate.requires_review else 'no'}")
        for lim in gate.limits:
            w(f"- Limit: {lim}")
        w("")
    w("## Steps")
    for n, s in enumerate(g.steps, 1):
        w(f"{n}. **{s.title}** · _{OWNER_LABEL[s.owner]}_ · gate {s.gate}"
          + (f" · {s.est_time}" if s.est_time else "") + _cite(s.evidence_ids, order))
        w(f"   {s.detail}")
        if s.url:
            w(f"   Link: {s.url}")
        for kv in s.values:
            w(f"   - {kv.label}: `{kv.value}`")
        for gt in s.gotchas:
            w(f"   - ⚠ {gt}")
    w("")
    if g.requirements:
        w("## Requirements")
        for r in g.requirements:
            w(f"- [ ] **{r.item}** (gate {r.gate}): {r.detail}{_cite(r.evidence_ids, order)}")
        w("")
    if g.scopes:
        w("## Scopes that change the review")
        w("| Scope | Tier | Effect |")
        w("|---|---|---|")
        for s in g.scopes:
            w(f"| `{s.scope}` | {s.tier} | {s.effect}{_cite(s.evidence_ids, order)} |")
        w("")
    if g.blockers:
        w("## If you get blocked")
        for b in g.blockers:
            w(f"- **{b.symptom}**: {b.cause} → {b.fix}" + (f" (contact: {b.contact})" if b.contact else "")
              + _cite(b.evidence_ids, order))
        w("")
    w("## Composio")
    c = g.composio
    if c.managed_oauth_available is not None:
        w(f"- Composio managed OAuth available: **{'yes' if c.managed_oauth_available else 'no'}**"
          + (f" (toolkit `{c.toolkit_slug}`)" if c.toolkit_slug else ""))
    w(f"- {c.note}")
    w("- Redirect URI: paste the callback shown in your Composio auth config. Composio's docs show "
      "`https://backend.composio.dev/api/v3/toolkits/auth/callback`; the toolkit API's default field still "
      "shows `https://backend.composio.dev/api/v1/auth-apps/add`.")
    w("")
    if g.open_questions:
        w("## Open questions")
        for q in g.open_questions:
            w(f"- {q}")
        w("")
    w("## Sources")
    for e in g.evidence:
        src = e.source_url or "(no source)"
        flags = f" ⚠ {'; '.join(e.flags)}" if e.flags else ""
        w(f"{order[e.id]}. [{STATUS_LABEL[e.status]}] {e.claim} {src}{flags}")
    w("")
    return "\n".join(out)
