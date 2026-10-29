"""The Guide model: single source of truth for the agent's submit tool schema,
the evidence validator, the HTML page and the Markdown export.

A guide is organised around three "gates" -- the levels of audience an OAuth
app can serve -- because "how long does approval take?" almost never has one
answer: most providers let you connect your own account in minutes, and only
gate *public* use behind review.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

EvidenceStatus = Literal["tested", "probed", "official_doc", "community", "inferred"]
Difficulty = Literal["instant", "self_serve", "light_review", "heavy_review"]
Owner = Literal["agent", "you", "provider_wait"]
Support = Literal["verified", "sourced", "community", "unsupported"]

DIFFICULTY_LABEL = {
    "instant": "Instant",
    "self_serve": "Self-serve",
    "light_review": "Light review",
    "heavy_review": "Heavy review",
}
OWNER_LABEL = {"agent": "Agent can do", "you": "You do", "provider_wait": "Wait on provider"}
STATUS_LABEL = {
    "tested": "Tested by hand",
    "probed": "Probed live",
    "official_doc": "Official docs",
    "community": "Community source",
    "inferred": "Inferred / unverified",
}


class Evidence(BaseModel):
    id: str = Field(description="Short id like 'e1', referenced from evidence_ids elsewhere.")
    claim: str = Field(description="The specific fact this evidence supports, in one sentence.")
    source_url: Optional[str] = Field(
        default=None,
        description="Exact URL you actually opened, searched or probed in THIS research run. Null for 'inferred'.",
    )
    quote: Optional[str] = Field(
        default=None, description="Short verbatim excerpt (<= 300 chars) from the source supporting the claim."
    )
    status: EvidenceStatus = Field(
        description=(
            "official_doc = provider-owned docs/help pages; community = forums, blogs, Stack Overflow, "
            "third-party docs; probed = result of one of your probe_* tool calls; inferred = your own "
            "reasoning with no direct source. Never use 'tested' (reserved for human hand-testing)."
        )
    )
    # Filled by the validator, never by the model.
    flags: list[str] = Field(default_factory=list)


class KeyValue(BaseModel):
    label: str
    value: str


class AppInfo(BaseModel):
    name: str
    website: Optional[str] = None
    developer_portal_url: Optional[str] = Field(default=None, description="Where you create/manage the OAuth app.")
    oauth_docs_url: Optional[str] = None
    category: Optional[str] = None


class Verdict(BaseModel):
    difficulty: Difficulty = Field(
        description=(
            "instant = create app and go public with no review; self_serve = some account/config "
            "prerequisites but no human review for public use; light_review = a review/listing step "
            "typically days; heavy_review = verification/security assessment typically weeks+."
        )
    )
    summary: str = Field(description="One or two plain sentences: what makes this easy or hard.")
    hands_on_effort: str = Field(description="Total active work for the developer, e.g. '30-60 min' or '1-2 days'.")
    time_to_first_connection: str = Field(
        description="How soon the developer can connect their OWN account through their app, e.g. '15 min'."
    )
    approval_wait: str = Field(
        description="Calendar wait for provider approval before ANY user can connect, e.g. 'none', '1-2 weeks', '4-8 weeks (reported)'."
    )


class Gate(BaseModel):
    level: Literal[1, 2, 3] = Field(description="1 = only you (dev), 2 = invited/test users, 3 = anyone (production).")
    name: str = Field(description="Provider's own term where one exists, e.g. 'Testing mode', 'Development store', 'Unlisted app'.")
    unlocks: str
    limits: list[str] = Field(default_factory=list, description="Caps, token expiry, warning screens, install limits.")
    time_to_reach: str
    requires_review: bool
    evidence_ids: list[str] = Field(default_factory=list)


class Step(BaseModel):
    title: str
    owner: Owner = Field(
        description=(
            "agent = an automated agent could do this given access (draft text, call a provider "
            "management API, validate a URL); you = needs the developer's identity, legal/business "
            "decision, payment, signature or a UI-only action; provider_wait = waiting on the provider."
        )
    )
    gate: Literal[1, 2, 3] = Field(description="The gate this step is required for.")
    detail: str = Field(description="What to do, concretely. Plain text, 1-4 sentences.")
    url: Optional[str] = None
    values: list[KeyValue] = Field(default_factory=list, description="Exact values to enter (e.g. redirect URI, scopes).")
    est_time: Optional[str] = None
    gotchas: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    support: Optional[Support] = None  # validator-filled


class Requirement(BaseModel):
    item: str = Field(description="e.g. 'Privacy policy URL on a verified domain'.")
    gate: Literal[1, 2, 3]
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)
    support: Optional[Support] = None


class ScopeNote(BaseModel):
    scope: str
    tier: Literal["basic", "sensitive", "restricted", "unknown"]
    effect: str = Field(description="How requesting this scope changes setup or review.")
    evidence_ids: list[str] = Field(default_factory=list)
    support: Optional[Support] = None


class Blocker(BaseModel):
    symptom: str = Field(description="What the developer sees, e.g. an error message or rejection reason.")
    cause: str
    fix: str
    contact: Optional[str] = Field(default=None, description="Support channel / form / email if escalation is needed.")
    evidence_ids: list[str] = Field(default_factory=list)
    support: Optional[Support] = None


class ComposioInfo(BaseModel):
    toolkit_slug: Optional[str] = None
    managed_oauth_available: Optional[bool] = Field(
        default=None, description="True if Composio offers a managed OAuth app for this toolkit (from composio_toolkit)."
    )
    default_scopes: list[str] = Field(default_factory=list)
    note: str = Field(
        description="When to start on managed auth vs. when you need your own app, specific to this provider."
    )


_FINDING_CATEGORIES = ("gate1", "gate2", "gate3", "step", "requirement", "scope", "blocker", "app_info", "other")


class Finding(BaseModel):
    """One cited fact gathered during research. This is the hand-off unit
    between the (cheap) research phase and the (strong) synthesis phase --
    pure extraction, no gate structure or verdict judgment."""

    id: str = Field(description="Short id like 'f1'. Reused as the evidence id in the final guide if this finding is used.")
    category: Literal["gate1", "gate2", "gate3", "step", "requirement", "scope", "blocker", "app_info", "other"] = Field(
        description="Which part of the eventual guide this is likely to support."
    )
    claim: str
    source_url: Optional[str] = None
    quote: Optional[str] = Field(default=None, description="Short verbatim excerpt (<= 300 chars) supporting the claim.")
    status: EvidenceStatus

    @field_validator("category", mode="before")
    @classmethod
    def _unknown_category_is_other(cls, v):
        # The category is only a routing hint for the writer. A cheap researcher
        # occasionally invents a label ("rate_limit"); the eval saw that abort a
        # whole run after 3 retries. File it under "other" instead -- the
        # evidence fields (status, url, quote) stay strictly validated.
        return v if v in _FINDING_CATEGORIES else "other"


class ResearchFindings(BaseModel):
    """What the research phase hands to the synthesis phase. No gates, no
    verdict, no step ownership -- just what was found and where it came from."""

    app_name_guess: str = Field(description="Which real app/product you believe this is.")
    developer_portal_url: Optional[str] = None
    oauth_docs_url: Optional[str] = None
    composio: ComposioInfo
    findings: list[Finding]
    open_questions: list[str] = Field(default_factory=list, description="Things you could not find or were unsure about.")


class GuideDraft(BaseModel):
    """What the synthesis phase submits."""

    app: AppInfo
    verdict: Verdict
    gates: list[Gate] = Field(description="Exactly three gates, levels 1, 2, 3, in order.")
    steps: list[Step] = Field(description="6-14 ordered steps from zero to production.")
    requirements: list[Requirement] = Field(default_factory=list)
    scopes: list[ScopeNote] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    composio: ComposioInfo
    evidence: list[Evidence]
    open_questions: list[str] = Field(
        default_factory=list, description="Things you could not verify that could change the plan."
    )


class Provenance(BaseModel):
    kind: Literal["verified", "agent"]
    generated_at: str
    model: Optional[str] = None  # display string, e.g. "claude-haiku-5-5 -> claude-opus-5-5"; curated guides use a note here instead
    research_model: Optional[str] = None
    synthesis_model: Optional[str] = None
    escalated: bool = False
    escalation_reason: Optional[str] = None
    verified_on: Optional[str] = None
    verification_notes: list[str] = Field(default_factory=list)
    sources_consulted: int = 0
    probes_run: int = 0
    research_seconds: Optional[int] = None
    est_cost_usd: Optional[float] = None
    cost_breakdown: dict[str, float] = Field(default_factory=dict)  # {"claude-haiku-5-5": 0.01, "claude-opus-5-5": 0.08}
    usage: dict[str, dict[str, int]] = Field(default_factory=dict)  # per model: in/out/cache_read/cache_write/search
    served_models: list[str] = Field(default_factory=list)


class Guide(GuideDraft):
    slug: str
    provenance: Provenance


def slugify(name: str) -> str:
    s = re.sub(r"^https?://", "", name.strip().lower())
    s = re.sub(r"^www\.", "", s)
    s = s.split("/")[0] if "." in s.split("/")[0] else s
    s = re.sub(r"\.(com|io|so|dev|ai|co|app|net|org)$", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "app"
