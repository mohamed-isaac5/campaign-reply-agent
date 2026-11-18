# English narration transcript

AI-generated narration. Scene captions are chapter takeaways, not word-level subtitles.

## A calmer way to handle campaign replies

Campaign replies should lead to a clear next step, not another pile of messages to sort. Campaign Reply Agent brings the conversation, the business facts, and the decision into one local workspace. It helps a single operator see who is interested, what needs a human answer, and which addresses must not be contacted. This walkthrough uses fictional contacts and offline simulation. Nothing you see here sends a real email.

## Start on your own machine

Start with the source folder. Install the documented Python and Node prerequisites, then run the startup script from the project root. It installs the pinned dependencies, builds the interface, and starts two services on your machine only. Open the local address shown in the terminal. No Gmail account or model key is needed for this demo. Stop both services with Control C. The read me also explains manual startup and local tests.

## Give the agent facts, not guesses

Setup begins with a short conversation. Explain what you sell, who it is for, the facts a customer may be told, and the exact price with its currency. Then describe the campaign plan and the tone you want. Your answers are saved as you go. Refreshing the page resumes the right question, instead of silently putting an answer in the wrong field. If loading fails, a retry restores the setup before you can continue.

## Review first. Permission stays explicit.

Before saving, check the summary. Automatic replies are off by default. Notes about permission never turn themselves into permission. A separate checkbox can allow a very narrow case: a simple price-only question, using the exact recorded price. It also needs the required confidence score, an active campaign, and no stop request. Discounts, mixed questions, and unknown facts stay in review. Here, we leave that checkbox off.

## See the work that actually arrived

The campaign overview shows replies observed, interested contacts, decisions waiting, and distinct do-not-contact addresses. These are local observations, not total campaign sends or conversion rates. Fictional sample campaigns have a separate switch, so they do not quietly inflate the default totals. In simulation, each click brings in the next fictional reply. The sequence includes a price question, a discount request, a stop request, and cases that need clarification.

## Make a decision with the full context

Open the review queue to see why this reply needs you. The card brings together the recipient, the original conversation, the recorded facts, and a specific reason for review. Read those before touching the draft. The draft is editable, and an empty reply cannot be approved. In offline mode it is a rule-based suggestion, not real model analysis. If you later configure a model, its draft still needs your factual review.

## Approve the exact words

Change the draft to the words you actually want, then approve that exact reply. In this demonstration, approval only records a local simulation. The receipt and decision history make the result visible. A deliberate local failure also produces a clear error, rather than pretending the action worked. The send path saves its identity before an attempt, and uncertain outcomes are held for reconciliation. They are never blindly retried.

## New context changes the decision

An old draft should not answer a new conversation. If another customer message arrives, the earlier approval is marked stale and blocked. Review the latest context instead. A genuine stop request suppresses that address across local campaigns and cancels pending drafts. The recent repairs also separate current replies from quoted campaign history in the tested Gmail, Outlook, Thunderbird, and plaintext formats. An old unsubscribe footer should not become the customer's new instruction.

## Readable threads, even on a phone

Campaign details show the plan, observed classifications, and full threads. Open a thread with the mouse or keyboard, then follow a pending action back to the full review editor. On a small phone, the reason, status, and review link now stack instead of squeezing the explanation into a narrow column. The overview also counts one suppressed address once, even when that person appears in more than one included campaign.

## Keep the operator in control

Settings keeps the effective rule and exact price visible. Editing the business facts revokes automatic permission until you confirm it again. You can pause a campaign or stop new sending with the global switch. Worker health and unresolved attempts remain visible too. An unsupported model-provider setting is now an explicit configuration error. It cannot silently route customer text or a key to a different provider.

## What changed since the earlier demo

This version adds reliable setup refresh and retry, keyboard thread controls, readable expanded phone layouts, an accurate cross-campaign stop count, complete displayed history, and clear missing-page states. The read-only M C P tools now include unresolved send states and thread actions. Behind the interface, the repair suite covers duplicate approvals, stale drafts, recovery, exact prices, provider validation, and the tested consent formats. One hundred and fifty-three local backend tests passed.

## Optional connections, explained, not demonstrated live

For your own setup, the read me explains the optional connections. Gmail needs your Google Cloud project, a Desktop O Auth client, a Pub Sub topic and pull subscription, and permission from your test inbox. The worker then maps one label to one explicitly selected campaign. A real model needs a supported provider and your own local key. The M C P interface is a local read-only inspection process, not an email send command. These paths are implemented, but this video does not demonstrate a live account or model connection.

## A useful local prototype. Honest boundaries.

The result is a local single-operator triage prototype with explicit decisions and visible evidence. It is not a proven unattended sales system. Real Gmail, real model behavior, and email delivery have not been tested end to end. The stop list cannot control a separate campaign sender, and no parser understands every email format. Start with the included demo, inspect the source, and follow the read me. Any real-account trial needs its own controlled review before customer traffic.
