# Findings from live use (source of truth)

This file covers what twelve live guides on the deployed site taught us, what was fixed, and what to do next, in priority order. The per-claim evidence is in [VERIFICATION.md](VERIFICATION.md). This file is the summary and the backlog. Last updated: 2026-10-30.

## 1. What was tested

- **12 guides were generated on the live site** (https://oauth-runway.onrender.com):
  - Asana and Teamleader
  - ten apps confirmed absent from Composio's catalog (by slug lookup *and* Composio's toolkit search): Planning Center, Netatmo, Withings, Oura, Exact Online, Procore, Fortnox, Etsy, Toast and Amazon SP-API.
- **About 30 claims that drive the verdicts were checked by hand** against the providers' own pages. The pages were fetched directly; PDFs were text-extracted. Composio facts were checked against Composio's API for all twelve.
- **Expected difficulties were written down before generating** (easy: Planning Center, Netatmo; hard: Toast, Amazon) so the verdicts could be scored, not rationalised afterwards.

## 2. What the evidence says works

| Signal | Result |
|---|---|
| Wrong facts found | **0 of about 30 claims checked.** Every quote that could be re-fetched matched word for word |
| Third-party numbers | Labelled as such every time: Exact's 5-day review, Etsy's "5 shops", Toast's 12-month write wait-list, Amazon's 2–4-week profile review |
| Routing | 12/12 ran Haiku research then Opus writing, with 0 escalations. $0.29–0.48 per guide (mean about $0.36), 2.5–5.5 min |
| Citation integrity | The validator downgraded 0–2 citations per guide, so research reads what it cites |
| Predicted difficulty | Matched on all four apps predicted beforehand. Overall spread: 6 self-serve, 2 light review, 4 heavy review |
| Non-OAuth and partner-only providers | Handled correctly. Toast has no user-consent OAuth and no self-serve path, and the guide says so first |
| Apps not in Composio | The prose was correct in 12/12. The structured fields were wrong once (fixed, see 3.2) |

**The catches that justify the product.** In each of these a developer would otherwise lose days:
- **Fortnox:** only a company registered under Swedish law can sign the developer agreement.
- **Amazon:**
  - you can't connect even your own account until the developer-profile review passes
  - unlisted public apps are capped at 25 sellers
  - client secrets must be rotated every 180 days
- **Oura:** hard cap of 10 users until Oura approves.
- **Procore:** since 20 Jan 2026, only company admins can install apps.
- **Withings:** from about 12 Oct 2026, new end-user accounts need Withings+ to share data with free-plan apps.
- **Etsy:** three access tiers (Seller → Personal → Commercial), each with its own review.
- **Netatmo:** camera scopes need a review, and require tokens to be stored only on the user's device. That conflicts with a server-side broker like Composio.

## 3. Problems found

### Fixed and shipped

| # | Problem | Found on | Fix | Re-evaluated? |
|---|---|---|---|---|
| 3.1 | The provider's own docs repo was labelled "community" | Teamleader (`github.com/teamleadercrm/api`) | Retrieved first-party sources are upgraded to official. Forums, Q&A, issue trackers and shared doc hosts are excluded, because the first version would have upgraded 32 forum posts | Tested on 18 stored guides (4 correct upgrades) |
| 3.2 | The writer invented Composio fields (a slug and scopes) after a "not found" lookup | Withings | `lock_composio()`: code copies slug, managed flag and default scopes from the lookup; the writer keeps only the prose | Unit-tested. Note: `managed_match` is now correct by construction, so it no longer measures the model |
| 3.3 | The same structure got different tiers (Procore heavy vs Fortnox self-serve) | Procore, Fortnox | The rubric now says an optional listing doesn't raise the tier, and a listing that lifts a hard cap does. This matches the curated guides (Notion, HubSpot) | **No.** The 6-app reference slice needs a re-run (about $7) |

### Open, in priority order

**P1. Turn these spot-checks into eval cases.**
- *What:* the ten non-Composio apps now have hand-verified facts: Fortnox's Swedish entity, Oura's 10 users, Amazon's 25 sellers, Toast's partner-only path and so on. Add them to `evals/cases.json` as reference cases with 2–3 key facts each and an expected tier.
- *Why:* the current answer key was written by Claude and never checked by a person. These facts were checked against fetched provider pages, so they are the strongest ground truth the project has.
- *Effort:* about 1 hour, plus a re-run of about $12.

**P2. Re-run the reference slice for the rubric change (3.3).**
- *What:* the rubric change is the only behaviour change since the eval that hasn't been measured.
- *Why:* the difficulty error for v7 was 0.24. This change should lower it or leave it flat; if it rises, revert.
- *Effort:* about $7, 30 min.

