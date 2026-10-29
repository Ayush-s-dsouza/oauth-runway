"""Compare an agent-generated guide against the curated (source-checked) guide
for the same app, to measure where the agent is right, wrong or unsupported.

    python -m scripts.generate "Slack" --slug slack-agent
    python -m scripts.compare slack slack-agent
"""

import sys

from app import store
from app.validate import evidence_stats


def row(label, a, b):
    mark = "  " if str(a).strip().lower() == str(b).strip().lower() else "≠ "
    print(f"{mark}{label:<26} | {str(a)[:70]:<70} | {str(b)[:70]}")


def main(curated_slug: str, agent_slug: str) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    c, a = store.get(curated_slug), store.get(agent_slug)
    if not c or not a:
        sys.exit(f"missing guide: {curated_slug if not c else agent_slug}")
    print(f"{'':28}| {'CURATED (' + curated_slug + ')':<70} | AGENT ({agent_slug})")
    for f in ("difficulty", "time_to_first_connection", "approval_wait", "hands_on_effort"):
        row(f, getattr(c.verdict, f), getattr(a.verdict, f))
    for lvl in (1, 2, 3):
        cg = next((g for g in c.gates if g.level == lvl), None)
        ag = next((g for g in a.gates if g.level == lvl), None)
        row(f"gate {lvl} review?", cg and cg.requires_review, ag and ag.requires_review)
        row(f"gate {lvl} name", cg and cg.name, ag and ag.name)
    row("composio managed", c.composio.managed_oauth_available, a.composio.managed_oauth_available)
    row("evidence", evidence_stats(c), evidence_stats(a))
    row("steps (agent/you/wait)",
        [sum(s.owner == o for s in c.steps) for o in ("agent", "you", "provider_wait")],
        [sum(s.owner == o for s in a.steps) for o in ("agent", "you", "provider_wait")])
    cs = {s.scope.split("/")[-1].lower(): s.tier for s in c.scopes}
    as_ = {s.scope.split("/")[-1].lower(): s.tier for s in a.scopes}
    for k in sorted(set(cs) | set(as_)):
        row(f"scope {k[:18]}", cs.get(k, "-"), as_.get(k, "-"))
    unsupported = [s.title for s in a.steps if s.support == "unsupported"]
    print(f"\nagent steps with no retrieved source: {unsupported or 'none'}")
    print(f"agent validator notes: {a.provenance.verification_notes or 'none'}")
    print(f"agent open questions: {a.open_questions}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
