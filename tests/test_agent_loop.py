"""The two-phase agent against a scripted fake Claude client.

Normal path: research on the cheap model (pause_turn resume, server-tool URL
capture, probe dispatch, invalid compile_findings -> error -> fixed), then
synthesis on the strong model with no tools. Escalation path: research
stalls on the cheap model and is retried on the strong one."""

import asyncio
from types import SimpleNamespace as NS

from app import agent
from tests.test_core import draft


import pytest


@pytest.fixture(autouse=True)
def harness_switches_off(monkeypatch):
    """Tests opt in to each steering mechanism explicitly, independent of the shipped defaults."""
    monkeypatch.setattr(agent, "COVERAGE_GATE", False)
    monkeypatch.setattr(agent, "RESEARCH_PLAN", False)
    monkeypatch.setattr(agent, "EVIDENCE_LOCK", "")


class Block(NS):
    def model_dump(self):
        return dict(vars(self))


def usage():
    return NS(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=0,
              server_tool_use=NS(web_search_requests=1))


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for b in self.msg.content:
                yield NS(type="content_block_stop", content_block=b)
        return gen()

    async def get_final_message(self):
        return self.msg


def findings() -> dict:
    return {
        "app_name_guess": "Acme",
        "developer_portal_url": "https://dev.acme.com",
        "composio": {"note": "n"},
        "findings": [
            {"id": "f1", "category": "gate1", "claim": "docs say so", "source_url": "https://docs.acme.com/oauth", "status": "official_doc"},
        ],
    }


def msg(stop, *blocks):
    return NS(stop_reason=stop, usage=usage(), content=list(blocks))


def run_with(script, monkeypatch):
    calls = []

    def fake_stream(**kw):
        calls.append({**kw, "messages": list(kw["messages"])})  # the loop mutates the list afterwards
        return FakeStream(script[len(calls) - 1])

    async def fake_probe(url):
        return {"probe": "url", "requested_url": url, "status": 200, "final_url": url}

    monkeypatch.setattr(agent.probes, "probe_url", fake_probe)
    events = []

    async def emit(e):
        events.append(e)

    r = agent.Research("Acme", emit)
    r.client = NS(beta=NS(messages=NS(stream=fake_stream)))
    return r, asyncio.run(r.run()), calls, events


def test_two_phase_happy_path(monkeypatch):
    good_guide = draft().model_dump()
    script = [
        # --- research (cheap model)
        msg("pause_turn",
            Block(type="server_tool_use", id="s1", name="web_search", input={"query": "acme oauth"}),
            Block(type="web_search_tool_result", tool_use_id="s1",
                  content=[{"type": "web_search_result", "url": "https://docs.acme.com/oauth", "title": "OAuth"}])),
        msg("tool_use", Block(type="tool_use", id="t1", name="probe_url", input={"url": "https://dev.acme.com"})),
        msg("tool_use", Block(type="tool_use", id="t2", name="compile_findings", input={"findings": "not a list"})),
        msg("tool_use", Block(type="tool_use", id="t3", name="compile_findings", input=findings())),
        # --- synthesis (strong model)
        msg("tool_use", Block(type="tool_use", id="t4", name="submit_guide", input=good_guide)),
    ]
    r, result, calls, events = run_with(script, monkeypatch)

    assert result.app.name == "Acme"
    assert [c["model"] for c in calls] == [agent.RESEARCH_MODEL] * 4 + [agent.SYNTHESIS_MODEL]
    assert not r.escalated

    # research has the tools; synthesis has only submit_guide and works from the findings
    research_tools = {t["name"] for t in calls[0]["tools"]}
    assert {"web_search", "web_fetch", "probe_url", "compile_findings"} <= research_tools
    assert "submit_guide" not in research_tools
    assert [t["name"] for t in calls[4]["tools"]] == ["submit_guide"]
    assert "https://docs.acme.com/oauth" in calls[4]["messages"][0]["content"]

    # the invalid compile_findings was returned to the model as an error
    assert calls[3]["messages"][-1]["content"][0]["is_error"] is True

    # server-side fallback is only requested for models that support it
    assert "fallbacks" not in calls[0]
    assert calls[4]["fallbacks"] == "default"

    assert "https://docs.acme.com/oauth" in r.seen_urls and "dev.acme.com" in r.probed_hosts
    breakdown = r.cost_breakdown()
    assert set(breakdown) == {agent.RESEARCH_MODEL, agent.SYNTHESIS_MODEL}
    # same token counts per call, so the cheap model's per-call cost must be far lower
    assert breakdown[agent.RESEARCH_MODEL] / 4 < breakdown[agent.SYNTHESIS_MODEL]


