"""Why do variants differ? Research depth, gate activity, how much evidence the
writer invented, and per-app recall averaged over reps -- read from the
traces, guides and rows each run wrote.

    python -m evals.diagnose
"""

import json
import re
import statistics as st
import sys
from collections import defaultdict

from evals.run import FLOW, VARIANTS

REFERENCE = ("github", "gmail", "slack", "shopify", "hubspot", "notion")


def load(v):
    rp = FLOW / v / "results.jsonl"
    rows = [json.loads(l) for l in rp.read_text(encoding="utf-8").splitlines()] if rp.exists() else []
    by_case = defaultdict(list)
    for r in rows:
        if r["status"] == "ok":
            by_case[r["prompt_id"]].append(r)
    return by_case


def trace_stats(v):
    pages, searches, bounced, invented = [], [], 0, []
    for p in (FLOW / v / "traces").glob("*.json"):
        t = json.loads(p.read_text(encoding="utf-8"))
        pages.append(sum(1 for x in t if x.get("name") == "web_fetch"))
        searches.append(sum(1 for x in t if x.get("name") == "web_search"))
        bounced += any("research sent back" in str(x.get("content", "")) for x in t)
        lock = next((re.search(r"dropped (\d+) invented", str(x.get("content", ""))) for x in t
                     if "evidence lock" in str(x.get("content", ""))), None)
        guide_p = FLOW / v / "guides" / p.name
        fcs = [x for x in t if x.get("name") == "compile_findings"]
        if guide_p.exists() and fcs:
            g = json.loads(guide_p.read_text(encoding="utf-8"))
            ids = {f["id"] for f in json.loads(fcs[-1]["content"])["findings"]}
            n_final = len(g["evidence"])
            if lock:  # locked variants: the writer's invented ids were dropped before saving
                k = int(lock.group(1))
                invented.append(k / (k + n_final) if k + n_final else 0)
            else:
                invented.append(sum(1 for e in g["evidence"] if e["id"] not in ids) / n_final if n_final else 0)
    return pages, searches, bounced, len(pages), invented


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    variants = [v for v in VARIANTS if load(v)]
    data = {v: load(v) for v in variants}
    ts = {v: trace_stats(v) for v in variants}
    print(f"{'':26}" + "".join(f"{v:>10}" for v in variants))
    med = lambda xs: f"{st.median(xs):.0f}" if xs else "-"
    print(f"{'pages read (median)':<26}" + "".join(f"{med(ts[v][0]):>10}" for v in variants))
    print(f"{'searches (median)':<26}" + "".join(f"{med(ts[v][1]):>10}" for v in variants))
    print(f"{'runs bounced by gate':<26}" + "".join(f"{f'{ts[v][2]}/{ts[v][3]}':>10}" for v in variants))
    print(f"{'writer-invented evidence':<26}" + "".join(f"{(st.mean(ts[v][4]) if ts[v][4] else 0):>10.0%}" for v in variants))
    allrows = {v: [r for rs in data[v].values() for r in rs] for v in variants}
    print(f"{'escalated to Opus':<26}" + "".join(f"{sum(r['escalated'] for r in allrows[v]):>10}" for v in variants))
    print(f"{'$ / guide':<26}" + "".join(f"{st.mean(r['cost_usd'] for r in allrows[v]):>10.3f}" for v in variants))
    print(f"{'median latency s':<26}" + "".join(f"{st.median(r['latency_s'] for r in allrows[v]):>10.0f}" for v in variants))
    print("\nkey-fact recall per reference app (mean over reps, n in brackets)")
    for cid in REFERENCE:
        cells = []
        for v in variants:
            xs = [r["grade"]["fact_recall"] for r in data[v].get(cid, []) if r["grade"].get("fact_recall") is not None]
            cells.append(f"{st.mean(xs):.2f}({len(xs)})" if xs else "-")
        print(f"  {cid:<24}" + "".join(f"{c:>10}" for c in cells))
    print("\nedge cases (pass=1)")
    for cid in ("openweathermap", "mercury", "hubspot-url"):
        print(f"  {cid:<24}" + "".join(f"{str(data[v][cid][0]['grade'].get('edge_pass')) if data[v].get(cid) else '-':>10}" for v in variants))


if __name__ == "__main__":
    main()
