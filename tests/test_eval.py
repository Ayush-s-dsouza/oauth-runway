"""Eval wiring checks that cost nothing: oracle and null through the
programmatic grader, and the runner end to end with generation and judge
stubbed (success, product failure, infra timeout, resume, harness gate)."""

import asyncio
import json
import shutil
from argparse import Namespace

import pytest

from app import store
from app.agent import AgentFailed
from app.schema import Guide, Provenance
from evals import grade, run
from tests.test_core import draft

CASES = {c["id"]: c for c in json.load(open("evals/cases.json", encoding="utf-8"))}


def test_oracle_curated_guides_score_perfectly_on_programmatic_metrics():
    for cid in ("github", "gmail", "slack", "shopify", "hubspot", "notion"):
        g = store.get(cid)
        exp = CASES[cid]["expected"]
        m = grade.programmatic(g, CASES[cid], exp["managed"])
        assert m["difficulty_dist"] == 0.0, cid
        assert m["managed_match"] == 1.0, cid
        assert m["grounded_rate"] == 1.0 and m["probed"] == 1.0, cid


def test_null_guide_scores_zero():
    d = draft(steps=[], evidence=[])
    g = Guide(**d.model_dump(), slug="x", provenance=Provenance(kind="agent", generated_at="2026-01-01T00:00:00+00:00"))
    m = grade.programmatic(g, CASES["gmail"], True)
    assert m["grounded_rate"] == 0.0 and m["supported_steps"] == 0.0 and m["probed"] == 0.0
    assert m["managed_match"] == 0.0  # guide says unknown, truth is known -> miss
    assert grade.managed_match(g, None) is None  # no ground truth -> n/a, not 0


class StubJudge:
    def __init__(self):
        self.usage = {"input_tokens": 10, "output_tokens": 5}

    def cost(self):
        return 0.0001

    async def fact_recall(self, md, facts):
        return 1.0, "stub"

    async def quote_support(self, evidence):
        return 1.0, "stub"

    async def edge_behaviour(self, md, rule):
        return 0.0, "stub"


@pytest.fixture
def flow(tmp_path, monkeypatch):
    f = tmp_path / "flow"
    f.mkdir()
    shutil.copy(run.FLOW / "_state.json", f / "_state.json")
    monkeypatch.setattr(run, "FLOW", f)
    for k in ("RESEARCH_MODEL", "SYNTHESIS_MODEL", "DATA_DIR"):
        monkeypatch.setenv(k, "unset")  # restored after the test; main_async overwrites them

    oracle = store.get("gmail")

    async def fake_generate(query, emit, slug=None):
        await emit({"type": "search", "text": f"{query} oauth"})
        if query == "Mercury":
            raise AgentFailed("research did not converge on either model")
        if query == "Xero":
            await asyncio.sleep(5)
        prov = oracle.provenance.model_copy(update={"kind": "agent", "served_models": ["claude-opus-5-5"], "est_cost_usd": 0.5})
        return oracle.model_copy(update={"slug": slug, "provenance": prov})

    async def no_composio(name):
        return {"available": False}

    import app.composio_lookup
    import app.pipeline
    import evals.judge

    monkeypatch.setattr(app.pipeline, "generate_guide", fake_generate)
    monkeypatch.setattr(app.composio_lookup, "composio_toolkit", no_composio)
    monkeypatch.setattr(evals.judge, "Judge", StubJudge)
    return f


def args(**kw):
    base = dict(variant="baseline", reps=1, cases="gmail,mercury,xero", timeout_s=1, concurrency=3, approve_harness=False)
    return Namespace(**{**base, **kw})


def test_runner_end_to_end(flow):
    with pytest.raises(SystemExit) as e:  # never approved -> refuses to run
        asyncio.run(run.main_async(args()))
    assert e.value.code == 2

    asyncio.run(run.main_async(args(approve_harness=True)))
    vdir = flow / "baseline"
    rows = {r["prompt_id"]: r for r in map(json.loads, (vdir / "results.jsonl").read_text().splitlines())}
    errors = [json.loads(l) for l in (vdir / "errors.jsonl").read_text().splitlines()]

    ok = rows["gmail"]
    assert ok["status"] == "ok" and ok["grade"]["fact_recall"] == 1.0 and ok["grade"]["difficulty_dist"] == 0.0
    assert ok["grade"]["managed_match"] == 1.0 and ok["grade"]["edge_pass"] is None
    assert (vdir / "traces" / "gmail_rep0.json").exists() and (vdir / "guides" / "gmail_rep0.json").exists()

    assert rows["mercury"]["status"] == "failed"  # product failure: scored as an outcome
    assert all(v is None for v in rows["mercury"]["grade"].values())
    assert "xero" not in rows and errors[0]["class"] == "timeout"  # infra failure: never scored

    summary = json.loads((vdir / "summary.json").read_text())
    assert summary["completed"] == 0.5 and summary["metrics"]["fact_recall"]["mean"] == 1.0

    # resume: approved harness, finished cases skipped, only the timed-out case is retried
    asyncio.run(run.main_async(args()))
    rows2 = (vdir / "results.jsonl").read_text().splitlines()
    assert len(rows2) == 2
    assert len((vdir / "errors.jsonl").read_text().splitlines()) == 2
