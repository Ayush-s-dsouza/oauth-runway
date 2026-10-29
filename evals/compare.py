"""Side-by-side of the routing variants: does cheaper routing hold quality?

    python -m evals.compare
"""

import json
import sys
from pathlib import Path

from evals.run import FLOW, VARIANTS


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    state = json.loads((FLOW / "_state.json").read_text(encoding="utf-8"))
    found = {v: json.loads((FLOW / v / "summary.json").read_text(encoding="utf-8"))
             for v in VARIANTS if (FLOW / v / "summary.json").exists()}
    found = {v: x for v, x in found.items() if x.get("rows")}  # skip variants with no scored rows (e.g. all infra errors)
    if not found:
        sys.exit("no summary.json yet - run `python -m evals.run --variant baseline` first")
    cols = list(found)
    print(f"{'':18}" + "".join(f"{v + ' (' + VARIANTS[v][0].split('-')[1] + '->' + VARIANTS[v][1].split('-')[1] + ')':>26}" for v in cols))
    for m in state["metrics"]:
        cells = []
        for v in cols:
            x = found[v]["metrics"].get(m["id"])
            cells.append(f"{x['mean']:.2f} +/- {x['ci95'] if x['ci95'] is not None else '-'} (n={x['n']})" if x else "-")
        arrow = " (lower=better)" if m.get("better") == "lower" else ""
        print(f"{m['label'] + arrow:<18}" + "".join(f"{c:>26}" for c in cells))
    for key, label in (("completed", "completed"), ("total_cost_usd", "total $"), ("error_attempts", "infra errors")):
        print(f"{label:<18}" + "".join(f"{str(found[v].get(key)):>26}" for v in cols))
    for v in cols:
        rows = [json.loads(l) for l in (FLOW / v / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        ok = [r for r in rows if r["status"] == "ok"]
        if ok:
            print(f"{v}: ${sum(r['cost_usd'] or 0 for r in ok) / len(ok):.3f}/guide, "
                  f"{sum(r['escalated'] for r in ok)}/{len(ok)} escalated, "
                  f"median latency {sorted(r['latency_s'] for r in ok)[len(ok) // 2]}s")
    print("\nDifferences inside the +/- bands are noise. See .claude/hillclimb/oauth-guide/metrics.md for caveats.")


if __name__ == "__main__":
    main()
