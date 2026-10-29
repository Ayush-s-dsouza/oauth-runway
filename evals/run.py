"""Eval runner for the guide-generation flow.

    python -m evals.run --variant baseline --approve-harness   # first run only: you approve the harness
    python -m evals.run --variant baseline                      # resumes; skips (case, rep) already scored
    python -m evals.run --variant v1 --cases gmail,slack --reps 2

Variants differ only in model routing (research -> synthesis):
    baseline  claude-haiku-5-5 -> claude-opus-5-5   (the shipped default)
    v1        claude-opus-5-5  -> claude-opus-5-5   (what does cheap research cost us?)
    v2        claude-haiku-5-5 -> claude-haiku-5-5  (does synthesis need the strong model?)

Writes .claude/hillclimb/oauth-guide/<variant>/{results.jsonl, errors.jsonl, traces/, guides/, summary.json}.
Infra failures (timeouts, API errors, model substitution, grader crashes) go to errors.jsonl and are
never scored. The product failing to produce a guide (stall, refusal) is a real outcome: it is
written to results.jsonl with status "failed" and counted in the completion rate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOW = ROOT / ".claude" / "hillclimb" / "oauth-guide"
OFF = {"COVERAGE_GATE": "0", "RESEARCH_PLAN": "0", "EVIDENCE_LOCK": "0"}
FULL = {"COVERAGE_GATE": "1", "RESEARCH_PLAN": "1", "EVIDENCE_LOCK": "1"}
VARIANTS = {  # (research model, synthesis model, extra env) -- every switch pinned so old variants stay reproducible
    "baseline": ("claude-haiku-5-5", "claude-opus-5-5", OFF),
    "v1": ("claude-opus-5-5", "claude-opus-5-5", OFF),
    "v2": ("claude-haiku-5-5", "claude-haiku-5-5", OFF),
    "v3": ("claude-haiku-5-5", "claude-opus-5-5", {**OFF, "COVERAGE_GATE": "1"}),
    "v4": ("claude-haiku-5-5", "claude-haiku-5-5", {**OFF, "COVERAGE_GATE": "1"}),
    "v5": ("claude-haiku-5-5", "claude-haiku-5-5", FULL),
    "v6": ("claude-haiku-5-5", "claude-opus-5-5", FULL),
    "v7": ("claude-haiku-5-5", "claude-opus-5-5", {**FULL, "EVIDENCE_LOCK": "quote"}),
    "v8": ("claude-haiku-5-5", "claude-sonnet-5-5", {**FULL, "EVIDENCE_LOCK": "quote"}),
}
CHANGE_MD = {
    "v1": "Research on claude-opus-5-5 instead of Haiku (synthesis unchanged): measures what routing research to the cheap model costs in quality.\n",
    "v2": "Synthesis on claude-haiku-5-5 instead of Opus (research unchanged): measures whether the inferential step actually needs the strong model.\n",
    "v3": "Baseline routing plus the research coverage gate (>=6 pages read; test-user limits, public review, scopes and requirements each covered by a fetched provider page): can harness steering close Haiku research's depth gap?\n",
    "v4": "Haiku for both phases plus the coverage gate: the all-cheap configuration, steered.\n",
    "v5": "v4 plus a code-supplied research plan (5 searches incl. the developer changelog; gate requires >=4 searches) and the evidence lock (evidence rebuilt verbatim from findings by id). All-Haiku.\n",
    "v6": "Same harness as v5, with Opus writing: does the strong writer still add anything once the harness is steering?\n",
    "v7": "v6 with a quote-only evidence lock: the writer may reword a claim (tighten it to its quote) but cannot invent evidence or alter quotes/URLs/status.\n",
    "v8": "v7 with Sonnet 5.5 writing instead of Opus: writing is ~85% of v7's cost, so can the mid-tier model hold judgement and faithfulness? Note: the default judge is also Sonnet, so v7/v8 are cross-checked with an Opus judge (evals/regrade.py).\n",
}


def harness_sha(paths: list[str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(set(paths) | {"evals/run.py"}):
        p = ROOT / rel
        h.update(rel.encode())
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()


def check_harness(state: dict, approve: bool) -> None:
    sha = harness_sha(state["harness_paths"])
    rec = FLOW / "harness.sha"
    if approve:
        rec.write_text(sha)
        print(f"harness approved ({sha[:12]})")
        return
    if not rec.exists() or rec.read_text().strip() != sha:
        print("Harness files changed since they were last approved (or never approved):\n  "
              + "\n  ".join(state["harness_paths"])
              + "\nReview the changes, then re-run once with --approve-harness. Exiting.", file=sys.stderr)
        sys.exit(2)


def build_trace(events: list[dict], query: str, research_model: str, synthesis_model: str, final_md: str | None) -> list[dict]:
    turns = [{"role": "system", "content": f"research: {research_model} | synthesis: {synthesis_model} | prompts: app/prompts.py"},
             {"role": "user", "content": query}]
    pending_thinking = None
    for ev in events:
        t, text = ev.get("type"), ev.get("text", "")
        if t == "thinking":
            pending_thinking = text
            continue
        turn = None
        if t in ("search", "fetch", "probe", "composio"):
            name = {"search": "web_search", "fetch": "web_fetch", "probe": "probe", "composio": "composio_toolkit"}[t]
            turn = {"role": "tool_call", "name": name, "content": text}
        elif t in ("results", "probe_result"):
            turn = {"role": "tool_result", "content": text}
        elif t == "findings":
            turn = {"role": "tool_call", "name": "compile_findings", "content": json.dumps(ev.get("data"), indent=2)}
        elif t in ("note", "escalate", "warning", "validating", "drafting", "error"):
            turn = {"role": "assistant", "content": f"[{t}] {text}"}
        if turn:
            if pending_thinking:
                turn["thinking"], pending_thinking = pending_thinking, None
            turns.append(turn)
    if final_md:
        turns.append({"role": "assistant", "content": final_md})
    return turns


def ci95(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    mean = sum(xs) / n
    if n < 2:
        return mean, float("nan")
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))
    return mean, 1.96 * sd / math.sqrt(n)


async def main_async(args) -> int:
    research_model, synthesis_model, extra_env = VARIANTS[args.variant]
    vdir = FLOW / args.variant
    # Point the app at this variant's models and a private data dir BEFORE importing it.
    os.environ["RESEARCH_MODEL"], os.environ["SYNTHESIS_MODEL"] = research_model, synthesis_model
    os.environ["DATA_DIR"] = str(vdir / "_appdata")
    os.environ.update(extra_env)

    import anthropic
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    from app.agent import AgentFailed
    from app.composio_lookup import composio_toolkit
    from app.pipeline import generate_guide
    from app.render_md import to_markdown
    from app.schema import Guide
    from evals.grade import programmatic
    from evals.judge import JUDGE_MODEL, Judge

    state = json.loads((FLOW / "_state.json").read_text(encoding="utf-8"))
    check_harness(state, args.approve_harness)

    for d in ("traces", "guides"):
        (vdir / d).mkdir(parents=True, exist_ok=True)
    if args.variant in CHANGE_MD and not (vdir / "change.md").exists():
        (vdir / "change.md").write_text(CHANGE_MD[args.variant], encoding="utf-8")

    cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    results_path, errors_path = vdir / "results.jsonl", vdir / "errors.jsonl"
    done = set()
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["prompt_id"], r["rep"]))
    todo = [(c, rep) for c in cases for rep in range(args.reps) if (c["id"], rep) not in done]

    # Ground truth for managed_match comes from the live Composio API, falling back to cases.json.
    truth_path = FLOW / "managed_truth.json"
    truth = json.loads(truth_path.read_text()) if truth_path.exists() else {}
    for c in cases:
        if c["id"] not in truth:
            info = await composio_toolkit(c["query"])
            truth[c["id"]] = {"managed": info["managed_oauth_available"], "source": "composio_api"} if info.get("available") \
                else {"managed": c["expected"].get("managed"), "source": "cases.json"}
    truth_path.write_text(json.dumps(truth, indent=1))

    print(f"{args.variant}: {research_model} -> {synthesis_model} | {len(todo)} to run "
          f"({len(cases) * args.reps - len(todo)} already done) | judge {JUDGE_MODEL} | concurrency {args.concurrency}")
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(args.concurrency)
    started = time.monotonic()
    finished = 0

    async def append(path: Path, row: dict):
        async with lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

    async def run_case(case: dict, rep: int):
        nonlocal finished
        key = f"{case['id']}_rep{rep}"
        guide_file = vdir / "guides" / f"{key}.json"
        events: list[dict] = []

        async def emit(ev):
            events.append(ev)

        async with sem:
            t0 = time.monotonic()
            err_base = {"prompt_id": case["id"], "rep": rep, "variant": args.variant}
            guide = None
            if guide_file.exists():  # generated earlier, grading failed: re-grade without paying for generation again
                guide = Guide.model_validate_json(guide_file.read_text(encoding="utf-8"))
            else:
                for attempt in range(3):
                    try:
                        guide = await asyncio.wait_for(generate_guide(case["query"], emit, slug=key), timeout=args.timeout_s)
                        break
                    except asyncio.TimeoutError:
                        await append(errors_path, {**err_base, "class": "timeout", "attempt": attempt, "detail": f">{args.timeout_s}s"})
                        break
                    except AgentFailed as e:  # the product failed to produce a guide: a graded outcome, not infra
                        await append(results_path, {
                            "prompt_id": case["id"], "prompt": case["query"], "tags": case["tags"], "rep": rep,
                            "status": "failed", "stop_reason": "refusal" if "declined" in str(e) else "agent_failed",
                            "grade": {m["id"]: None for m in state["metrics"]}, "explanation": {"failure": str(e)},
                            "model": f"{research_model}->{synthesis_model}", "latency_s": round(time.monotonic() - t0, 1),
                        })
                        (vdir / "traces" / f"{key}.json").write_text(json.dumps(build_trace(events, case["query"], research_model, synthesis_model, None), indent=1), encoding="utf-8")
                        finished += 1
                        return
                    except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
                        await append(errors_path, {**err_base, "class": "api_error", "attempt": attempt, "detail": f"{type(e).__name__}: {str(e)[:200]}"})
                        await asyncio.sleep((2 ** attempt) * 5 + random.uniform(0, 5))  # jittered backoff
                    except anthropic.APIStatusError as e:
                        await append(errors_path, {**err_base, "class": "api_error", "attempt": attempt, "detail": f"{e.status_code}: {str(e)[:200]}"})
                        break
                    except Exception as e:  # a bug in the app or harness: log it, keep the other cases running
                        await append(errors_path, {**err_base, "class": "harness_error", "attempt": attempt, "detail": f"{type(e).__name__}: {str(e)[:300]}"})
                        break
                if guide is None:
                    finished += 1
                    return
                guide_file.write_text(guide.model_dump_json(indent=1), encoding="utf-8")

            latency = round(time.monotonic() - t0, 1)
            allowed = {research_model, synthesis_model}
            bad = [s for s in guide.provenance.served_models if not any(s.startswith(a) for a in allowed)]
            if bad:
                await append(errors_path, {**err_base, "class": "served_model_mismatch", "detail": f"served {bad}, asked for {sorted(allowed)}"})
                finished += 1
                return

            md = to_markdown(guide)
            grade = programmatic(guide, case, truth[case["id"]]["managed"])
            expl = {}
            judge = Judge()
            try:
                facts = case["expected"].get("facts")
                grade["fact_recall"], expl["fact_recall"] = (await judge.fact_recall(md, facts)) if facts else (None, "n/a: no answer key")
                grade["quote_support"], expl["quote_support"] = await judge.quote_support([e.model_dump() for e in guide.evidence])
                rule = case["expected"].get("behaviour")
                grade["edge_pass"], expl["edge_pass"] = (await judge.edge_behaviour(md, rule)) if rule else (None, "n/a")
            except Exception as e:  # grader crash: infra, not a model failure; the cached guide gets re-graded next run
                await append(errors_path, {**err_base, "class": "grader_error", "detail": f"{type(e).__name__}: {str(e)[:200]}"})
                finished += 1
                return

            prov = guide.provenance
            row = {
                "prompt_id": case["id"], "prompt": case["query"], "tags": case["tags"], "rep": rep,
                "status": "ok", "stop_reason": "end_turn",
                "grade": grade, "explanation": expl,
                "model": f"{research_model}->{synthesis_model}",
                "cost_usd": prov.est_cost_usd, "latency_s": prov.research_seconds or latency,  # generation time, not grading
                "sources": prov.sources_consulted, "probes": prov.probes_run, "escalated": int(prov.escalated),
                "judge_model": JUDGE_MODEL, "judge_usage": judge.usage, "judge_cost_usd": judge.cost(),
                "meta": {"served_models": prov.served_models, "cost_breakdown": prov.cost_breakdown, "usage": prov.usage,
                         "managed_truth_source": truth[case["id"]]["source"], "difficulty": guide.verdict.difficulty,
                         "guide": str(guide_file.relative_to(FLOW))},
            }
            if events or not (vdir / "traces" / f"{key}.json").exists():
                (vdir / "traces" / f"{key}.json").write_text(json.dumps(build_trace(events, case["query"], research_model, synthesis_model, md), indent=1), encoding="utf-8")
            await append(results_path, row)
            finished += 1
            el = time.monotonic() - started
            print(f"  {finished}/{len(todo)} {key}: fact_recall={grade.get('fact_recall')} quote_support={grade.get('quote_support')} "
                  f"grounded={grade['grounded_rate']} ${prov.est_cost_usd} | ~{int(el / finished * (len(todo) - finished))}s left", flush=True)

    await asyncio.gather(*(run_case(c, r) for c, r in todo))

    # Summary over everything on disk for this variant (not just this invocation).
    rows = [json.loads(l) for l in results_path.read_text(encoding="utf-8").splitlines()] if results_path.exists() else []
    n_err = len(errors_path.read_text(encoding="utf-8").splitlines()) if errors_path.exists() else 0
    ok = [r for r in rows if r["status"] == "ok"]
    summary = {"variant": args.variant, "research_model": research_model, "synthesis_model": synthesis_model,
               "rows": len(rows), "completed": round(len(ok) / len(rows), 3) if rows else None, "error_attempts": n_err,
               "total_cost_usd": round(sum((r.get("cost_usd") or 0) + (r.get("judge_cost_usd") or 0) for r in rows), 2),
               "metrics": {}}
    for m in state["metrics"]:
        xs = [r["grade"][m["id"]] for r in ok if r["grade"].get(m["id"]) is not None]
        if xs:
            mean, half = ci95(xs)
            summary["metrics"][m["id"]] = {"mean": round(mean, 3), "ci95": round(half, 3) if half == half else None, "n": len(xs)}
    (vdir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")  # before printing: a print crash can't lose it

    print(f"\n{args.variant} ({research_model} -> {synthesis_model}): {len(ok)}/{len(rows)} produced a guide, "
          f"{n_err} infra-error attempts, ${summary['total_cost_usd']} total")
    for m in state["metrics"]:
        if m["id"] in summary["metrics"]:
            x = summary["metrics"][m["id"]]
            print(f"  {m['label']:<15} {x['mean']:.3f} +/- {x['ci95'] if x['ci95'] is not None else 'n/a'}  (n={x['n']})")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="baseline")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--cases", help="comma-separated case ids (default: all)")
    ap.add_argument("--timeout-s", type=int, default=900, help="hard wall-clock ceiling per case")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--approve-harness", action="store_true", help="for the user to pass after reviewing harness changes")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
