# Campaign Reply Agent

Local-first campaign reply triage with explicit boundaries, a review queue and an audit trail. All bundled contacts, conversations and campaign results are fictional.

**Status:** local prototype with offline simulation and fake integration-contract tests. Real Gmail, real model behavior and actual email delivery have **not** been tested end to end. A passing test suite is not a production-readiness guarantee. Do not point this at customer traffic before a controlled test-inbox review.

## Quick start

The startup script targets Linux/WSL or a Unix shell with Bash 4.3+. Use Node **20.9+** and Python **3.11+** (3.12 recommended), plus an internet connection to install dependencies. The local test environment also ran the suite on Python 3.10.12, but the installed Google libraries warn that Python 3.10 is no longer supported.

```bash
./run.sh
```

The script creates `.venv`, installs the pinned Python dependency snapshot, runs `npm ci` using the committed UI lockfile, builds the UI and starts both local servers:

- UI: http://127.0.0.1:8792
- Engine: http://127.0.0.1:8791

Both bind to loopback only. Do not expose these ports to a network: this is a single-operator local app, not a multi-user authenticated service. Stop with Ctrl+C. Dependency installation needs internet; the offline simulator needs no accounts or keys once installed.

### Try the demo

1. Open **Chat**. Answer the business, facts, exact price, campaign plan, tone and permission-notes questions.
2. Review the summary. Automatic replies are **off by default**, regardless of what the notes say. A separate explicit checkbox can allow simple price-only replies using the exact recorded price.
3. Open **Campaigns** and click **Simulate next inbound reply**. The bundled sequence covers a price question, discount, opt-out, auto-reply, later request, unknown question, unclear reply and a duplicate.
4. Open **Review queue**. Read the recipient, reason, recorded facts and complete local conversation. Edit the exact draft, then approve or reject. In demo mode approval only records a local simulated reply.
5. Check the visible decision receipt and history. Handle follow-up requests in your own campaign tool, then mark them handled here. Nothing is scheduled by that button.
6. Ask Chat for today's summary to get a fresh snapshot. The snapshot time and live queue count are separate. Fictional sample campaigns are excluded from the Campaigns view/totals unless selected.

A simulation label is not a real AI result or a real email send.

## Effective rules

- Permission notes are stored as notes, **never executed as authority**. Only the dedicated confirmed boolean controls automatic price replies.
- The automatic scope is deliberately narrow: an exact supported price-only phrase, recorded positive amount and explicit currency, a validated `interested` classification, at least 85% confidence, no mixed/discount request, an active campaign, no suppression and the kill switch off. Unknown phrasing goes to review, even if it might mean the same thing.
- Exact prices accept supported ISO codes such as `49.99 USD`, `1299.50 EGP` or `USD 1,299.50`. Bare amounts, currency symbols with ambiguous country, ranges, extra payment terms and excessive decimal places are rejected. Put billing terms in the business facts, not the exact-number field.
- Editing a business/onboarding answer revokes automatic permission. Reconfirm the rule in Settings after reviewing the changed facts. Stale automatic drafts cannot use changed facts or grants.
- The kill switch and campaign pause block both automatic and newly approved sends. They cannot recall an outbound request that already passed the send gate and is in flight.
- An opt-out suppresses the address across local campaigns, cancels pending drafts and blocks later approvals. This app cannot stop an unrelated external campaign sender: stop it in that tool too.
- The offline classifier is deterministic English/Arabic keyword simulation, not a reliable general language model. Ambiguous cases need review. Incoming multilingual content remains readable; UI and generated reply templates are English.

## Durable processing and send outcomes

Inbound work has a separate durable processing state and a timed lease. Classification/action planning can resume after interruption; action creation and inbound completion are committed together. The API's local recovery loop and the worker both recover incomplete inbound work.

Outbound actions use a durable compare-and-set claim before I/O. The exact reviewed body and RFC Message-ID are saved before the send call. Concurrent approvals cannot both claim one action. A crash, failed request or unknown response never becomes an automatic second attempt.

