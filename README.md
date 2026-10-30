# OAuth Runway

**How long until your OAuth app is approved?** You can look up an app and get a guide you can share for registering your own OAuth app with that provider. Each guide covers where to start, what you do versus what an agent can do, how long it takes, and what can block approval. If an app isn't in the library, an agent researches it live and writes the guide.

- **Live demo:** https://oauth-runway.onrender.com (Render free plan: the first visit after 15 idle minutes takes about a minute to wake)
- Library: GitHub · Notion · Slack · HubSpot · Shopify · Gmail (Google). They span instant to heavy review.
- Agent-generated library: 17 guides from the shipped configuration (Zoom, Salesforce, Outlook, Xero, DocuSign, Bexio, Clio, Calendly and more), labelled as not reviewed.
- Every guide is available as HTML (`/g/<app>`), Markdown (`/g/<app>.md`) and JSON (`/g/<app>.json`).

## The problem as I understood it

An agent builder on Composio can start on Composio's **managed** OAuth app. For production they usually need **their own** app: their name on the consent screen, scopes beyond the defaults, and their own quota. That means registering an app with each provider, and sometimes a review that lasts weeks. Composio lists 382 OAuth toolkits, and 258 of them have no managed app at all.

The key insight that shaped the product: **approval is rarely one event.** Almost every provider lets an app work right away in a limited mode, and only gates *wider audiences* behind review. So every guide is built around three gates:

1. **Works for you.** Create the app and connect your own account. Usually minutes.
2. **Works for test users.** Allowlists, dev stores, unlisted installs. These come with caps: Google allows 100 test users with 7-day tokens, HubSpot 25 installs, Shopify one Plus organization.
3. **Works for anyone.** Verification, marketplace review or a security assessment. This is where the weeks go.

That gives developers the answer they actually need: *you can build today; public launch takes about X, and here is what decides X.*

## Key decisions

