"""Re-grade saved guides with a different judge model, to check a comparison
isn't an artefact of the judge preferring its own model's writing.

    python -m evals.regrade --judge claude-opus-5-5 --variants v7,v8

Reads <variant>/guides/*.json (never regenerates), writes
<variant>/regrade_<judge>.jsonl (resumable), prints per-variant means.
"""

import argparse
import asyncio
import json
import math
import statistics as st
import sys

from dotenv import load_dotenv

from evals.run import FLOW, ROOT

REFERENCE = ("github", "gmail", "slack", "shopify", "hubspot", "notion")


async def main_async(args) -> None:
    load_dotenv(ROOT / ".env")
    from app.render_md import to_markdown
    from app.schema import Guide
    from evals.judge import Judge

    cases = {c["id"]: c for c in json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))}
    sem = asyncio.Semaphore(args.concurrency)
    for v in args.variants.split(","):
        out_path = FLOW / v / f"regrade_{args.judge}.jsonl"
        done = {(r["prompt_id"], r["rep"]) for r in map(json.loads, out_path.read_text(encoding="utf-8").splitlines())} if out_path.exists() else set()
        rows = [json.loads(l) for l in (FLOW / v / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        todo = [r for r in rows if r["status"] == "ok" and r["prompt_id"] in REFERENCE and (r["prompt_id"], r["rep"]) not in done]

        async def one(r):
            async with sem:
                g = Guide.model_validate_json((FLOW / v / "guides" / f"{r['prompt_id']}_rep{r['rep']}.json").read_text(encoding="utf-8"))
                j = Judge(model=args.judge)
                fr, _ = await j.fact_recall(to_markdown(g), cases[r["prompt_id"]]["expected"]["facts"])
                qs, _ = await j.quote_support([e.model_dump() for e in g.evidence])
                with out_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"prompt_id": r["prompt_id"], "rep": r["rep"], "judge": args.judge,
                                        "fact_recall": fr, "quote_support": qs, "judge_cost_usd": j.cost()}) + "\n")

        await asyncio.gather(*(one(r) for r in todo))
        graded = [json.loads(l) for l in out_path.read_text(encoding="utf-8").splitlines()] if out_path.exists() else []
        for m in ("fact_recall", "quote_support"):
            xs = [x[m] for x in graded if x[m] is not None]
            if xs:
                half = 1.96 * st.stdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else float("nan")
                print(f"{v} [{args.judge}] {m}: {st.mean(xs):.3f} +/- {half:.3f} (n={len(xs)})")
        print(f"{v} judge spend: ${sum(x['judge_cost_usd'] for x in graded):.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", required=True)
    ap.add_argument("--variants", required=True)
    ap.add_argument("--concurrency", type=int, default=4)
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