An unresolved attempt remains visible in Review queue. Reconciliation requires the exact saved Message-ID in our outbound Gmail thread. Similar body text or an absent result does not prove the outcome. If no exact evidence can be recovered, the item remains unresolved and needs manual investigation. There is intentionally no blind retry button. Old records without an outbound identity cannot be automatically cleared. A draft is also bound to its triggering inbound message: newer non-automatic customer context makes the old approval stale and blocks it.

A successful Gmail response is labeled **Gmail accepted**, not delivered/read. Counts include both automatic and manually approved outcomes, on the day the decision/send completed. Auto-replies and historical inbound context are excluded from human arrival counts. Do-not-contact counts are distinct addresses, not thread counts. The Campaigns overview deduplicates across all campaigns included by the sample-data switch. Decision history shows up to the latest 50 records without a hidden 12-row cutoff.

## Drafting and model setup

Without a model key, drafts are transparent rule-based suggestions. They use the current customer's intent, name and recorded facts where applicable. They do not invent discounts, support terms or commitments.

With a configured model, the app requests a context-aware **review-only** draft using the business facts, tone and full local thread. Output shape and referenced fact keys are validated. This is not an independent factual audit: a model can still write an unsupported claim. The UI asks the operator to verify every claim before approving. Model drafts never bypass the automatic rules.

```bash
cp .env.example .env
# Edit locally. Do not paste credentials in chat or commit .env.
# LLM_PROVIDER=openai or anthropic
# LLM_API_KEY=your-own-key
# LLM_MODEL=your-selected-model
```

`run.sh` loads `.env`. If starting commands individually, load it in that shell first:

```bash
set -a
. ./.env
set +a
```

Use **Settings > Test configured model connection** for a basic request check. It does not certify reply quality or the entire pipeline. Reply text, campaign facts and thread context are sent to the selected provider in real mode. Provider usage may cost money; review that provider's current terms yourself before enabling it.

## Optional Gmail setup (implemented, not live-tested)

**Use a dedicated test inbox and keep automatic permission off first.** You supply your own Google Cloud project, credentials and account consent. This project contains no real account credentials.

1. Enable Gmail API and Cloud Pub/Sub in your Google Cloud project. Create a topic and a **pull** subscription. Grant Gmail's publisher service account `gmail-api-push@system.gserviceaccount.com` publish rights on that topic. The topic project must match the project executing the watch request.
2. Create an OAuth **Desktop app** client and save its JSON at `data/google_credentials.json` (gitignored). Configure the OAuth consent screen/test users as required for your project.
3. Run `.venv/bin/python backend/setup_gmail.py`. Google's consent flow writes the token to `data/gmail_token.json`.
4. Configure Application Default Credentials for the Pub/Sub subscriber with the permissions your subscription needs. Gmail OAuth and Pub/Sub application credentials are separate. If using `GOOGLE_APPLICATION_CREDENTIALS`, it must be a local path to your own credentials, never a committed file.
5. Set the Gmail label name or ID, topic and pull subscription in your local `.env`. Set `GMAIL_ENABLED=1` only after reviewing the setup. Load `.env` in the worker's shell.
6. Find the intended campaign ID in the local Campaigns URL, then explicitly select it:

```bash
.venv/bin/python backend/worker.py --campaign-id 2
```

One worker configuration maps **one label to one chosen campaign**. Do not use the same label for unrelated campaigns. The app does not infer which campaign owns arbitrary inbox mail.

The worker resolves the configured label to a Gmail label ID, reconciles an existing checkpoint before watch renewal, and initializes a missing checkpoint from the watch response. It renews the watch daily. Pub/Sub acknowledgements and checkpoint advancement happen only after processing completes. If history expires, recovery reads every listing page using a checkpoint captured before the listing; recovered historical events are review-only. Failures back off and appear in Settings. A stale worker heartbeat needs attention, even if its last cycle was healthy.

Each Gmail conversation is keyed by its Gmail thread ID, not merely the contact address. Full MIME bodies and source timestamps are read, and thread history is synchronized **before** classification and again before a send gate. Sent/draft messages do not enter the inbound reply pipeline. Missing full bodies or changed thread recipients block processing rather than substituting a snippet or guessing a destination. Supported outbound replies include the original thread ID and RFC threading headers.

Official setup/contract references checked during this repair round:
- https://developers.google.com/workspace/gmail/api/guides/push
- https://developers.google.com/workspace/gmail/api/guides/threads