- **Gates before steps.** The header answers the brief's four questions at a glance: difficulty, your hands-on time, time to first connection, and approval wait. The steps sit below, grouped by gate, so a developer can stop at the gate they need.
- **Every step says who does it:** *Agent can do* (draft a manifest or privacy policy, trim scopes, call a provider's management API, build a redirect proxy), *You do* (identity, legal, payment, UI-only console actions, a demo video), or *Wait on provider*. The owner bar shows the split.
- **Evidence is shown, not hidden.** Each claim carries a source and a status (tested / probed / official docs / community / inferred). Agent output is **validated in code after generation**:
  - any URL the agent cites but never retrieved during that run is downgraded to "inferred"
  - "probed" without a matching probe is downgraded
  - the agent can never claim "tested"
  - unsourced steps are flagged "unsourced" on the page
- **The tool knows about Composio.** The agent asks Composio's toolkit API whether managed OAuth exists, which scopes Composio requests by default, and which authorize URL to probe. That surfaced the most useful findings: Composio's defaults are broad (Gmail includes restricted `https://mail.google.com/`; Slack has 40 scopes), and copying them pushes you into a heavier review than you need.
- **Probes instead of browser automation.** Most portals sit behind a login, CAPTCHA or a JavaScript app. Instead, the agent probes portals and **authorize endpoints with a deliberately invalid client_id**. Nothing is created, and providers like Google reveal how they validate clients. A headless browser driving signups would be fragile and would need credentials.
- **Curated over complete.** Six source-checked guides with honest labels are worth more than 100 unchecked ones. The live agent covers the long tail and labels its own output as unreviewed.

## How the agent works

```
query
 → Phase 1, RESEARCH (claude-haiku-5-5): tool loop, extraction only
     ├─ web_search / web_fetch (Anthropic server tools)
     ├─ composio_toolkit      → managed auth? default scopes? authorize URL
     ├─ probe_url             → portal exists? login wall?
     ├─ probe_oauth_authorize → endpoint live? how does it reject an unknown client?
     ├─ probe_oidc_discovery  → endpoints + scopes_supported
     └─ compile_findings      → cited facts only (no verdict, no gates)
     (stalls → retried once on the synthesis model; recorded in provenance)
 → Phase 2, SYNTHESIS (claude-opus-5-5): no tools, findings in, guide out
     └─ submit_guide          → pydantic-validated GuideDraft built only from the findings
 → validate.py (evidence downgrades) → saved JSON → /g/<slug>
```

The routing is fixed in code (see DECISIONS.md), never chosen by the person using the product. Each guide records which model did which phase, the cost per model, and whether research escalated.

Progress streams to the browser over Server-Sent Events as searches, pages read, probes and validation happen. Jobs are buffered server-side, so a live page can be reloaded or shared mid-run. A typical run is 2–5 minutes; cost per guide is shown in its provenance.

Safety: probes reach public hosts only. Each redirect hop is re-checked against private, loopback and metadata IPs, with an 8 s timeout and a 512 KB cap (tested). Generation is rate-limited per IP and capped per day. Identical requests within 7 days are served from cache. A re-research never overwrites a curated guide.

## Evaluation: does it work on apps it hasn't seen, and on cheaper models?

`evals/` runs the real pipeline over **23 cases**:
- **6 reference apps**, each with an answer key of 25 key facts in total
- **12 long-tail apps**
- **2 obscure apps** (Bexio, Clio), where the model's memory can't carry it
- **3 edge cases**: an API-key-only service, an ambiguous name, and a URL as input

It runs them under three routing variants: Haiku→Opus (default), Opus→Opus and Haiku→Haiku.

| Metric | How it's graded |
|---|---|
| **fact recall** (headline) | Does the guide state the key facts? Judged by Sonnet 5.5, which is not a model under test |
| quote support | Do the quoted sources actually say what the guide claims? Catches made-up content behind real URLs |
| grounded / steps sourced | Code: share of citations the validator kept, and share of steps backed by a retrieved source |
| managed match | Code, checked against the **live Composio API** rather than a model-written answer |
| probed, difficulty dist, edge pass | Live investigation happened; verdict tier vs. reference; edge-case behaviour |

```bash
python -m evals.judge --selfcheck                                        # judge must fail empty / "I don't know" / wrong-app guides
python -m evals.run --variant baseline --cases gmail,bexio --approve-harness   # pilot: measures real cost per guide
python -m evals.run --variant baseline && python -m evals.run --variant v1 && python -m evals.run --variant v2
python -m evals.compare                                                  # side by side: does cheaper routing hold quality?
```

**Results (9 configurations, about 290 graded guides, two judge models):**
- The shipped configuration does Haiku research, steered by code (a coverage gate and a research plan), then Opus writing, with code locking every quote.
- It matches all-Opus on key-fact recall (0.90 vs 0.93; 0.94 under an Opus judge) and beats it on evidence quality (quote support 0.70 vs 0.65).
- It costs **55% less** ($0.40 vs $0.87 per guide).
- On 17 apps with no answer key, it matches or beats all-Opus on every measure.
- Sonnet was tested as the writer and rejected: half the cost, but weaker recall and difficulty judgement.
- Each mechanism was added because the eval exposed a specific failure: Haiku read too little, missed changelogs, and invented evidence when writing.

Full table and findings: [VERIFICATION.md](VERIFICATION.md#eval-results-routing-and-harness-steering-2026-1028-29). Reasoning: [DECISIONS.md](DECISIONS.md).

Infrastructure failures (timeouts, API errors, model substitution) are logged separately and never scored. A guide the product fails to produce is scored as a failure. The full metric definitions and the eval's own known weaknesses are in [.claude/hillclimb/oauth-guide/metrics.md](.claude/hillclimb/oauth-guide/metrics.md). The main weakness: the answer key was written by Claude and hasn't been human-verified.

## Verification

See **[VERIFICATION.md](VERIFICATION.md)**. In short:
- The six curated guides were checked against fetched provider docs and live probes.
- Eight assumptions were corrected or sharpened along the way. For example: Slack's real blocker for agents is the rate limit on apps outside its Marketplace, not review. Notion reviews listings, not OAuth use. HubSpot's real gate is a 25-install cap.
- Two things are unresolved: the Composio redirect URI inconsistency (v1 vs. v3) and whether Google accepts Composio's callback domain during verification.
- **Console steps are not hand-tested yet.** The pages say so, and VERIFICATION.md has the checklist.

## Run it locally

```bash
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                                # add ANTHROPIC_API_KEY (and optionally COMPOSIO_API_KEY)
uvicorn app.main:app --reload                       # http://localhost:8000
pytest                                              # 20 tests: validator, SSRF guard, two-phase agent + escalation, web flow, eval wiring
python -m scripts.generate "Calendly"               # CLI generation with progress
```

The library works without any keys; only live generation needs `ANTHROPIC_API_KEY`. `COMPOSIO_API_KEY` is optional: without it the agent skips the Composio catalog lookup.

## Deploy (Render, free plan)

`render.yaml` is a Render Blueprint. In Render: **New → Blueprint**, pick this repo, then paste `ANTHROPIC_API_KEY` (and optionally `COMPOSIO_API_KEY`) when prompted. Keys are never committed.

The free plan is fine for a demo, with three limits worth knowing:
- **Sleep:** after 15 idle minutes the service sleeps, and the first visit then takes about a minute to wake.
- **Ephemeral disk:** guides generated live disappear when the service sleeps or restarts. The curated guides and the bundled agent-generated samples ship inside the image, so they're unaffected.
- **Spend cap:** live generation is capped at 15 a day, at about $0.40 per guide. Use a dedicated API key with a spend limit set in the Anthropic console.

Why not Cloudflare: Workers' free tier allows about 10 ms of CPU per request and keeps no in-memory state between requests. A guide is a 2–5 minute background job streamed to the browser, so it would need a rewrite onto Durable Objects, or Cloudflare Containers (paid). Hugging Face Docker Spaces now require a paid plan, and Fly.io has no free tier for new accounts.

The Dockerfile serves on `$PORT`, and the health check is `/api/status`.

## Getting to 100 guides

- Seed a list of 100 apps from Composio's OAuth directory, weighted toward the 258 apps with no managed option, where the need is greatest.
- Run `scripts/generate.py` over the list. At volume, use the Message Batches API for half the cost; it is offline work anyway.
- Re-research on a schedule (e.g. monthly). Diff each new guide against the previous one and flag changed verdicts or timelines for human review. Provider policies drift: Slack edited a changelog date in place.
- Promote a guide from "agent" to "curated" only after a person runs the hand-test checklist.

## Limitations

- Console steps in the curated guides are source-checked, **not hand-tested**.
- Review timelines are what providers state plus what the community reports. Real waits vary with the queue and with how fast you reply to reviewers.
- Probes can't see validation behaviour on JavaScript-rendered consent pages (Slack, HubSpot, Notion); for those they only confirm the endpoint is reachable.
- The URL check verifies that the agent *retrieved* a source, not that the source says what the agent claims. Quotes are shown so a reader can judge, but they are not checked automatically.
- The SSRF guard resolves DNS before connecting, but doesn't pin the resolved IP (DNS-rebinding window). That is acceptable for a demo, not for production.
- Job state lives in memory, so a server restart drops in-flight generations; finished guides persist on the volume.

## Repository layout

```
app/agent.py           Claude research loop (server tools + probes + submit_guide)
app/prompts.py         system prompt (three gates, evidence rules, ownership)
app/probes.py          SSRF-guarded HTTP probes
app/composio_lookup.py Composio toolkit catalog lookup
app/validate.py        post-generation evidence checks
app/schema.py          Guide model (drives tool schema, validation, rendering)
app/main.py            FastAPI: library, guide pages, SSE generation
guides/*.json          curated, source-checked guides
scripts/generate.py    CLI generation · scripts/compare.py  agent-vs-curated diff
```
