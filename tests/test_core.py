import asyncio

import pytest

from app import probes
from app.render_md import to_markdown
from app.schema import Guide, GuideDraft, Provenance, slugify
from app.validate import validate


def draft(**over) -> GuideDraft:
    base = {
        "app": {"name": "Acme", "developer_portal_url": "https://dev.acme.com"},
        "verdict": {"difficulty": "light_review", "summary": "s", "hands_on_effort": "1h",
                    "time_to_first_connection": "10 min", "approval_wait": "1-2 weeks"},
        "gates": [
            {"level": 1, "name": "Dev", "unlocks": "you", "time_to_reach": "10 min", "requires_review": False, "evidence_ids": ["e1"]},
            {"level": 2, "name": "Test", "unlocks": "testers", "time_to_reach": "10 min", "requires_review": False, "evidence_ids": ["e1"]},
            {"level": 3, "name": "Public", "unlocks": "anyone", "time_to_reach": "2 weeks", "requires_review": True, "evidence_ids": ["e2"]},
        ],
        "steps": [
            {"title": "Create app", "owner": "you", "gate": 1, "detail": "d", "evidence_ids": ["e1"]},
            {"title": "Probe", "owner": "agent", "gate": 1, "detail": "d", "evidence_ids": ["e3"]},
            {"title": "Guess", "owner": "you", "gate": 3, "detail": "d", "evidence_ids": []},
        ],
        "composio": {"note": "n"},
        "evidence": [
            {"id": "e1", "claim": "docs say so", "source_url": "https://docs.acme.com/oauth/", "status": "official_doc"},
            {"id": "e2", "claim": "recalled URL", "source_url": "https://docs.acme.com/never-fetched", "status": "official_doc"},
            {"id": "e3", "claim": "probe", "source_url": "https://auth.acme.com/authorize", "status": "probed"},
            {"id": "e4", "claim": "claims tested", "source_url": "https://docs.acme.com/oauth", "status": "tested"},
        ],
    }
    base.update(over)
    return GuideDraft.model_validate(base)


def test_validator_downgrades_unretrieved_and_fake_statuses():
    d, notes = validate(draft(), seen_urls={"https://www.docs.acme.com/oauth"}, probed_hosts={"auth.acme.com"})
    ev = {e.id: e for e in d.evidence}
    assert ev["e1"].status == "official_doc" and not ev["e1"].flags  # normalised URL match
    assert ev["e2"].status == "inferred" and ev["e2"].flags
    assert ev["e3"].status == "probed"
    assert ev["e4"].status == "inferred"  # agent may never claim hand-testing
    supports = [s.support for s in d.steps]
    assert supports == ["sourced", "verified", "unsupported"]
    assert any("gate 3" in n for n in notes)  # review verdict with no retrieved gate-3 source


def test_probed_without_probe_is_downgraded():
    d, _ = validate(draft(), seen_urls=set(), probed_hosts=set())
    assert {e.id: e for e in d.evidence}["e3"].status == "inferred"


def test_markdown_export_and_roundtrip():
    d, _ = validate(draft(), {"https://docs.acme.com/oauth"}, {"auth.acme.com"})
    g = Guide(**d.model_dump(), slug="acme", provenance=Provenance(kind="agent", generated_at="2026-10-28T00:00:00+00:00"))
    again = Guide.model_validate_json(g.model_dump_json())
    md = to_markdown(again)
    assert "# Acme" in md and "Gate 3" in md and "[Official docs]" in md


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/", "file:///etc/passwd", "http://example.com:8080/", "http://user:pw@example.com/",
])
def test_ssrf_guard_blocks(url):
    out = asyncio.run(probes.probe_url(url))
    assert "blocked" in out


def test_slugify():
    assert slugify("https://www.notion.so/integrations") == "notion"
    assert slugify("Google Workspace (Gmail)") == "google-workspace-gmail"
    assert slugify("xero.com") == "xero"


def test_unknown_finding_category_is_filed_as_other_but_evidence_stays_strict():
    from app.schema import Finding

    f = Finding(id="f1", category="rate_limit", claim="c", status="official_doc")
    assert f.category == "other"
    with pytest.raises(ValueError):
        Finding(id="f2", category="scope", claim="c", status="made_up_status")