OAuth scopes are account-wide; the campaign-label restriction is enforced by this app, not a Google-enforced mailbox sandbox. Tokens are local files, not a secure multi-user secret store. A future live setup may have Google Cloud costs and requires its own consent, permissions and operational review.

## Run components manually

After installing dependencies and loading `.env` if needed:

```bash
.venv/bin/python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8791
# Separate terminal:
cd frontend && npm ci && npm run build && npm start
```

Optional configuration is documented in `.env.example`. `CRA_DB` can point to a separate local database for tests/demos, `CRA_SEED=0` disables the extra sample campaign, and `CRA_DIGEST_HOUR` selects the daily snapshot hour in the host machine's local timezone.

## Tests and local acceptance

```bash
.venv/bin/python -m pip install -r backend/requirements-lock.txt
.venv/bin/python -m pytest backend/tests -q
cd frontend && npm ci && npm run build
```

The repair suite covers permission refusal and explicit grants, decimals/currencies, confidence and mixed intent, duplicate inbound events, simultaneous approvals, later opt-outs, exact edited text, crash recovery, uncertain-outbound no-retry, Gmail entry/full-body/context order, pagination, checkpoint ordering, schema validation, model-draft fallback and metrics. All external sends/provider calls in these tests are fake.

For the isolated browser acceptance suite, stop the regular app first, then run:

```bash
cd frontend
npx playwright install chromium
npm run test:ui
```

It builds and starts both servers against a temporary DB with real Gmail/model access forced off, saves screenshots/results in a temporary output folder, and stops its own servers on exit. Set `CRA_TEST_PYTHON` to an installed Python interpreter if not using the project `.venv`.

See [`REPAIR-NOTES.md`](../REPAIR-NOTES.md) for the repair acceptance checklist and the tested-vs-unverified boundary. Local browser checks exercise onboarding, visible failure responses, empty drafts, receipts and all five pages at 320/390/1280 widths. Those checks do not replace live provider/account tests.

## MCP interface

```bash
.venv/bin/python backend/mcp_server.py
```

The local stdio interface exposes campaigns, review queue, daily summary and conversation details. It does not expose a send tool or grant send authority. It is a small hand-written protocol implementation, not a certification of compatibility with every MCP client.

## Before any real use

Back up local data, use a test inbox, leave automatic permission off, verify full thread context and destinations, test opt-out/pause/kill behavior, test a failed/uncertain send and its reconciliation, and inspect the actual outcome in Gmail. Only then consider a deliberately limited rollout. Security is a layered goal, not an absolute guarantee that a model or local machine cannot be compromised.

This package is a local review artifact. No repository publication or hosted product deployment is implied.

## Final-review repairs

- Only `openai` and `anthropic` are valid model providers. An unsupported value (or an API key without a provider) is a visible configuration error. No HTTP client/model request is created in that state; classification stays review-only. No typo fallback to another provider.
- Consent and classification use the newest authored text, while full text remains visible in review. Conventional `>` quotes, reply headers, forwarded-message separators and common HTML quote containers are separated. HTML-only MIME conversion preserves quote markers. Explicit current stop requests still suppress the address across campaigns; empty/ambiguous authored text and unmarked footer instructions go to review instead of becoming confirmed opt-outs. Email formats and multilingual phrasing vary; this is not a guarantee of perfect parsing or consent interpretation.
- Chat reload restores saved answers, the current question or the unconfirmed summary. Inputs wait for state to load. Campaign answer edits update its displayed name, goal and parsed plan in the same transaction as the saved summary. Comma-separated first segments supply the campaign name; goal/name preambles are not steps.
- Campaign thread disclosures are keyboard buttons with expanded-state metadata. Expanded action rows stack on phones. Missing campaigns and failed loads have explicit states and a way back.
- MCP `get_review_queue` returns pending, sending and uncertain actions with status. `get_thread` includes messages and actions. These are read-only inspection tools, not send commands.
- Browser acceptance now covers onboarding refresh and summary restoration, expanded pending-action readability and keyboard operation at 320/390/1280, all 15 fixture history rows, missing/failed campaign states and invalid-provider status. Screenshots still need human pixel inspection; width checks alone do not prove readability.