def test_research_stall_escalates_to_strong_model(monkeypatch):
    monkeypatch.setattr(agent, "RESEARCH_MAX_TURNS", 2)
    chatter = lambda: msg("end_turn", Block(type="text", text="still thinking"))  # never calls compile_findings
    script = [
        chatter(), chatter(),  # cheap model stalls
        msg("tool_use", Block(type="tool_use", id="t1", name="compile_findings", input=findings())),  # strong model research
        msg("tool_use", Block(type="tool_use", id="t2", name="submit_guide", input=draft().model_dump())),
    ]
    r, result, calls, events = run_with(script, monkeypatch)

    assert result.app.name == "Acme"
    assert [c["model"] for c in calls] == [agent.RESEARCH_MODEL] * 2 + [agent.SYNTHESIS_MODEL] * 2
    assert r.escalated and "did not converge" in r.escalation_reason
    assert any(e["type"] == "escalate" for e in events)


def test_tool_schemas_match_function_signatures():
    """Every client tool's declared parameters must be accepted by the function
    that runs it. (A mismatch here crashed the first live run.)"""
    import inspect

    from app import composio_lookup, probes

    fns = {"probe_url": probes.probe_url, "probe_oauth_authorize": probes.probe_oauth_authorize,
           "probe_oidc_discovery": probes.probe_oidc_discovery, "composio_toolkit": composio_lookup.composio_toolkit}
    for tool in agent.RESEARCH_TOOLS:
        if tool["name"] not in fns:
            continue
        params = set(inspect.signature(fns[tool["name"]]).parameters)
        declared = set(tool["input_schema"]["properties"])
        if tool["name"] == "composio_toolkit":  # dispatched as composio_toolkit(inp["name"])
            continue
        assert declared <= params, f"{tool['name']}: schema {declared} vs function {params}"


def test_bad_tool_arguments_become_an_error_result_not_a_crash():
    async def emit(e):
        pass

    r = agent.Research("Acme", emit)
    out = asyncio.run(r._run_tool("probe_url", {"link": "https://example.com"}))
    assert "error" in out


def test_coverage_gate_sends_thin_research_back(monkeypatch):
    """With the gate on, findings built from search snippets alone are bounced
    with a specific list of what to research; after real page reads they pass."""
    monkeypatch.setattr(agent, "COVERAGE_GATE", True)
    monkeypatch.setattr(agent, "MIN_PAGES", 2)
    page = "https://docs.acme.com/oauth"

    def full_findings():
        f = findings()
        f["findings"] = [{"id": f"f{i}", "category": c, "claim": f"{c} fact", "source_url": page, "status": "official_doc"}
                         for i, c in enumerate(["gate2", "gate3", "scope", "requirement"])]
        return f

    script = [
        msg("tool_use", Block(type="tool_use", id="t1", name="compile_findings", input=full_findings())),  # nothing fetched yet
        msg("tool_use",
            Block(type="server_tool_use", id="s1", name="web_fetch", input={"url": page}),
            Block(type="server_tool_use", id="s2", name="web_fetch", input={"url": "https://docs.acme.com/review"}),
            Block(type="tool_use", id="t2", name="compile_findings", input=full_findings())),
        msg("tool_use", Block(type="tool_use", id="t3", name="submit_guide", input=draft().model_dump())),
    ]
    r, result, calls, events = run_with(script, monkeypatch)
    bounce = calls[1]["messages"][-1]["content"][0]
    assert bounce["is_error"] and "You read 0 pages" in bounce["content"] and "gate2" in bounce["content"]
    assert r.coverage_bounces == 1 and result.app.name == "Acme"
    assert any(e["type"] == "warning" and "sent back" in e["text"] for e in events)


