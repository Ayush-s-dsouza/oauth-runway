"""Programmatic (free, deterministic) metrics for one generated guide.

Every metric is a number in [0, 1] (difficulty_dist: 0-3, lower is better),
or None when it doesn't apply to the case -- never a 0 standing in for "n/a".
"""

from __future__ import annotations

from app.schema import Guide

TIER = {"instant": 0, "self_serve": 1, "light_review": 2, "heavy_review": 3}


def grounded_rate(g: Guide) -> float:
    """Share of evidence items the validator left untouched (cited URL was
    actually retrieved in-run; probe claims had a matching probe)."""
    if not g.evidence:
        return 0.0
    return sum(1 for e in g.evidence if not e.flags) / len(g.evidence)


def supported_steps(g: Guide) -> float:
    """Share of steps backed by at least one retrieved source."""
    if not g.steps:
        return 0.0
    return sum(1 for s in g.steps if s.support and s.support != "unsupported") / len(g.steps)


def probed(g: Guide) -> float:
    """Did the harness actually touch live provider infrastructure?"""
    return 1.0 if g.provenance.probes_run >= 1 else 0.0


def managed_match(g: Guide, truth: bool | None) -> float | None:
    """Does the guide's 'Composio managed OAuth available' match ground truth?
    An unknown (None) answer counts as a miss when the truth is known."""
    if truth is None:
        return None
    return 1.0 if g.composio.managed_oauth_available is truth else 0.0


def difficulty_dist(g: Guide, expected: str | None) -> float | None:
    """Tier distance from the reference verdict (0 = same tier)."""
    if expected is None:
        return None
    return float(abs(TIER[g.verdict.difficulty] - TIER[expected]))


def programmatic(g: Guide, case: dict, managed_truth: bool | None) -> dict[str, float | None]:
    exp = case.get("expected", {})
    return {
        "grounded_rate": round(grounded_rate(g), 3),
        "supported_steps": round(supported_steps(g), 3),
        "probed": probed(g),
        "managed_match": managed_match(g, managed_truth),
        "difficulty_dist": difficulty_dist(g, exp.get("difficulty")),
    }
