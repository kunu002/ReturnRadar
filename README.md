# ReturnRadar

A working MVP with private merchant workspaces and role-based access for sellers to reduce avoidable product return requests through evidence, verified improvements, and follow-up reviews. The customer-facing tagline is **Fewer returns. Happier customers.** Built from the supplied `ReturnRadar.md` brief.

## Try the live app

**[Open ReturnRadar](https://returnradar-ai-7d4a2b.web.app)**. Sign in with your own Google account and choose **Explore demo** to create your private sample store. No store credentials are needed to explore the demo.

1. Open **Patterns** and read the customer feedback behind a recurring product issue.
2. Open **Action center** to review product-specific improvements and buyer guidance.
3. Open **Product Reviews** to plan a change and follow up on its results.
4. Use **Ask Radar** for concise explanations of the evidence and suggested next steps. **Run analysis** generates fresh Gemini insights.

The demo includes 18 products across nine categories, 256 return records, sales records, draft improvements and three planned reviews. Sample records are fictional; the review plans do not claim measured reductions in returns. Each reviewer gets an independent copy. From **Settings**, start a separate merchant workspace or switch between your workspaces. Matching workspace names never grant access to another merchant; team access requires an invitation.

## Run locally

Requires Python 3.11+ and Node.js 22+. On a fresh clone, follow the installation steps below first.

```bash
bash scripts/dev.sh
```

Open **http://127.0.0.1:5173**. Choose **Create account** to create a merchant workspace, or **Explore sample workspace** to view the existing demo. The API runs on port 8000; its interactive documentation is at **http://127.0.0.1:8000/docs**. The script creates `.env` from `.env.example` if it is missing.

Development automatically reloads Python files in `backend/`. After editing `.env`, stop the running app with Ctrl+C and start it again so the new settings are loaded.

If both services already run, `scripts/dev.sh` prints the existing URL and exits. If only one service is running, it starts the missing one. The frontend stays on port 5173; an occupied port belonging to another service or an unhealthy API produces a clear error instead of silently switching ports. Stop the original running instance before restarting to load changed environment settings.

For a fresh installation:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm ci
cp .env.example .env
bash scripts/dev.sh
```

To serve the production build through the Python server:

```bash
npm run build
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --env-file .env
```

## Included workflows

- Dashboard with time/source filters, daily return-item counts, complaint shares, and emerging signals backed by evidence IDs and minimum samples.
- Searchable, paginated return inbox; product investigations with original feedback and listing-history limitations.
- CSV import with complete-file validation, separate order/item/variant/return/refund IDs, source isolation, and idempotent updates by return-item ID. Download a sample CSV inside the app.
- Product-level action proposals; editable listing suggestions, inspection steps, and fit-check questions, with persistent approval/dismissal decisions.
- Public demo product pages, for example `/?product=p1`, using approved measurements and deterministic Python fit checks. Buyer dimensions are not stored. Negative/missing measurements are rejected; unavailable measurements return `cannot_verify`.
- Integration status and retryable processing history.
- **Ask Radar**, a Gemini chatbot for questions about selected return data, why signals appeared, and how to use ReturnRadar. Answers show concise points, supporting feedback and follow-up questions. Provider receipts stay private. Chat cannot change records or approve actions.
- Recoverable event storage, atomic processing leases, workspace-scoped Cloud Tasks dispatch, and a separate private worker role. A legacy Shopify adapter remains in development code.
- Optional Gemini classification and comparisons with listing text and supported Shopify CDN images. Structured output is validated against supplied evidence IDs and exact listing quotations. AI observations are labelled hypotheses for merchant review. Python computes all counts/shares.
- Email/password accounts locally, optional Firebase Google sign-in, isolated merchant workspaces and API-enforced Owner/Admin/Analyst/Viewer roles, Firestore/Cloud Storage adapters, Docker image, and Firebase Hosting configuration.

New users explicitly choose **Explore demo** or **Start my workspace**. Explore demo creates their own private sample workspace with **18 products across nine categories, 256 return records, 1,336 orders, 30 draft improvements/buyer guides and three planned product reviews**. Start my workspace opens an empty merchant workspace for store connections or CSV imports. Matching workspace names never grant access; teammates join by invitation. Settings lets returning users explore their own demo or open their merchant workspace without mixing the datasets. The sample banner, guided workflow and suggested Ask Radar questions help reviewers investigate an issue, choose an improvement and plan a review. Sample reviews never claim a proven reduction in returns, and seeding does not call Gemini or fabricate a successful analysis receipt.

The anonymous shared sample workspace remains a local-development feature and is disabled in production. Its legacy fixtures include an illustrative approved fit check. Signed-in private demos start with drafts and planned reviews. Gemini analyses all sources, including synthetic data, when a key is configured. Without a key, local grouping remains available. Synthetic evidence keeps its demo label.

Try the primary flow: **On your radar → Everyday Backpack / Laptop fit → Next steps → Review action → verify measurements → Approve & publish → Preview**. The seeded dimensions are illustrative; do not approve them for a real product without measuring it.

## Product actions and buyer guidance

**Action Center** supports combined product, issue, action-type, status and text filters, plus a source selector and reset controls. Dashboard draft-review counts and published buyer-check counts use the selected source. Time ranges select feedback and patterns; actions and live checks represent the current state for that source. Older workspace analysis metadata stays labelled separately from selected-source figures.

The overview previews four patterns. **See all N patterns** opens the full list, where product/issue searches and emerging/recurring filters apply to the selected source and period.

**Propose actions** on a product prepares issue-specific improvements and an appropriate buyer-guide draft. A product with no qualifying pattern can still use **Prepare buyer guide**. Bag dimensions remain a verified, single-variant laptop-fit calculator. Other products get merchant-approved checklists: clothing/shoe sizing, device compatibility/setup, kitchen use/care, home dimensions/delivery, beauty packaging/care, fitness dimensions/materials, or included toy pieces. Unknown products receive a neutral facts checklist. These guides never invent measurements or automatically guarantee suitability.

Owners/Admins edit the buyer instructions/checklist and verify the facts before **Approve & publish**. Draft, dismissed or unverified guides stay out of public responses. Published guides expose only approved product facts/instructions; customer complaints and internal evidence remain private. The sample workspaces have buyer-guide drafts ready to review, and existing merchant decisions are preserved. When a bag has both approved check types, its calculator remains the default and the guide’s preview opens the selected checklist explicitly.

**Buyer guidance on actual product pages** is marked **Coming soon** in Data Connections. Current publishing makes the ReturnRadar buyer preview available; Shopify/WooCommerce storefront placement remains planned. Store connections only read data.

## Merchant accounts and roles

The merchant workflow is **Create account → connect Shopify/WooCommerce or import CSV → update store data → run analysis → investigate → plan a product review → apply and follow up**. Each store connection belongs to its merchant workspace. The legacy global development-store adapter is separate from merchant connections; merchant sessions cannot access its credentials.

| Role | Read records and evidence | Import, analyse, chat, propose | Approve changes | Manage team |
| --- | --- | --- | --- | --- |
| Owner | Yes | Yes | Yes | Yes, including Admins |
| Admin | Yes | Yes | Yes | Analysts and Viewers |
| Analyst | Yes | Yes | No | No |
| Viewer | Yes | No | No | No |

Open **Settings** to create an invitation for a teammate's email and role. Opening a teammate’s invitation while signed in as the inviter correctly denies acceptance; the acceptance screen shows the current account and provides **Use a different account** without losing the invitation link. Sign in or create an account with the invited email. Demo selection and revoked access to an old workspace cannot prevent accepting an otherwise valid new invitation. Share the generated link yourself; no email is sent. Invitations expire in 48 hours, are single-use, and require the matching account email. Team access can be changed or removed; the next API request checks the current membership. Owners cannot remove themselves or change their own role. Users who belong to multiple workspaces can switch them in Settings. Each account can create one merchant workspace and one separate private sample workspace, alongside any workspaces joined by invitation; owner transfer is outside its scope.

Each authorized request uses a workspace-scoped data store, including imports, returns, products, orders, actions, Gemini reports/cache/receipts, processing history and audit records. Worker tasks carry the workspace ID. Account identities, opaque-session hashes and invitations are stored separately from merchant data. Existing unscoped records remain in the sample workspace; they are never automatically assigned to a new merchant.

Local setup uses `AUTH_PROVIDER=local` and SQLite. Registration and sign-in accept passwords from 8 to 15 characters. Passwords use salted scrypt (`N=32768`, `r=8`, `p=3`); opaque sessions use HttpOnly, SameSite=Strict cookies, expire after eight hours, and are revoked on sign-out. Session tokens and invitation tokens are stored as hashes. The configured frontend origin is checked for browser mutations. Password parameters follow an [OWASP scrypt configuration](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

For hosted merchant access, configure `AUTH_PROVIDER=firebase`, `DEMO_MODE=false`, Firebase Google sign-in and the server's Firebase configuration/credentials. Firebase tokens are verified with revocation checks and verified email is required; membership roles still come from the server database. [Firebase token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens). On first sign-in, create a workspace or accept an invitation. Local email accounts do not send verification/reset emails or offer password recovery; use Firebase for those identity-management requirements. Auth/chat/fit rate limits are process-local and require a shared limiter before scaling beyond one API instance.

Buyer preview links include `workspace=WORKSPACE_ID` and expose product facts, approved fit dimensions and verified buyer guides only. Private complaints, orders, members and model receipts are not in public buyer responses.

## UI review account and Data Connections

A local review account is provisioned in the current workspace as `reviewer@returnradar.test` with its own **UI Demo Merchant** workspace. The supplied password is shared in the task response, not committed here. It has Owner access, 18 synthetic products across bags, clothing, footwear, electronics, kitchen, home, beauty, fitness and toys; 256 sample feedback records; suggested improvements; two review plans; and one synthetic approved fit fixture. Sign in normally; these fixtures are isolated from other merchant workspaces. The initial seeded classifications are fixtures; only a real completed analysis creates Gemini call receipts.

To create another local test merchant explicitly:

```bash
.venv/bin/python scripts/create-demo-account.py --email your-test@example.com
```

The script prompts for an 8–15 character password. Re-running it preserves existing test-account passwords and review edits; it refuses to overwrite a regular merchant account. Do not enable a test account automatically in production.

To prepare sample data for an existing hosted merchant workspace, use `scripts/seed-workspace-samples.py --project PROJECT --workspace WORKSPACE_ID --owner ACTIVE_OWNER_ID`. It previews the changes; add `--apply` to create the missing fixtures. Verify the intended workspace and its active owner first. The fixtures contain 18 products across nine categories, 256 synthetic feedback records, synthetic orders, draft buyer guides and three planned reviews. Sample IDs are separate from imported store IDs. Existing decisions, real records, membership and completed analyses are preserved; no guidance is automatically published and no AI call receipt is fabricated. Run analysis afterwards to generate real findings.

**Data Connections** is the source entry screen. The working return CSV import accepts a declared export platform (generic, Shopify, WooCommerce or other), stores that provenance and records workspace-scoped import history. Map native exports into the supplied template first; a native Shopify order CSV does not automatically supply every returned item's reason/status. The platform label declares CSV provenance, and does not represent an authenticated store connection. [Shopify order exports](https://help.shopify.com/en/manual/fulfillment/managing-orders/exporting-orders).

Shopify and WooCommerce now provide read-only store connections, verification, data updates, reconnection and disconnection. Imported store data and connection credentials remain workspace-scoped. Credentials are encrypted; local development creates `data/connector.key` (ignored by Git, mode 600), while hosted deployments require `CONNECTOR_ENCRYPTION_KEY` supplied through Secret Manager. Keep the key stable to preserve saved connections. Disconnecting removes connection credentials but preserves imported records and review history.

For Shopify, enter the store’s `your-store.myshopify.com` address from Shopify **Settings → Domains**. Do not use `dev.shopify.com/...` app settings or `admin.shopify.com/...` page links. Choose either an installed app’s **Store access token** with product/order/return read access, or **App credentials** for an app and installed store in your own Shopify organization. The latter uses the Client ID and Client secret from the Dev Dashboard app Settings page; the server obtains and refreshes a temporary store access token. This grant does not authorize unrelated merchants’ stores. [Shopify client credentials](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant). Each unrelated merchant must supply its own authorized store connection; a public multi-store OAuth installation flow remains outside this implementation.

WooCommerce uses a store HTTPS address and read-only consumer key/secret generated under **WooCommerce → Settings → Advanced → REST API**. Store responses are retrieved over checked public HTTPS destinations with redirects blocked. Shopify imports products, recent orders and recorded return requests; WooCommerce imports products, recent orders and refunds. Refund feedback is labelled separately and never presented as proof of physically received returns. Updates cover orders created in the last 60 days and use stable source/store/item identifiers to avoid duplicates. [WooCommerce refund records](https://developer.woocommerce.com/docs/apis/rest-api/v3/order-refunds/).

**Product Reviews** assigns an active teammate to one product issue, a customer-facing goal and one verified change. Analysts can plan reviews; Owners/Admins record application and review decisions. Teams can continue monitoring, keep a change, revise it with the previous lesson carried forward, or close the review. Check-in history and applied dates are retained.

Results compare equal purchase windows with the same allowed time for return requests from each purchase. A full comparison waits until both cohorts have completed follow-up, a successful store update covers both purchase periods after follow-up, and at least five distinct orders and five sold units exist on each side. Under twenty orders per period is marked as an early signal. Missing quantities, unmatched variants/items, unknown dates or incomplete history prevent a misleading rate. Request counts are distinct per order/return; requested item quantities and their share of sold units remain separate, so rising request counts are not hidden by falling percentages. Sales-volume changes are descriptive, not proof of a change’s effect.

WooCommerce orders/refunds alone do not establish a complete return-request feed; review rates remain unavailable for that feed while refund feedback can still inform improvements. Completed review results freeze the measured data and coverage; concurrent updates cannot overwrite a completed result or check-in history. Repeat purchases/customer loyalty require customer history not imported by this implementation, and lower product returns or higher sales are not automatically attributed to a product change.

Merchant screens present product insights, customer feedback, review steps and store-update status. Provider models, request identifiers and token counts remain internal diagnostics.

## Gemini

To check your key and configured model directly, without running the app:

```bash
.venv/bin/python scripts/check-gemini.py
```

This reads `.env` (existing shell variables take precedence) and sends one short text request. Add `--model MODEL_ID` to test another model without editing your settings, or `--structured` to check the same classification response schema used by the application. It prints success or a redacted error, never the key.

Set `GEMINI_API_KEY` and optionally `GEMINI_MODEL` (default `gemini-3.1-flash-lite`), then restart with `bash scripts/dev.sh`. Open **Ask Radar** and ask a question; chat uses Gemini even when discussing synthetic demo data. Answers display concise points and supporting customer feedback. Provider receipts are stored privately for diagnostics; they are not rendered in the merchant UI.

The dashboard and chat sidebar show completed analysis and its recency in functional terms. All sources, including synthetic demo records, use Gemini when a key is configured. Run analysis to replace older local results; unchanged Gemini results are reused from the cache. Without a key, local grouping remains available. Gemini failures are visible and do not become successful local AI answers.

Requests prompt the model for JSON matching a supplied schema; Python validates the returned structure and cited evidence. Provider-native JSON schema enforcement is not enabled. Analysis additionally verifies exact quotations against listing descriptions or claims. Unsupported optional comparisons are omitted; validated classifications remain available. Schema checks cannot guarantee the interpretation is correct; merchant review remains necessary. Analysis caches incorporate product/listing data, source records, prompt version, provider, and model. The analysis uses product descriptions and grouped complaint text. Repeated feedback is grouped to reduce model requests; validated batches and product results are reused after transient failures. Analysis model requests have a 90-second deadline and bounded retries for temporary failures. Ask Radar uses a 15-second provider timeout and at most two attempts to stay within the frontend proxy limit.

Chat returns one to five bullet points within 150 words, using saved Gemini listing hypotheses, calculated metrics and a bounded sample of private seller evidence for the selected date range/source. Questions, recent conversation context, and selected data are sent to Gemini. ReturnRadar keeps chat content in page memory only; navigating away clears it. Stored receipts contain call metadata rather than questions or answers. The API allows six chat requests per seller per minute and one concurrent chat call per process. These limits are process-local; use a shared limiter before scaling.

Chat context retains full filtered totals while prioritizing representative products and feedback within its size budget. Saved analysis citations are restricted to that same selected evidence. Short internal evidence references and a restricted answer schema prevent mixing product or historical IDs with current feedback citations; validated answers resolve those references back to the original records. Review summaries follow the selected source and omit private actor details. Invalid evidence is rejected without inventing supporting feedback.

## Firebase and Google Cloud deployment

The hosted app is available at **https://returnradar-ai-7d4a2b.web.app**. It uses verified Google sign-in, Cloud Run and Firestore. Follow [the deployment guide](docs/DEPLOYMENT.md) for project setup, secret provisioning, builds, updates and hosted acceptance checks. GitHub publishing remains a separate manual step.

The deployment passed Cloud Build's backend checks and frontend build, hosted health/readiness and private-worker access checks, and a live Cloud Tasks/Gemini analysis of five temporary sample records. Those verification records were removed. New merchants create their own workspace after signing in; local accounts and database contents are not part of this release.

| Responsibility | Google product |
| --- | --- |
| React frontend, HTTPS and `/api` routing | Firebase Hosting |
| Merchant API and separate private analysis worker | Cloud Run |
| Merchant records, memberships and processing history | Firestore |
| Merchant Google sign-in | Firebase Authentication |
| Analysis and store-update delivery | Cloud Tasks |
| Gemini and saved-connector encryption credentials | Secret Manager |
| Container builds and images | Cloud Build and Artifact Registry |
| Optional private CSV archive | Cloud Storage |
| Service and job diagnostics | Cloud Logging |

Firestore stores data; Firebase Hosting serves the frontend. The browser uses the same-origin `/api` rewrite to `returnradar-api` in `asia-south1`; Firebase Authentication handles verified Google sign-in. The worker is a separate, private Cloud Run service invoked by the Cloud Tasks dispatch identity. Secrets are supplied only at runtime, with no service-account key files in the application image.

Hosted startup validates production settings before opening the database. Production disables the anonymous shared sample workspace, SQLite and local sign-in, and requires Cloud Tasks for background work. Signed-in users can explicitly create a separate private demo or start an empty merchant workspace. Local development data and the UI review account are not copied into a hosted deployment. The stable connector encryption key is supplied through Secret Manager; merchants configure their store connections inside their own hosted workspaces.

Firebase Hosting has a [60-second request timeout](https://firebase.google.com/docs/hosting/cloud-run). Analysis and store updates use the private worker; chat uses a shorter provider timeout. Cloud Tasks dispatch and the worker request timeout are aligned for long-running analysis. Client-facing Firestore and Storage rules deny direct access; the backend uses its service account.

Public fit checks require a **single-variant product**; approving one measurement set for a multi-variant Shopify product is rejected. Rate limiting remains per API process, so the deployment limits the API to one instance. Add a shared limiter before scaling beyond that. The deployment guide includes live checks for Google sign-in, invitation acceptance, merchant isolation, store updates and completed Gemini analysis; these require your cloud project and store access.

## Validation

```bash
.venv/bin/python -m pytest -q
npm run build
npx playwright install chromium
npm run test:e2e
```

Account tests cover password/session handling, member roles, merchant isolation, invitation expiry/email binding, role escalation denial, immediate revocation, scoped jobs and public previews, and optional private demos. Browser tests exercise signup, returning login, CSV imports, invitations, Viewer controls, independent sample copies and switching between sample and merchant workspaces. Browser tests start their own API/frontend on ports 8001/5174 with a unique, isolated database. Screenshots are written to ignored `artifacts/`. Tests also cover product investigations, approvals, fit boundaries, filtering, mobile layout, chat evidence and error handling. Automated tests mock Gemini.

The deployed release passed 206 backend tests, a production frontend build and 18 browser workflow checks. Cloud checks verified website availability, Firebase sign-in configuration, rejection of unauthenticated merchant access, private worker access, separate demo copies and preservation of data when switching. Fresh sample stores took approximately 4.5 seconds to prepare; temporary verification records were removed. The hosted Shopify connector and live Gemini analysis/chat were also verified separately. These checks do not establish a causal reduction in returns or increased sales.

Optional browser WebMCP tools expose the current summary and open an existing investigation, when the browser supports `document.modelContext`. They use the same visible state and do not approve or publish actions. Native experimental browser support is not required for the app.

## Budget and remaining scope

Track actual billing against the proposed ₹1,000 AI / ₹500 infrastructure / ₹500 contingency allocation. The ₹2,000 figure is not an enforceable bill cap. Cloud services scale to zero; instances, retries and queue concurrency are limited. Product reviews already compare mature purchase cohorts around an applied change. Savings, causal effects, supplier/batch inference, repeat-purchase retention and multi-store OAuth remain outside this implementation. Buyer guidance on Shopify/WooCommerce product pages is marked Coming soon.

The original checkpoint requiring a real fulfilled order/return and signed webhook remains credential-dependent. CSV, product investigation and merchant approval/fit workflows are runnable locally now.

## Demo photograph

`public/backpack.jpg` is an illustrative [Unsplash photograph](https://unsplash.com/s/photos/bagpack) using the image asset `photo-1550253594-356b2f788907`, available under the [Unsplash license](https://unsplash.com/license). It does not verify any synthetic product's dimensions, materials or color.
