"""Guides live as JSON files:
  * curated: source-checked, committed in guides/
  * agent samples: agent output from the eval's shipped configuration, committed in
    guides/agent/ so a host with an ephemeral disk still shows real agent guides
  * generated: written to DATA_DIR at runtime by live research (lost when a free
    host sleeps or restarts)."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from .schema import Guide
from .validate import label_support

ROOT = Path(__file__).resolve().parent.parent
CURATED_DIR = ROOT / "guides"
SAMPLES_DIR = CURATED_DIR / "agent"
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
GENERATED_DIR = DATA_DIR / "generated"
CACHE_DAYS = 7


def _load(path: Path) -> Guide | None:
    try:
        g = Guide.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if g.provenance.kind == "verified":
        label_support(g)  # curated guides skip the agent validator; label from their evidence
    return g


def get(slug: str) -> Guide | None:
    for d in (CURATED_DIR, GENERATED_DIR, SAMPLES_DIR):  # a fresh live run beats a bundled sample
        p = d / f"{slug}.json"
        if p.exists():
            return _load(p)
    return None


def fresh_generated(slug: str) -> Guide | None:
    g = get(slug)
    if g is None:
        return None
    if g.provenance.kind == "verified":
        return g
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(g.provenance.generated_at)
    return g if age.days < CACHE_DAYS else None


def save_generated(guide: Guide) -> Path:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    p = GENERATED_DIR / f"{guide.slug}.json"
    p.write_text(guide.model_dump_json(indent=2), encoding="utf-8")
    return p


def list_all() -> tuple[list[Guide], list[Guide]]:
    curated = [g for p in sorted(CURATED_DIR.glob("*.json")) if (g := _load(p))]
    curated_slugs = {g.slug for g in curated}
    live = {g.slug: g for p in sorted(GENERATED_DIR.glob("*.json")) if (g := _load(p))}
    samples = {g.slug: g for p in sorted(SAMPLES_DIR.glob("*.json")) if (g := _load(p))}
    generated = [g for slug, g in {**samples, **live}.items() if slug not in curated_slugs]
    generated.sort(key=lambda g: g.provenance.generated_at, reverse=True)
    return curated, generated
