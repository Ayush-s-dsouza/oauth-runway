# Verification log

This log covers what was checked, how it was checked, what changed as a result, and what is still uncertain.

## Evidence levels

Every claim in a guide carries one of these labels, and the guide page shows it next to the claim.

| Label | Meaning | Who can assign it |
|---|---|---|
| **Tested by hand** | A person did it in the provider console and saw the result | Humans only. The validator downgrades any agent claim that uses it |
| **Probed live** | Our HTTP probe hit the URL during research (portal page, authorize endpoint, OIDC discovery, Composio catalog) | Agent or human, but only if a probe actually ran against that host |
| **Official docs** | Provider-owned page, read during research | The URL must appear in that run's search/fetch results. Otherwise the claim is downgraded |
| **Community** | Forum, blog, Stack Overflow, third-party docs | Same rule as official docs |
| **Inferred** | Reasoning without a retrieved source | Default for anything that can't show a source |

The downgrade rules live in [app/validate.py](app/validate.py) and are tested in [tests/test_core.py](tests/test_core.py).

## Curated library: what was checked (2026-10-28)

All six curated guides were researched against provider documentation. Each `official_doc` claim was re-read against the fetched page before it was written down, and probes were run live against every provider. **None of the console steps have been hand-tested yet.** The pages say so ("Source-checked … not yet hand-tested"), and the checklist below is the plan to close that gap.

| App | Official-doc claims | Probes | Community | Hand-tested |
|---|---|---|---|---|
| GitHub | 7 | 2 | 0 | not yet |
| Gmail (Google) | 9 | 3 | 1 | not yet |
| Slack | 5 | 2 | 1 | not yet |
| Shopify | 4 | 3 | 2 | not yet |
| HubSpot | 4 | 3 | 0 | not yet |
| Notion | 5 | 3 | 0 | not yet |

## What research changed: assumptions corrected or sharpened

These are cases where the first draft of the answer, often from memory, was wrong, incomplete, or less useful than the sourced answer.

