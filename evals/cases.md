# Eval cases (proposed, awaiting sign-off)

23 cases. Each runs the real pipeline (`app.pipeline.generate_guide`) for one query.

| id | query | slice | expected |
|---|---|---|---|
| github | GitHub | reference | difficulty=instant, managed=True, 3 key facts |
| gmail | Gmail | reference | difficulty=heavy_review, managed=True, 5 key facts |
| slack | Slack | reference | difficulty=self_serve, managed=True, 5 key facts |
| shopify | Shopify | reference | difficulty=heavy_review, managed=False, 4 key facts |
| hubspot | HubSpot | reference | difficulty=light_review, managed=True, 4 key facts |
| notion | Notion | reference | difficulty=self_serve, managed=True, 4 key facts |
| zoom | Zoom | long_tail | managed=True |
| salesforce | Salesforce | long_tail | managed=True |
| outlook | Microsoft Outlook | long_tail | managed=True |
| dropbox | Dropbox | long_tail | managed=True |
| linear | Linear | long_tail | managed=True |
| xero | Xero | long_tail | managed=False |
| docusign | DocuSign | long_tail | managed=False |
| pipedrive | Pipedrive | long_tail | managed=False |
| klaviyo | Klaviyo | long_tail | managed=False |
| gusto | Gusto | long_tail | managed=False |
| twitter | X (Twitter) | long_tail | managed=False |
| servicenow | ServiceNow | long_tail | managed=False |
| bexio | Bexio | obscure | managed=False |
| clio | Clio Manage | obscure | managed=False |
| openweathermap | OpenWeatherMap | edge | behaviour check |
| mercury | Mercury | edge | behaviour check |
| hubspot-url | https://www.hubspot.com/products/crm | edge | managed=True, behaviour check |

## Key facts (reference slice)

Written by Claude from the curated guides, each traceable to a fetched official source. **Not human-verified yet.** Please check these: they are the answer key.

### github
- Registering a GitHub OAuth app has no review or approval step.
- Organizations with an OAuth app access policy must have an owner approve the app before it can access org resources.
- OAuth app scopes are broad (e.g. repo grants access to all of the user's repositories), and GitHub recommends GitHub Apps instead.

### gmail
- An app in Testing publishing status is limited to 100 test users.
- Refresh tokens expire after 7 days while the app is in Testing (beyond basic profile scopes).
- Restricted Gmail scopes (e.g. gmail.readonly, gmail.modify, https://mail.google.com/) require a third-party security assessment (CASA), renewed at least annually.
- gmail.send is a sensitive (not restricted) scope: it needs verification but not a security assessment.
- Unverified apps requesting sensitive or restricted scopes are capped at 100 users.

### slack
- An app can be installed into its own development workspace without review.
- Public (unlisted) distribution to other workspaces does not require Slack review.
- Commercially distributed apps not in the Slack Marketplace are limited to about 1 request per minute on conversations.history / conversations.replies.
- Submitting to the Slack Marketplace requires the app to be installed on at least 10 active workspaces.
- The Slack Marketplace functional review can take up to about 10 weeks for new apps.

### shopify
- The distribution method (public vs. custom) cannot be changed after it is chosen.
- Custom distribution (install link to one store or one Plus organization) does not go through App Store review.
- Public distribution through the Shopify App Store requires app review.
- Accessing protected customer data in a public app requires a separate review.

### hubspot
- An unlisted marketplace-distribution OAuth app is capped at 25 installs until it is reviewed and listed.
- A privately distributed OAuth app can be installed in up to 10 allowlisted accounts.
- Marketplace listing requires at least 3 active installs.
- HubSpot does an initial review of a listing submission within 10 business days.

### notion
- The installation scope (any workspace vs. selected workspaces) is set at creation and cannot be changed.
- A public connection can be used through OAuth without being listed on the Notion Marketplace.
- Notion reviews Marketplace listings, with a reply in roughly 5-10 business days.
- Users grant access page by page rather than through OAuth scopes.

## Edge-case behaviour checks

- **openweathermap** (`OpenWeatherMap`): States that this provider uses API keys, not OAuth, so no OAuth app is needed, rather than inventing an OAuth registration flow.
- **mercury** (`Mercury`): Says which product it chose (several companies are named Mercury) and produces a guide for a real developer-facing product with an OAuth API.
- **hubspot-url** (`https://www.hubspot.com/products/crm`): Resolves the URL to HubSpot and produces a HubSpot guide.
