"""research -> synthesis -> validate -> wrap with provenance -> save."""

from __future__ import annotations

import datetime as dt
import time

from . import store
from .agent import RESEARCH_MODEL, SYNTHESIS_MODEL, Emit, Research
from .schema import Guide, Provenance, slugify
from .validate import evidence_stats, validate


async def generate_guide(query: str, emit: Emit, slug: str | None = None) -> Guide:
    started = time.monotonic()
    await emit({"type": "start", "text": f"Researching {query} (research: {RESEARCH_MODEL}, synthesis: {SYNTHESIS_MODEL})"})
    research = Research(query, emit)
    draft = await research.run()

    await emit({"type": "validating", "text": "checking every citation against what was actually retrieved"})
    draft, notes = validate(draft, research.seen_urls, research.probed_hosts)
    if research.lock_stats and any(research.lock_stats.values()):
        notes.append(f"evidence lock: dropped {research.lock_stats['invented']} evidence ids the writer invented, "
                     f"restored {research.lock_stats['altered_quotes']} quotes it altered")
    stats = evidence_stats(draft)
    await emit({"type": "validating", "text": ", ".join(f"{v} {k}" for k, v in stats.items()) or "no evidence"})
    for n in notes:
        await emit({"type": "warning", "text": n})

    breakdown = research.cost_breakdown()
    guide = Guide(
        **draft.model_dump(),
        slug=slug or slugify(draft.app.name or query),
        provenance=Provenance(
            kind="agent",
            generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            model=f"{RESEARCH_MODEL} -> {SYNTHESIS_MODEL}",
            research_model=SYNTHESIS_MODEL if research.escalated else RESEARCH_MODEL,
            synthesis_model=SYNTHESIS_MODEL,
            escalated=research.escalated,
            escalation_reason=research.escalation_reason,
            verification_notes=notes,
            sources_consulted=len(research.seen_urls),
            probes_run=research.probes_run,
            research_seconds=int(time.monotonic() - started),
            est_cost_usd=research.total_cost(),
            cost_breakdown=breakdown,
            usage=research.usage,
            served_models=sorted({x["served"] for x in research.served}),
        ),
    )
    store.save_generated(guide)
    await emit({"type": "done", "slug": guide.slug,
                "text": f"guide ready in {guide.provenance.research_seconds}s (~${guide.provenance.est_cost_usd}: "
                        + ", ".join(f"{m} ${c}" for m, c in breakdown.items()) + ")"})
    return guide
