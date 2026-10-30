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


def test_first_party_community_sources_are_upgraded_only_when_retrieved():
    from app.validate import is_first_party

    doms = {"teamleader.eu"}
    assert is_first_party("https://github.com/teamleadercrm/api/blob/master/src/authentication.apib", doms)
    assert is_first_party("https://support.focus.teamleader.eu/hc/en-150/articles/1", doms)
    assert not is_first_party("https://github.com/MCore-Services-bv/teamleader-sdk", doms)  # third-party SDK
    assert not is_first_party("https://developers.apideck.com/apis/crm/teamleader", doms)
    assert not is_first_party("https://github.com/x", {"x.io"})  # short brands never match an org
    # provider-hosted forums are still user posts
    assert not is_first_party("https://community.docusign.com/go-live-70/thread-1", {"docusign.com"})
    assert not is_first_party("https://devcommunity.pipedrive.com/t/app-review/7555", {"pipedrive.com"})
    assert not is_first_party("https://learn.microsoft.com/en-us/answers/questions/1/x", {"microsoft.com"})
    assert not is_first_party("https://www.servicenow.com/community/developer-forum/x", {"servicenow.com"})
    assert not is_first_party("https://github.com/teamleadercrm/api/issues/12", doms)
    # shared doc hosts are identified by full host, never by domain
    assert is_first_party("https://pipedrive.readme.io/docs/oauth", {"pipedrive.readme.io"})
    assert not is_first_party("https://othervendor.readme.io/docs", {"pipedrive.readme.io"})
    assert is_first_party("https://github.com/pipedrive/client-nodejs", {"pipedrive.readme.io"})

    urls = {"own": "https://github.com/acmeinc/docs/auth.md", "sdk": "https://github.com/someone/acme-sdk",
            "unseen": "https://github.com/acmeinc/other"}
    d = draft(evidence=[{"id": k, "claim": k, "source_url": u, "status": "community"} for k, u in urls.items()],
              steps=[], gates=[])
    d, notes = validate(d, seen_urls={urls["own"], urls["sdk"]}, probed_hosts=set())
    ev = {e.id: e for e in d.evidence}
    assert ev["own"].status == "official_doc" and not ev["own"].flags
    assert ev["sdk"].status == "community"
    assert ev["unseen"].status == "inferred"  # never upgrade something that wasn't retrieved
    assert any("upgraded" in n for n in notes)


def test_composio_facts_come_from_the_lookup_not_the_writer():
    from app.validate import lock_composio

    # Withings, 2026-10-30: lookup found nothing, writer still filled in a slug and the provider's own scopes
    d = draft(composio={"toolkit_slug": "withings", "managed_oauth_available": False,
                        "default_scopes": ["user.info", "user.metrics"], "note": "n"})
    notes = lock_composio(d, [{"available": False, "reason": "No Composio toolkit found for 'Withings' (tried [...])."}])
    assert (d.composio.toolkit_slug, d.composio.managed_oauth_available, d.composio.default_scopes) == (None, False, [])
    assert notes and d.composio.note == "n"  # the prose stays the writer's

    d = draft(composio={"toolkit_slug": None, "managed_oauth_available": False, "note": "n"})
    lock_composio(d, [{"available": True, "toolkit_slug": "acme", "managed_oauth_available": True,
                       "auth_configs": [{"default_scopes": []}, {"default_scopes": ["read", "write"]}]}])
    assert (d.composio.toolkit_slug, d.composio.managed_oauth_available, d.composio.default_scopes) == ("acme", True, ["read", "write"])

    d = draft(composio={"toolkit_slug": "acme", "managed_oauth_available": True, "note": "n"})
    lock_composio(d, [{"available": False, "reason": "COMPOSIO_API_KEY not configured on this server"}])
    assert d.composio.managed_oauth_available is None  # no key: unknown, not "no managed app"


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