**P3. Split "difficulty" into gate type and duration.**
- *What:* today one tier encodes both *what kind of gate* (none / review / partnership or security assessment) and *how long* (minutes / days / weeks). When the duration is unknown the writer has to choose. Etsy's Commercial Access is a manual review with no published timeline: is it light or heavy?
- *Proposal:* add `gate_type` (enum) plus `wait` (a range and a confidence: official / reported / unknown). The badge would read, for example, "Manual review · wait unknown (reports: weeks)".
- *Why:* developers plan around the wait, and the confidence tells them how far to trust it.
- *Effort:* schema, template and grader changes, then a re-eval.

**P4. Detect dead and moved sources.**
- *What:* a citation counts as "retrieved" if its URL appeared in search results, and search indexes keep pages that have gone offline. Procore's `procore.github.io` docs return 404 today but were cited as official. Amazon moved domains (`developer-docs.amazon.com` → `developer-docs.amazon`); that one redirects, so it's fine.
- *Fix:* at validation time, probe each cited URL (in parallel, via the existing SSRF-guarded probe). Flag a 404 or 410 as "source offline". A JavaScript site that returns 200 for any path will still pass, so this catches most dead links, not all.
- *Effort:* about 1 hour; adds about 5–10 s per guide.

**P5. Snapshot what the agent read.**
- *What:* re-verification depends on reaching the page later, and several official pages can't be reached by a script: `etsy.com/legal/api` returns 403, Netatmo, Oura and Procore are JavaScript-rendered, and Fortnox and Exact serve PDFs. The agent's server-side fetch could read them; our audit tools could not.
- *Fix:* store a hash of each fetched page and the quoted passage in the guide's provenance at research time, so an audit can check the quote against what the agent actually saw.
- *Why:* this moves "trust the quote" to "verify the quote against the snapshot".

**P6. Composio lookup: fall back to Composio's search.**
- *What:* `composio_toolkit` only tries slugs derived from the name the user typed (for example `amazon_sp_api`). A provider listed under a different slug would read as "not in Composio".
- *Fix:* add a fallback to `GET /api/v3/toolkits?search=`, accepting a match only on an exact name or slug.
- *Effort:* 30 min.

**P7. Date-weight community evidence.**
- *What:* Withings' "apps are reviewed before access" rests on one old openHAB forum post, and nothing records how old a community source is.
- *Fix:* capture the source date at research time; flag community evidence older than about 2 years as "may be outdated", and prefer recent reports when sources conflict.

**P8. Show evidence strength on the library card.**
- *What:* Exact Online's guide rests on 33 community citations out of 48. Its card looks the same as Procore's, which has 23 official citations out of 27.
- *Fix:* a small "mostly official / mixed / mostly third-party" marker on each card and guide header, computed from evidence statuses (no model involved).

**P9. Keep the verified guides as bundled samples.**
- *What:* live guides disappear on every Render redeploy or sleep, and the ten spot-checked guides are the best demonstration of the agent on apps Composio doesn't cover.
- *Fix:* add them to `guides/agent/`, marked "spot-checked 2026-10-30". The Withings guide was generated before 3.2, so it should be regenerated rather than hand-edited, which keeps the provenance honest.

**P10. Make the README's "retrieved" precise.**
- *What:* the README says the validator downgrades URLs that were "never retrieved". In fact a URL counts as seen if it appeared in a search result, which carries page content but may be stale (see P4).
- *Fix:* reword. Keep the claims as strong as the mechanism and no stronger.

## 4. Questions only Composio can answer

These came up repeatedly and are product questions rather than research gaps:
- **Non-standard auth.**
  - Toast uses client credentials with no user consent, and Amazon issues an LWA token per seller plus self-authorization.
  - Can a Composio custom auth config represent these?
  - Every such guide ends with this as an open question.
- **Device-bound token rules.** Netatmo's camera scopes forbid storing tokens anywhere except the user's device. A server-side broker can't comply, so Composio should say which providers it can't serve for that reason.
- **Redirect URI inconsistency.** The docs give `/api/v3/toolkits/auth/callback`, but the toolkit API's default is still `/api/v1/auth-apps/add`. This has been open since the first curated guide. Many providers match the redirect URI exactly, so it matters.

## 5. Cost of this round

| Item | Cost |
|---|---|
| 12 live guides | about $4.30 |
| Verification | No API spend; all pages were fetched directly or searched |
| Total eval spend to date | about $91 (see VERIFICATION.md) |
