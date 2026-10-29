# What each metric means

**Headline: `fact_recall`** (reference slice only). This is the share of an app's key facts (3–5 per app, 25 in total, listed in `evals/cases.json`) that the generated guide actually states. A judge model (Sonnet 5.5) grades it. It counts a fact only if the guide asserts it; hints, hedges, contradictions and wrong numbers don't count.

| Metric | Applies to | How | Why it's here |
|---|---|---|---|
| fact_recall | reference (6) | judge | Does the guide contain what matters? |
| quote_support | every case with quoted evidence | judge: supports = 1, partial = 0.5, not = 0 | Catches a real URL with made-up content, which `validate.py` can't see |
| grounded_rate | every case | code | Share of citations the validator didn't downgrade (the URL was really retrieved) |
| steps sourced | every case | code | Share of steps backed by at least one retrieved source |
| managed_match | wherever ground truth exists | code, against the **live Composio API** | A fact with a non-model answer key |
| probed | every case | code | Did the harness touch live provider infrastructure at all? |
| difficulty dist | reference | code | Tier distance from the reference verdict (0–3, lower is better) |
| edge_pass | edge (3) | judge | Not-OAuth / ambiguous / URL-input behaviour |

`null` means the metric doesn't apply to the case. It is never a 0 standing in for "not applicable".

**Failures.**
- Infrastructure failures go to `errors.jsonl` and are never scored: timeout, API error, served-model mismatch, grader crash.
- The product failing to produce a guide counts as a real outcome. That covers research not converging on either model, the guide failing its schema three times, and a refusal. These cases are written with `status: "failed"` and lower the completion rate in `summary.json`.

**Cost.** Each case uses two models, so `cost_usd` is computed per model by the app (`provenance.cost_breakdown`) rather than derived from a single row-level `model × usage`. The judge's spend is in `judge_cost_usd`.

**Known weaknesses of this eval.**
- **The answer key was written by Claude Opus.** It came from the curated guides, and every fact traces to a fetched official source, but no person has verified it. Synthesis also runs on Opus, so the key could reward imitation. The judge is Sonnet to limit self-preference. A human pass over the 25 facts would remove this caveat.
- **Resolution is coarse.** 25 facts means each fact is 4 points of `fact_recall`, and 6 reference cases × 1 rep is a wide confidence interval. Treat differences under about 10–15 points as noise unless the reference slice is rerun with `--reps 2` or more.
- **The famous apps are partly answerable from memory.** Slack and Gmail are a weak test of research ability. The two obscure cases (Bexio, Clio) are a better one, but they have no answer key.
- **The judge is cross-checked, not human-calibrated.** `python -m evals.judge --selfcheck` passes the known negatives (an empty guide, "I don't know", the wrong app's guide). Re-grading 35 guides with Opus as the judge (`python -m evals.regrade`) gave identical scores on 31 and kept all 35 within one fact. Agreement with human grades is still unmeasured.