def test_coverage_gate_gives_up_after_two_bounces(monkeypatch):
    monkeypatch.setattr(agent, "COVERAGE_GATE", True)
    thin = lambda i: msg("tool_use", Block(type="tool_use", id=f"t{i}", name="compile_findings", input=findings()))
    script = [thin(1), thin(2), thin(3), msg("tool_use", Block(type="tool_use", id="t9", name="submit_guide", input=draft().model_dump()))]
    r, result, calls, events = run_with(script, monkeypatch)
    assert r.coverage_bounces == 2 and len(calls) == 4  # accepted on the third try rather than looping forever


def test_evidence_lock_drops_invented_evidence_and_restores_quotes(monkeypatch):
    monkeypatch.setattr(agent, "EVIDENCE_LOCK", "1")
    f = findings()
    f["findings"][0]["quote"] = "the real quote"
    g = draft().model_dump()
    g["evidence"] = [
        {"id": "f1", "claim": "reworded", "source_url": "https://docs.acme.com/oauth", "quote": "an edited quote", "status": "official_doc"},
        {"id": "x9", "claim": "invented", "source_url": "https://docs.acme.com/oauth", "quote": "made up", "status": "official_doc"},
    ]
    g["steps"][0]["evidence_ids"] = ["f1", "x9"]
    script = [
        msg("tool_use", Block(type="tool_use", id="t1", name="compile_findings", input=f)),
        msg("tool_use", Block(type="tool_use", id="t2", name="submit_guide", input=g)),
    ]
    r, result, calls, events = run_with(script, monkeypatch)
    assert [e.id for e in result.evidence] == ["f1"]
    assert result.evidence[0].quote == "the real quote" and result.evidence[0].claim == "docs say so"
    assert result.steps[0].evidence_ids == ["f1"]
    # x9 plus e1/e2/e3, which the shared draft() fixture cites in its gates and steps but no finding backs
    assert r.lock_stats == {"invented": 4, "altered_quotes": 1}
    assert all(i == "f1" for gate in result.gates for i in gate.evidence_ids)


def test_research_plan_is_in_the_prompt_and_gate_counts_searches(monkeypatch):
    monkeypatch.setattr(agent, "RESEARCH_PLAN", True)
    monkeypatch.setattr(agent, "COVERAGE_GATE", True)
    monkeypatch.setattr(agent, "MIN_PAGES", 0)
    f = findings()
    f["findings"] = [{"id": f"f{i}", "category": c, "claim": c, "source_url": "https://docs.acme.com/oauth", "status": "probed"}
                     for i, c in enumerate(["gate2", "gate3", "scope", "requirement"])]
    script = [
        msg("tool_use", Block(type="tool_use", id="t1", name="compile_findings", input=f)),  # 0 searches -> bounced
        msg("tool_use", *[Block(type="server_tool_use", id=f"s{i}", name="web_search", input={"query": f"q{i}"}) for i in range(4)],
            Block(type="tool_use", id="t2", name="compile_findings", input=f)),
        msg("tool_use", Block(type="tool_use", id="t3", name="submit_guide", input=draft().model_dump())),
    ]
    r, result, calls, events = run_with(script, monkeypatch)
    assert "changelog" in calls[0]["messages"][0]["content"] and "Acme" in calls[0]["messages"][0]["content"]
    assert "You ran 0 searches" in calls[1]["messages"][-1]["content"][0]["content"]
    assert r.coverage_bounces == 1


def test_quote_lock_keeps_writer_wording_but_not_quotes_or_invented_ids(monkeypatch):
    monkeypatch.setattr(agent, "EVIDENCE_LOCK", "quote")
    f = findings()
    f["findings"][0]["quote"] = "the real quote"
    g = draft().model_dump()
    g["evidence"] = [
        {"id": "f1", "claim": "tightened wording", "source_url": "https://evil.example", "quote": "an edited quote", "status": "tested"},
        {"id": "x9", "claim": "invented", "source_url": "https://docs.acme.com/oauth", "quote": "made up", "status": "official_doc"},
    ]
    script = [
        msg("tool_use", Block(type="tool_use", id="t1", name="compile_findings", input=f)),
        msg("tool_use", Block(type="tool_use", id="t2", name="submit_guide", input=g)),
    ]
    r, result, calls, events = run_with(script, monkeypatch)
    (e,) = result.evidence
    assert e.claim == "tightened wording"  # writer may reword
    assert e.quote == "the real quote" and e.source_url == "https://docs.acme.com/oauth" and e.status == "official_doc"
