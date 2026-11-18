"""Live background worker. Runs ONLY with GMAIL_ENABLED=1 and the operator's own
Google Cloud app + local OAuth token. Not yet validated against a live account.

Responsibilities:
- Initialize the history checkpoint from the watch response ONLY when none exists;
  an existing checkpoint is reconciled first, never overwritten (no skipped backlog).
- Renew the Gmail watch daily after reconciliation (Google expires it after 7 days).
- Pull Pub/Sub notifications without blocking the event loop; acknowledge only
  after the matching history has been processed (at-least-once, idempotent engine).
- Reconcile via history.list as the safety net for missed notifications; if the
  checkpoint expired (Gmail 404), recover with a full label sync, not a silent skip.
- Sync outbound-only thread context from Gmail before processing new replies.
- Survive external errors: bounded exponential backoff and visible health in
  settings (worker_health), never a dead silent loop.
- Process every new message through the same engine pipeline, idempotently.
"""
import asyncio
import json
import os
import time

import db
import engine
import gmail_client

POLL_SECONDS = int(os.environ.get("WORKER_POLL_SECONDS", "30"))
RENEW_SECONDS = 24 * 3600
TOPIC = os.environ.get("PUBSUB_TOPIC", "")
MAX_BACKOFF_SECONDS = 900


def _health(ok, error=None, failures=0):
    db.set_setting("worker_health", json.dumps({
        "ok": ok, "error": error, "consecutive_failures": failures,
        "at_unix": time.time(), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False))


def _ensure_checkpoint():
    """Renew the watch. The stored checkpoint moves ONLY forward through
    processing; the watch's historyId initializes it solely when unset."""
    resp = gmail_client.renew_watch(TOPIC)
    db.set_setting("gmail_watch_expiration", str(resp.get("expiration", "")))
    if not db.get_setting("gmail_history_id") and resp.get("historyId"):
        db.set_setting("gmail_history_id", str(resp["historyId"]))


def _sync_thread_context(local_thread_id, gmail_thread_id):
    """Insert any thread messages we do not have yet, both directions, so the
    review page and the classifier see the whole conversation. Historical
    inbound is CONTEXT ONLY: marked inbound_context, excluded from every
    arrival/classification metric, and never run through the reply pipeline —
    only a genuinely new reply (arriving via history/sync as new) is processed."""
    for msg in gmail_client.fetch_thread_messages(gmail_thread_id):
        if not msg.get("body_complete", True):
            raise ValueError("Historical thread body unavailable; context sync incomplete")
        if msg["direction"] == "in" and engine.llm.has_optout(msg["body"]):
            email = msg.get("contact_email", "").lower()
            if email:
                with db.transaction() as c:
                    c.execute("INSERT OR IGNORE INTO suppressions VALUES (?,?)", (email, time.time()))
                    c.execute("UPDATE threads SET do_not_contact=1 WHERE lower(contact_email)=?", (email,))
                    c.execute("UPDATE actions SET status='cancelled',reason='Cancelled after contact opted out' WHERE status='pending' AND thread_id IN (SELECT id FROM threads WHERE lower(contact_email)=?)", (email,))
        exists = db.q("SELECT id FROM messages WHERE external_id = ?", (msg["external_id"],), one=True)
        if exists:
            continue
        if msg["direction"] == "out":
            cls, why = "outbound_context", "Outbound context synced from Gmail"
        elif msg.get("auto") or engine.is_auto_message(msg["body"], msg.get("contact_email")):
            cls, why = "auto_reply", "Historical automatic reply, skipped"
        else:
            cls, why = "inbound_context", "Historical inbound context synced from Gmail, for display only: not processed and never replied to"
        db.run(
            "INSERT OR IGNORE INTO messages (thread_id, direction, body, classification, class_reason, external_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (local_thread_id, msg["direction"], msg["body"], cls, why, msg["external_id"], msg.get("created_at") or time.time()),
        )


async def _process_one(campaign_id, full, source="gmail"):
    if any(label in full.get("labelIds", []) for label in ("SENT", "DRAFT")):
        return False
    parsed = gmail_client.parse_message(full)
    complete = parsed.pop("body_complete")
    if not complete:
        raise ValueError("Full message body unavailable; refusing to classify a snippet")
    thread = engine.ensure_thread(campaign_id, parsed["contact_email"], parsed["contact_name"], parsed["subject"], parsed["gmail_thread_id"], parsed["reply_to_id"])
    # Sync before classification, including the current event as context-only.
    await asyncio.to_thread(_sync_thread_context, thread["id"], parsed["gmail_thread_id"])
    result = await engine.process_reply(campaign_id=campaign_id, source=source, **parsed)
    if result.get("status") in ("error", "busy"):
        raise RuntimeError("Inbound work is incomplete; checkpoint and Pub/Sub acknowledgement held")
    return True


async def process_new(campaign_id):
    await engine.resume_pending()
    history_id = db.get_setting("gmail_history_id")
    if not history_id:
        return 0
    recovering = False
    try:
        messages, latest = await asyncio.to_thread(gmail_client.fetch_new_messages_since, history_id)
    except gmail_client.HistoryExpired:
        # Checkpoint older than Gmail's history window: full label sync, then
        # reset the checkpoint to now. Engine idempotency dedupes what we saw.
        recovering = True
        latest = await asyncio.to_thread(gmail_client.current_history_id)
        messages = await asyncio.to_thread(gmail_client.full_sync_list)
        db.set_setting("worker_last_recovery", time.strftime("%Y-%m-%d %H:%M:%S"))
    count = 0
    for full in messages:
        if await _process_one(campaign_id, full, source="gmail_recovery" if recovering else "gmail"):
            count += 1
    if latest and latest != history_id:
        db.set_setting("gmail_history_id", str(latest))  # checkpoint moves only after processing
    return count


async def run(campaign_id):
    ok, why = gmail_client.available()
    if not ok:
        print(f"worker not started: {why}")
        return
    last_renew = 0.0
    failures = 0
    while True:
        try:
            if time.time() - last_renew > RENEW_SECONDS:
                await process_new(campaign_id)  # reconcile backlog BEFORE renewal
                await asyncio.to_thread(_ensure_checkpoint)
                last_renew = time.time()
            notifications = await asyncio.to_thread(gmail_client.pull_pubsub)
            acked = []
            for _payload, ack_id in notifications:
                await process_new(campaign_id)
                acked.append(ack_id)
            await asyncio.to_thread(gmail_client.ack_pubsub, acked)  # ack only after processing
            await process_new(campaign_id)  # reconciliation pass even without a notification
            engine.scheduled_digest_tick()
            failures = 0
            _health(True)
            await asyncio.sleep(POLL_SECONDS)
        except Exception as e:
            failures += 1
            _health(False, f"{type(e).__name__}: {e}", failures)
            await asyncio.sleep(min(MAX_BACKOFF_SECONDS, POLL_SECONDS * (2 ** min(failures, 5))))


if __name__ == "__main__":
    db.init()
    import argparse
    parser = argparse.ArgumentParser(description="Run the worker for one explicitly selected campaign label mapping")
    parser.add_argument("--campaign-id", required=True, type=int)
    args = parser.parse_args()
    camp = engine.get_campaign(args.campaign_id)
    if not camp:
        raise SystemExit("Campaign ID not found; finish onboarding and choose its ID first")
    asyncio.run(run(args.campaign_id))