1. **"Slack needs no review" was true but misleading for agents.** Public distribution of a Slack app needs no review. A 2025 change, however, limits `conversations.history` and `conversations.replies` to **1 request/minute with 15 objects** for commercially distributed apps that aren't in the Slack Marketplace. For an agent that reads channels, that is the real blocker, and getting into the Marketplace needs 10+ active workspaces and a functional review of up to 10 weeks. The Slack guide now leads with this. Slack also edited the changelog date for existing installs in place (Sep 2, 2025 → Mar 3, 2026), which is recorded as an open question.
2. **Notion: my first assumption was "public integrations need Notion review before others can install them."** The docs say otherwise: public connections "work independently of Marketplace listings" and can be used via OAuth without being listed. Only listing is reviewed (5–10 business days). One caption still says the Authorization URL appears "after a public connection is submitted for review", which the docs don't explain. It is kept as an open question and is the first hand-test item.
3. **HubSpot "3 active installs".** I remembered this requirement, and the first search did not support it. A later search found it in HubSpot's listing requirements. The more important fact turned out to be different: a Marketplace-distribution OAuth app is **capped at 25 installs until listed**, and a private app at 10 allowlisted accounts. The real gate is that cap, not approval.
4. **Google: the scope choice determines the timeline.** Composio's default Gmail scopes include `https://mail.google.com/`, which is **restricted**. Copying those defaults moves a developer from "verification in days" (gmail.send is only sensitive) to "verification plus an annual third-party CASA security assessment that can take several weeks". The guide now opens with choosing the scope.
5. **Composio defaults are broad everywhere**: 40 Slack scopes, 33 HubSpot, 36 Shopify (including customer and order data, which triggers Shopify's protected-customer-data review). Each guide now has a "trim the scopes" step that an agent can do.
6. **The Composio redirect URI is inconsistent.** Composio's docs give `https://backend.composio.dev/api/v3/toolkits/auth/callback`, while the live toolkit API's default `oauth_redirect_uri` field returns `https://backend.composio.dev/api/v1/auth-apps/add`. Guides tell developers to copy what their dashboard shows and print both values. This is **not resolved**; see the next steps below.
7. **Google may reject a third-party callback domain.** Google requires every authorized domain to be verified by a project owner. A community thread reports being blocked by a platform's callback domain (`*.supabase.co`), with no official answer. Composio documents a 302 redirect proxy on your own domain, so the Gmail guide recommends it as a step an agent can implement. This is still unconfirmed for `backend.composio.dev` specifically.
8. **The HubSpot developer signup URL now redirects** to the standard CRM signup with `intent=developer`. That suggests the separate developer account has been folded into regular accounts, but the docs checked don't say so.

## What the probes can and can't show

| Provider | Authorize endpoint, invalid client | What we learn |
|---|---|---|
| Google | Redirects to an error page; `authError` decodes to `invalid_client: The OAuth client was not found.` | The endpoint is live and validates the client before login |
| GitHub | Redirects to sign-in first | GitHub authenticates the user before validating the client |
| Slack, HubSpot, Notion | 200, page rendered in JavaScript | Reachability only. Validation happens client-side, out of reach of a plain HTTP probe |
| Shopify | 404 "Store unavailable" for a made-up shop | The authorize URL is per-store; you need a real (dev) store to test |

Two fixes came out of reading the probe output. The probe's login-wall detection flagged Google's error page as a login page, because its path contains "signin", so the heuristic was tightened. Google's `authError` is base64 protobuf, so the probe now decodes it into readable text for the agent.

## Hand-test checklist (about 15 min per app, all free accounts)

Record the date, what you saw, and a screenshot. Change the matching evidence to `"status": "tested"` in `guides/<app>.json`, or correct the claim and add a line to the section above.

- [ ] **Notion:** create a public connection with "Any workspace". Does it go into a review state before the Authorization URL appears? Can a second workspace install it right away? *(open question)*
- [ ] **Composio redirect URI:** create a custom auth config in the Composio dashboard. Which callback does it show (v1 or v3)? Start a connection and read the `redirect_uri` in the authorize URL it generates.
- [ ] **GitHub:** create an OAuth app with the Composio callback and connect through Composio. Does the consent screen show your app name?
- [ ] **Slack:** create the app from a manifest, install it into your own workspace, and confirm the Activate Public Distribution checklist items.
- [ ] **Google:** create a project, set it to External + Testing, add yourself as a test user, connect with `gmail.send`, and confirm the unverified-app warning appears. Can `backend.composio.dev` be saved as a redirect URI without adding it as an authorized domain?
- [ ] **HubSpot:** create a project app (`hs project create`), set redirectUrls, upload, and install into a developer test account.
- [ ] **Shopify:** Partner account, then a development store, then a custom-distribution app. Confirm the "can't change distribution later" warning.

## Agent vs. curated (to run once an API key is configured)

```bash
python -m scripts.generate "Slack" --slug slack-agent
python -m scripts.compare slack slack-agent
```

`scripts/compare.py` lines up the verdict, gate review flags, scope tiers and evidence mix, and lists the agent's unsourced steps. Run it for all six apps and record the disagreements here.

## Eval results: routing and harness steering (2026-10-28/29)

The eval and its caveats are described in [README § Evaluation](README.md). Rows, traces and the report are in `.claude/hillclimb/oauth-guide/`. Run `python -m evals.compare` and `python -m evals.diagnose` to reproduce the tables.

**The question:** how much of this can run on the cheapest model (Haiku 5.5) without losing quality, if the harness does the steering?

### Results (6 reference apps × 3 runs = 18 graded guides per setup; v1 and v2 have 1 run each)

| Setup | Research → writing | Steering | Key facts stated | Steps backed by a real source | Quotes support claims | Difficulty error | $/guide |
|---|---|---|---|---|---|---|---|
| v1 | Opus → Opus | none | **0.93** ± 0.08 | 0.99 | 0.65 | 0.17 | $0.85 |
| baseline | Haiku → Opus | none | 0.75 ± 0.12 | 0.94 | 0.67 | 0.33 | $0.30 |
| v2 | Haiku → Haiku | none | 0.67 ± 0.23 | 0.91 | 0.62 | 0.50 | $0.055 |
| v3 | Haiku → Opus | gate | 0.80 ± 0.10 | 0.98 | 0.68 | 0.22 | $0.34 |
| v4 | Haiku → Haiku | gate | 0.87 ± 0.09 | 0.99* | 0.61 | 0.67 | $0.07 |
| v5 | Haiku → Haiku | gate + plan + lock | 0.85 ± 0.08 | 0.82 | 0.59 | 0.67 | $0.09 |
| v6 | Haiku → Opus | gate + plan + full lock | 0.91 ± 0.06 | 0.93 | 0.57 | 0.50 | $0.37 |
| **v7 (shipped default)** | Haiku → Opus | gate + plan + quote-only lock | **0.88** ± 0.09 | **1.00** | **0.71** | 0.29 | **$0.40** |

\* v4's 0.99 is inflated: its writer invented 23% of its evidence (see finding 3).

v7 has 14 of 18 runs: the API credit ran out on its 3 Notion runs and one HubSpot grading call. These are logged as infrastructure errors and not scored. On the same 5 apps, v6 vs v7 differs by **+0.12 quote support (better on all 5 apps)** and −0.02 key-fact recall (noise).

### What we learned, in the order we learned it

1. **A cheap researcher fails by reading too little, not by reasoning badly.** Unsteered Haiku read a median of 4 pages where Opus read 14, and ran 3 searches to Opus's 8. The facts it missed (HubSpot's 25-install cap, Shopify's "you can't change distribution later") were never in its findings. The writer didn't drop them; nobody found them.
2. **Prompting didn't fix it; code did.** The prompt already asked for 6–10 page reads.
   - The **coverage gate** (findings bounced until ≥6 pages are read and test-user limits, public review, scopes and requirements are each covered by a fetched provider page) raised all-Haiku recall from 0.67 to 0.87, and fixed Shopify (1.0 in every gated run).
   - The gate checks counts and categories, though, and Haiku satisfied it with the wrong pages. The **research plan** (the code supplies 5 searches, including one aimed at the developer changelog) fixed HubSpot from 0.50–0.58 to 0.92–1.00, because recent limits are announced in changelogs.
   - Searches rose from 3 to 5–6.
3. **A cheap writer invents evidence.** Told to cite only the given findings:
   - Opus invented 1–6% of its evidence.
   - Haiku invented 16–23%, and altered 17–21% of quotes.
   - The URL check can't see this (the URLs are real). It's why v4 *looked* 99% sourced.
   - The **evidence lock** (code rebuilds evidence from findings by id) makes fabrication impossible. In v5 that exposed the truth: 82% of steps are actually sourced.
4. **The judgement step stays on the strong model.** Every Haiku-writer setup misjudged the difficulty tier (error 0.50–0.67 vs 0.17–0.33). No steering moved it.
5. **The strong writer was quietly fixing the researcher's overreach.** Opus rewrote about 90% of research claims and, in doing so, tightened them to their quotes (quote support 0.67–0.68). The full evidence lock (v5, v6) forced research's own wording and quote support fell to 0.57–0.59. My lock design caused that regression. **v7** tests the fix: lock quotes, URLs and status, but let the writer reword claims.
6. **v7 confirmed the fix:** lock quotes, URLs and status, and let the writer reword the claim. Quote support rose from 0.59 to 0.71 (better than all-Opus's 0.64), steps sourced reached 1.00, and writer-invented evidence fell to 1%.
7. **The shipped configuration is v7.**
   - Research on Haiku, made reliable by code (coverage gate + research plan).
   - Writing on Opus, kept honest by code (quote-only evidence lock).
   - Against all-Opus: key-fact recall is level within noise (0.88 vs 0.92), evidence quality is better, and the cost is **55% lower** ($0.40 vs $0.89 per guide).
   - Most of the work, and nearly all the tokens read, run on the cheap model. The strong model is used only for judgement and writing, which is where every Haiku-writer setup measurably fell short.

### Smaller findings
- The GitHub "miss" was correct grading. The guide labelled org-owner approval "inferred, not confirmed", because research had found it only on a forum.
- OpenWeatherMap passed in every setup. My prediction that the 3-stage schema would force an invented OAuth flow was wrong.
- Mercury failed only on the unsteered baseline: it picked the right product but never said which Mercury it chose.
- Research never stalled, so escalation to Opus never fired (0 of 241 runs). The stall trigger is safe but untested in the wild.
- The first live run found a tool-schema/function mismatch that crashed the whole run. It's fixed, with a test that checks every tool against its function.
- Notion's difficulty disagrees with the reference across setups. It mirrors the open question in the curated guide, so the reference may be the uncertain part.

### Caveats
- The answer key is model-authored (25 facts written by Claude from source-checked guides), not human-verified.
- n = 18 per setup gives a confidence interval of about ±0.06–0.12 on recall. The v6-vs-v1 difference is inside it, so "matches" means "not distinguishable at this sample size".
- 3 of v1's 23 guides ran on slightly newer code (bug fixes, with the gate off).

**Spend:** about $75 in total across all variants and grading (list-price estimates from token usage).

**Still open:**
- v7's 3 Notion runs and one HubSpot regrade, about $1.50.
- v7 on the 17 non-reference cases, about $7.
- A Sonnet-writer variant: writing is the expensive half of v7.
- A human pass over the 25-fact answer key.
