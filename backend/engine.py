"""Durable inbound processing and an at-most-once outbound attempt per action.
Automatic decisions fail closed. Real providers are optional and not live-tested.
"""
import re
import time
import uuid
import db
import llm
from email_text import newest_authored_text

AUTO_SENDER_MARKERS = ("auto-reply", "automatic reply", "out of office", "خارج المكتب", "رد تلقائي", "no-reply", "noreply", "mailer-daemon")
METRIC_EXCLUDED = ("auto_reply", "inbound_context", "processing")
PRICE_WORDS = ("price", "pricing", "cost", "سعر", "أسعار")
NO_PRICE_MARKERS = ("no fixed price", "no set price", "no price", "no flat rate", "مفيش", "مافيش", "لا يوجد", "بدون")
DISCOUNT_WORDS = ("discount", "deal", "negotia", "cheaper", "special price", "خصم")
AUTONOMY_WORDS = PRICE_WORDS + ("draft", "nothing", "none", "مسود", "مفيش")
MIN_CONFIDENCE = 0.85
LEASE_SECONDS = 120


def is_auto_message(body, sender):
    return any(m in f"{body or ''} {sender or ''}".lower() for m in AUTO_SENDER_MARKERS)


def get_campaign(campaign_id):
    row = db.q("SELECT * FROM campaigns WHERE id=?", (campaign_id,), one=True)
    if row:
        for key, default in (("facts", {}), ("autonomy", {}), ("steps", {})):
            row[key] = db.jload(row[key], default) or default
    return row


def known_campaign_step(campaign, contact_email):
    steps = (campaign or {}).get("steps") or {}
    for step in (steps.get("mapping", []) if isinstance(steps, dict) else steps):
        if step.get("contact") == contact_email:
            return step.get("step")
    return None


def parse_steps(text):
    parts = re.split(r"->|→|\n+| then | next | followed by | after that | ثم | بعدين | وبعدين |،|, |\. ", text or "", flags=re.I)
    steps = []
    for part in parts:
        part = part.strip(" .,،")
        if not part or any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", part.lower()) for w in ("campaign", "goal", "called", "named", "حملة", "هدف", "اسمها")):
            continue
        steps.append({"order": len(steps) + 1, "name": part})
    return steps


def ensure_thread(campaign_id, contact_email, contact_name, subject, gmail_thread_id=None, reply_to_id=None):
    email = contact_email.strip().lower()
    ref = f"gmail:{campaign_id}:{gmail_thread_id}" if gmail_thread_id else f"{campaign_id}:{email}"
    with db.transaction() as c:
        c.execute("INSERT OR IGNORE INTO threads (campaign_id,external_ref,subject,contact_email,contact_name,gmail_thread_id,created_at,reply_to_id) VALUES (?,?,?,?,?,?,?,?)",
                  (campaign_id, ref, subject, email, contact_name, gmail_thread_id, time.time(), reply_to_id))
        row = c.execute("SELECT * FROM threads WHERE external_ref=?", (ref,)).fetchone()
        # One Gmail thread with multiple senders is not safe to route automatically.
        if row["contact_email"].lower() != email:
            raise ValueError("Thread recipient changed; manual review of the destination is required")
        if reply_to_id:
            c.execute("UPDATE threads SET reply_to_id=? WHERE id=?", (reply_to_id, row["id"]))
        return dict(c.execute("SELECT * FROM threads WHERE id=?", (row["id"],)).fetchone())


def thread_context_text(thread_id):
    msgs = db.q("SELECT direction,body FROM messages WHERE thread_id=? ORDER BY created_at,id", (thread_id,))
    return "\n".join(("Customer: " if m["direction"] == "in" else "Us: ") + m["body"] for m in msgs)


def send_reply(thread, body):
    import gmail_client
    ok, why = gmail_client.available()
    if thread.get("gmail_thread_id") and ok:
        try:
            result = gmail_client.send_message(thread["gmail_thread_id"], thread["contact_email"], thread.get("subject") or "", body,
                                               message_id=thread.get("outbound_id"), in_reply_to=thread.get("reply_to_id"))
            return "sent", "Gmail accepted the message. This is not a recipient delivery receipt.", (result or {}).get("id")
        except Exception as exc:
            return "send_failed", f"Send outcome is uncertain ({type(exc).__name__}). No automatic retry. Reconcile the saved message identity in Gmail.", None
    if thread.get("gmail_thread_id"):
        return "send_failed", f"Gmail unavailable: {why}. No send was attempted; manual review is required.", None
    return "sent_simulated", "Simulation: recorded locally only. No real email was sent.", None


def _send_gate(c, thread, automatic=False):
    if thread["do_not_contact"] or c.execute("SELECT 1 FROM suppressions WHERE email=?", (thread["contact_email"].lower(),)).fetchone():
        return "Contact has opted out. Sending is blocked across campaigns."
    kill = c.execute("SELECT value FROM settings WHERE key='kill_switch'").fetchone()
    if kill and kill[0] == "on":
        return "The kill switch is on. Sending is blocked."
    campaign = c.execute("SELECT * FROM campaigns WHERE id=?", (thread["campaign_id"],)).fetchone()
    if not campaign or campaign["status"] != "active":
        return "This campaign is paused. Sending is blocked."
    if automatic:
        rules = db.jload(campaign["autonomy"], {}) or {}
        if rules.get("version") != 2 or rules.get("auto_price") is not True:
            return "Automatic price replies are not explicitly enabled."
    return None


def execute_action(aid, body=None, automatic=False):
    """One durable claim before I/O. A crash or unknown response never retries a send.
    A concurrent caller cannot pass the pending->sending compare-and-set gate.
    Approval uses the exact provided text, including a deliberately empty edit.
    """
    now = time.time()
    preliminary = db.q("SELECT t.* FROM threads t JOIN actions a ON a.thread_id=t.id WHERE a.id=?", (aid,), one=True)
    if preliminary and preliminary.get("gmail_thread_id"):
        import gmail_client, worker
        ok, why = gmail_client.available()
        if not ok:
            return {"ok": False, "detail": f"Gmail is unavailable ({why}); no send attempted."}
        try:
            worker._sync_thread_context(preliminary["id"], preliminary["gmail_thread_id"])
        except Exception as exc:
            return {"ok": False, "detail": f"Could not refresh the full thread ({type(exc).__name__}); no send attempted."}
    with db.transaction() as c:
        row = c.execute("SELECT * FROM actions WHERE id=?", (aid,)).fetchone()
        if not row:
            return {"ok": False, "detail": "Item not found"}
        action = dict(row)
        if action["status"] != "pending":
            return {"ok": False, "detail": "Already decided, being sent, or outcome uncertain. No second attempt was made.", "status": action["status"]}
        final = action["body"] if body is None else body
        if not isinstance(final, str) or not final.strip():
            return {"ok": False, "detail": "The edited reply is empty. Write a reply before approving."}
        thread = dict(c.execute("SELECT * FROM threads WHERE id=?", (action["thread_id"],)).fetchone())
        block = _send_gate(c, thread, automatic)
        if block:
            if automatic:
                c.execute("UPDATE actions SET kind='draft',reason=? WHERE id=?", (block, aid))
            return {"ok": False, "detail": block}
        if automatic and action.get("campaign_snapshot"):
            current = c.execute("SELECT facts,autonomy FROM campaigns WHERE id=?", (thread["campaign_id"],)).fetchone()
            if action["campaign_snapshot"] != db.jdump([db.jdump(db.jload(current["facts"], {})), db.jdump(db.jload(current["autonomy"], {}))]):
                c.execute("UPDATE actions SET kind='draft',reason='Business facts or permission changed while preparing this reply. Review the exact draft.' WHERE id=?", (aid,))
                return {"ok": False, "detail": "Facts or permission changed. Kept for human review."}
        latest = c.execute("SELECT id FROM messages WHERE thread_id=? AND direction='in' AND COALESCE(classification,'') != 'auto_reply' ORDER BY created_at DESC,id DESC LIMIT 1", (thread["id"],)).fetchone()
        if not action.get("message_id") or not latest or latest["id"] != action["message_id"]:
            c.execute("UPDATE actions SET status='stale',reason='A newer customer message changed the context. This old draft cannot be approved; review the latest conversation.',decision_at=? WHERE id=?", (time.time(), aid))
            return {"ok": False, "status": "stale", "detail": "Newer customer context exists or this draft has no inbound anchor. Approval blocked. Review the latest message and its current draft."}
        # Old uncertain attempts are never converted into fresh sends.
        if action["send_state"] in ("failed", "attempting", "uncertain"):
            return {"ok": False, "detail": "An earlier attempt needs reconciliation. No retry was made."}
        outbound_id = action["outbound_id"] or f"<cra-{uuid.uuid4().hex}@campaign-reply.local>"
        c.execute("UPDATE actions SET status='sending',send_state='attempting',body=?,reviewed_body=?,outbound_id=?,attempt_at=? WHERE id=? AND status='pending'",
                  (final, final, outbound_id, now, aid))
        thread["outbound_id"] = outbound_id
    try:
        st, note, gm_id = send_reply(thread, final)
    except BaseException:
        # Even a process interruption is not proof the outbound call never landed.
        db.run("UPDATE actions SET status='uncertain',send_state='uncertain',reason=? WHERE id=?",
               ("Interrupted outbound attempt. Reconcile in Gmail; never resend blindly.", aid))
        raise
    with db.transaction() as c:
        failed = st == "send_failed"
        status = "uncertain" if failed else (("sent" if st == "sent" else "sent_simulated") if automatic else ("approved_sent" if st == "sent" else "approved_recorded"))
        c.execute("UPDATE actions SET status=?,send_state=?,gmail_message_id=?,reason=?,decision_at=? WHERE id=?",
                  (status, "uncertain" if failed else st, gm_id, note, time.time(), aid))
        if not failed:
            c.execute("INSERT OR IGNORE INTO messages (thread_id,direction,body,external_id,created_at,event_state) VALUES (?,?,?,?,?,'done')",
                      (thread["id"], "out", final, f"action-{aid}", time.time()))
    return {"ok": not failed, "status": status, "send_status": st, "detail": note, "note": note, "final_body": final}


def reconcile_action(aid):
    """Only positive exact outbound identity evidence can close an uncertain send.
    A negative or unavailable search never authorizes another attempt.
    """
    import gmail_client
    a = db.q("SELECT * FROM actions WHERE id=?", (aid,), one=True)
    if not a or a["status"] not in ("uncertain", "sending"):
        return {"ok": False, "detail": "No uncertain attempt to reconcile"}
    if a["status"] == "sending" and time.time() - (a["attempt_at"] or 0) < LEASE_SECONDS:
        return {"ok": False, "detail": "The send is still in progress. Wait before reconciling."}
    t = db.q("SELECT * FROM threads WHERE id=?", (a["thread_id"],), one=True)
    if not t.get("gmail_thread_id") or not a.get("outbound_id"):
        return {"ok": False, "detail": "No verifiable Gmail outbound identity. Kept unresolved; no retry."}
    try:
        found = gmail_client.find_landed_reply(t["gmail_thread_id"], a["body"], a["attempt_at"], message_id=a["outbound_id"])
    except Exception as exc:
        return {"ok": False, "detail": f"Reconciliation unavailable ({type(exc).__name__}). No retry."}
    if not found:
        return {"ok": False, "detail": "No exact outbound identity found yet. Absence is not proof of failure. No retry."}
    with db.transaction() as c:
        c.execute("UPDATE actions SET status='approved_sent',send_state='sent',decision_at=?,reason=? WHERE id=? AND status IN ('uncertain','sending')",
                  (time.time(), "Exact outbound Message-ID verified in Gmail. No duplicate was sent.", aid))
        c.execute("INSERT OR IGNORE INTO messages (thread_id,direction,body,external_id,created_at,event_state) VALUES (?,?,?,?,?,'done')",
                  (t["id"], "out", a["body"], f"action-{aid}", time.time()))
    return {"ok": True, "status": "approved_sent", "detail": "Verified the original message in Gmail. No new send."}


def _grounded_draft(intent, body, campaign, name):
    """Reviewable, not automatic: only explicit stored facts are included.
    No invented discount, support, timing, promise, or follow-up commitment.
    """
    price = (campaign.get("facts") or {}).get("price")
    intro = f"Hi {name}, " if name else "Hi, "
    if any(w in body.lower() for w in DISCOUNT_WORDS):
        return intro + (f"the recorded standard price is {price}. " if price else "") + "I need to confirm whether a discount is available before quoting a different price."
    if intent == "interested" and price:
        return intro + f"the recorded price is {price}. What else would you like to know?"
    if intent == "objection":
        return intro + "thanks for explaining your concern. Could you share which part you would like us to clarify?"
    if intent == "unclear":
        return intro + "could you clarify what you would like help with?"
    recorded = (campaign.get("facts") or {}).get("raw_facts") or ""
    # Literal supplied-fact excerpts are review drafts, never inferred answers.
    topics = [w for w in ("support", "shipping", "delivery", "training", "onboarding", "integration") if w in body.lower()]
    for sentence in re.split(r"(?<=[.!?])\s+|;\s*", recorded):
        if any(topic in sentence.lower() for topic in topics):
            return intro + f'our recorded information says: "{sentence.strip()}" Does that answer your question?'
    return intro + "thanks for your question. I need to verify this detail before giving you an answer."


def price_only_question(body):
    """Conservative scope matcher, not a language model. Unrecognized or compound
    questions are review-only, even when a classifier calls them interested.
    """
    text = (body or "").lower().strip(" .!?؟")
    text = re.sub(r"^(hi|hello|hey)[,! .]+", "", text)
    return bool(re.fullmatch(r"(?:how much(?: does it cost(?: exactly)?)?|what(?: is|'s) (?:the |your )?(?:price|cost)|can you (?:tell|share) (?:me )?(?:the |your )?price|بيكلف كام|بكام|السعر كام|كام السعر)", text))


async def process_reply(campaign_id, external_id, subject, contact_email, contact_name, body, source="simulator", gmail_thread_id=None, reply_to_id=None, created_at=None, auto=False):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return {"status": "error", "detail": "Campaign not found"}
    thread = ensure_thread(campaign_id, contact_email, contact_name, subject, gmail_thread_id, reply_to_id)
    with db.transaction() as c:
        old = c.execute("SELECT * FROM messages WHERE external_id=?", (external_id,)).fetchone()
        if old and old["classification"] != "inbound_context" and old["event_state"] == "done":
            return {"status": "duplicate", "thread_id": old["thread_id"]}
        if old and old["event_state"] == "processing" and (old["lease_at"] or 0) > time.time() - LEASE_SECONDS:
            return {"status": "busy", "thread_id": old["thread_id"]}
        if old:
            mid = old["id"]
            c.execute("UPDATE messages SET event_state='processing',lease_at=? WHERE id=?", (time.time(), mid))
        else:
            mid = c.execute("INSERT INTO messages (thread_id,direction,body,external_id,created_at,source,event_state,lease_at) VALUES (?,'in',?,?,?,?,'processing',?)",
                            (thread["id"], body, external_id, created_at or time.time(), source, time.time())).lastrowid
    try:
        # Opt-out and auto-submitted safeguards are independent of model output.
        authored = newest_authored_text(body)
        mock = llm._classify_mock(authored, campaign)
        if auto or is_auto_message(authored, contact_email):
            result = {"intent": "auto_reply", "reason": "Auto-submitted message skipped", "confidence": 1, "asks_price": False, "quote": ""}
        elif llm.has_optout(authored):
            result = mock
        else:
            result = llm.validate_result(await llm.classify(authored, campaign, thread_context_text(thread["id"])))
        if not authored and result["intent"] != "auto_reply":
            result = {"intent": "unclear", "reason": "No separate newest authored text could be identified. Quoted history cannot establish a new consent decision.", "confidence": 0.0, "asks_price": False, "quote": ""}
        if result["intent"] == "opt_out" and not llm.has_optout(authored):
            result = {**result, "intent": "unclear", "confidence": 0.0, "reason": "Possible stop intent needs human review; no explicit stop signal in the newest authored text."}
        intent = result["intent"]
        confidence = result["confidence"]
        draft = _grounded_draft(intent, authored, campaign, contact_name)
        draft, draft_note = await llm.review_draft(authored, campaign, thread_context_text(thread["id"]), draft) if intent not in ("opt_out", "auto_reply", "not_now") else (draft, "")
        mixed = any(w in authored.lower() for w in DISCOUNT_WORDS) or result.get("mixed_intent", False)
        facts = campaign.get("facts") or {}
        rules = campaign.get("autonomy") or {}
        wants_send = (intent == "interested" and result.get("asks_price") and confidence >= MIN_CONFIDENCE
                      and source not in ("gmail_recovery", "legacy_recovery") and price_only_question(authored) and not mixed and rules.get("version") == 2 and rules.get("auto_price") is True and bool(extract_price(facts.get("price"))))
        aid = None
        with db.transaction() as c:
            fresh = dict(c.execute("SELECT * FROM threads WHERE id=?", (thread["id"],)).fetchone())
            blocked = _send_gate(c, fresh, automatic=True)
            if intent == "auto_reply":
                action = None
            elif intent == "opt_out":
                c.execute("INSERT OR IGNORE INTO suppressions VALUES (?,?)", (contact_email.lower(), time.time()))
                c.execute("UPDATE threads SET do_not_contact=1 WHERE lower(contact_email)=?", (contact_email.lower(),))
                c.execute("UPDATE actions SET status='cancelled',reason='Cancelled after contact opted out',decision_at=? WHERE status='pending' AND thread_id IN (SELECT id FROM threads WHERE lower(contact_email)=?)", (time.time(), contact_email.lower()))
                action = ("dnc", "done", "", "Stop request recorded across local campaigns. Stop the source campaign tool as well.")
            elif fresh["do_not_contact"] or c.execute("SELECT 1 FROM suppressions WHERE email=?", (contact_email.lower(),)).fetchone():
                action = ("note", "done", "", "Suppressed contact: no reply allowed.")
            elif intent == "not_now":
                action = ("note", "followup_pending", "", "Follow-up-later request. Review the quoted request and schedule in your campaign tool; no follow-up was scheduled or sent.")
            elif wants_send and not blocked:
                action = ("reply", "pending", f"Hi {contact_name}, the recorded price is {facts['price']}.", "Recorded price, explicit permission, confidence above threshold, no mixed intent.")
            else:
                reason = ("No price is recorded in onboarding." if result.get("asks_price") and not facts.get("price") else blocked) or ("Mixed or discount request needs your decision." if mixed else "Low confidence; no automatic send." if confidence < MIN_CONFIDENCE else "No price is recorded in onboarding." if result.get("asks_price") and not facts.get("price") else "Outside the explicit automatic price-only permission. Verify the draft against the original question.")
                action = ("draft", "pending", draft, reason + " " + draft_note)
            if action:
                aid = c.execute("INSERT INTO actions (thread_id,message_id,kind,status,body,reason,created_at) VALUES (?,?,?,?,?,?,?)",
                                (thread["id"], mid, *action, time.time())).lastrowid
            if aid and action[0] == "reply":
                # Snapshot must describe the values used to compose this body,
                # not simply whichever values happen to be current at commit.
                source_row = db.jdump([db.jdump(campaign["facts"]), db.jdump(campaign["autonomy"])])
                c.execute("UPDATE actions SET campaign_snapshot=? WHERE id=?", (source_row, aid))
            c.execute("UPDATE messages SET classification=?,class_reason=?,confidence=?,quote=?,event_state='done',result=? WHERE id=?",
                      (intent, result.get("reason"), confidence, result.get("quote", ""), db.jdump(result), mid))
        if aid and wants_send and action[0] == "reply":
            execute_action(aid, automatic=True)
        return {"status": "skipped" if intent == "auto_reply" or (action and action[3] == "Suppressed contact: no reply allowed.") else "processed", "intent": intent, "thread_id": thread["id"], "simulated": result.get("simulated", True)}
    except BaseException:
        db.run("UPDATE messages SET event_state='pending' WHERE id=? AND event_state='processing'", (mid,))
        raise


async def resume_pending():
    """Retry only inbound work, never an outbound attempt. Called by worker/API loop."""
    rows = db.q("SELECT m.*,t.campaign_id,t.subject,t.contact_email,t.contact_name,t.gmail_thread_id,t.reply_to_id FROM messages m JOIN threads t ON t.id=m.thread_id WHERE m.direction='in' AND (m.event_state='pending' OR (m.event_state='processing' AND m.lease_at<?)) ORDER BY m.created_at", (time.time()-LEASE_SECONDS,))
    for m in rows:
        await process_reply(m["campaign_id"], m["external_id"], m["subject"], m["contact_email"], m["contact_name"], m["body"], m["source"], m["gmail_thread_id"], m["reply_to_id"], m["created_at"])

# ---------- Adaptive onboarding (one question at a time) ----------

ONBOARD_STEPS = [
    ("business", "What do you sell, and to whom?"),
    ("facts", "What facts can I tell a customer? If there is a fixed price, write it, e.g.: the consultation is 500 EGP."),
    ("campaign", "What is the campaign's name, its goal, and its steps in order?"),
    ("tone", "What tone should replies have? Friendly or formal?"),
    ("autonomy", "What can I reply to on my own? For example: price questions whose answer is recorded. And what should I always bring back to you?"),
]

PRICE_QUESTION = ("So I answer price questions correctly without ever getting a number wrong: what is the exact "
                  "fixed price, just the number and currency, e.g.: 500 EGP. If there is no fixed price, say: no fixed price.")


def extract_price(price_answer):
    """Accept exactly one positive decimal amount and an explicit currency.
    Full match only: bare values, periods/terms, ranges and ambiguity are rejected.
    No rounding and no extraction from business prose.
    """
    from decimal import Decimal, InvalidOperation
    if not price_answer:
        return None
    low = price_answer.strip().lower()
    if low in NO_PRICE_MARKERS or low in ("no fixed price", "مفيش سعر ثابت"):
        return None
    currencies = {code.lower(): code for code in ("EGP", "USD", "EUR", "GBP", "SAR", "AED", "CAD", "AUD")}
    currencies.update({"جنيه مصري": "EGP", "دولار أمريكي": "USD", "ريال سعودي": "SAR"})
    cur = "(?:" + "|".join(re.escape(k) for k in sorted(currencies, key=len, reverse=True)) + ")"
    num = r"(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
    m = re.fullmatch(rf"(?:(?P<before>{cur})\s*(?P<n1>{num})|(?P<n2>{num})\s*(?P<after>{cur}))", low)
    if not m:
        return None
    number = (m.group("n1") or m.group("n2")).replace(",", "")
    try:
        if Decimal(number) <= 0:
            return None
    except InvalidOperation:
        return None
    return f"{number} {currencies[m.group('before') or m.group('after')]}"


def dynamic_steps(answers):
    """Onboarding asks for the price explicitly whenever the facts mention any
    number, so a branch count or a year can never be mistaken for the price."""
    steps = []
    for key, question in ONBOARD_STEPS:
        steps.append((key, question))
        if key == "facts" and re.search(r"\d", answers.get("facts") or ""):
            steps.append(("price", PRICE_QUESTION))
    return steps


def onboarding_state():
    row = db.q("SELECT * FROM onboarding WHERE id = 1", one=True)
    row["answers"] = db.jload(row["answers"], {})
    return row


def onboarding_next_question():
    st = onboarding_state()
    if st["done"]:
        return None
    for key, question in dynamic_steps(st["answers"]):
        if key not in st["answers"]:
            return key, question
    return None


def _wants_auto_price(autonomy_text):
    """Suggestion only, never a grant. Negation/approval/draft wording fails closed.
    Actual authority comes from the dedicated confirmed boolean in set_rules.
    """
    low = (autonomy_text or "").lower()
    if any(w in low for w in ("not", "never", "don't", "do not", "draft", "without", "approval", "only me", "ماترد", "مترد", "لا ", "مسود", "ارجع")):
        return False
    return any(w in low for w in ("reply on your own to price", "automatically answer price", "رد لوحدك على أسئلة الأسعار"))


def set_rules(campaign_id, auto_price):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return {"ok": False, "detail": "Campaign not found"}
    if auto_price and not extract_price((campaign.get("facts") or {}).get("price")):
        return {"ok": False, "detail": "Record an exact positive price with currency first"}
    rules = {"version": 2, "auto_price": auto_price is True, "confirmed_at": time.time()}
    db.run("UPDATE campaigns SET autonomy=? WHERE id=?", (db.jdump(rules), campaign_id))
    return {"ok": True, "rules": rules}


def onboarding_summary():
    st = onboarding_state()
    a = st["answers"]
    price = extract_price(a.get("price"))
    auto_price = _wants_auto_price(a.get("autonomy"))
    note = "Automatic replies are off by default. Only the explicit price-only checkbox can enable them."
    if any(w in (a.get("autonomy") or "").lower() for w in PRICE_WORDS) and not price:
        note = "Your notes mention price questions, but no fixed price is recorded. I will escalate every price question until you record one."
    return {
        "business": a.get("business"),
        "facts": a.get("facts"),
        "price": price,
        "campaign": a.get("campaign"),
        "tone": a.get("tone"),
        "autonomy": a.get("autonomy"),
        "limits_note": note,
    }


def onboarding_answer(text):
    text = (text or "").strip()
    st = onboarding_state()
    if st["done"]:
        return {"type": "done"}
    nxt = onboarding_next_question()
    if not nxt:
        return {"type": "summary", "summary": onboarding_summary()}
    key, _ = nxt
    if len(text) < 3:
        return {"type": "followup", "message": "Can you tell me a bit more? I just need a clearer answer for this point.", "step": key}
    low = text.lower()
    if key == "price" and extract_price(text) is None and not any(m in low for m in NO_PRICE_MARKERS):
        return {"type": "followup", "step": "price",
                "message": "I need the price as a clear number and currency, e.g.: 500 EGP. If there is no fixed price, say: no fixed price."}
    if key == "facts" and not re.search(r"\d", text) and not any(m in low for m in NO_PRICE_MARKERS):
        return {"type": "followup", "step": "facts",
                "message": "I don't see a clear price in your answer. Is there a fixed price I can quote? Write it, or say: no fixed price, and I will escalate every price question to you."}
    if key == "autonomy" and not any(w in low for w in AUTONOMY_WORDS):
        return {"type": "followup", "step": "autonomy",
                "message": "Name at least one type I can reply to on my own, e.g.: price questions. Or say: drafts only, and come back to me for everything."}
    answers = st["answers"]
    answers[key] = text
    db.run("UPDATE onboarding SET answers = ? WHERE id = 1", (db.jdump(answers),))
    nxt2 = onboarding_next_question()
    if not nxt2:
        return {"type": "summary", "summary": onboarding_summary(),
                "message": "Here is what I understood, and the boundaries of what I do on my own. Review it, and confirm if it is right."}
    return {"type": "question", "step": nxt2[0], "message": nxt2[1]}


def onboarding_confirm(name=None, auto_price=False):
    st = onboarding_state()
    a = st["answers"]
    price = extract_price(a.get("price"))
    wants_auto_price = _wants_auto_price(a.get("autonomy"))
    autonomy = {"version": 2, "auto_price": bool(auto_price is True and price), "confirmed_at": time.time()}
    campaign_name = name or re.split(r"[,،]", (a.get("campaign") or "New campaign"))[0][:60].strip()
    steps = {"plan": parse_steps(a.get("campaign") or ""), "mapping": []}
    cid = db.run(
        "INSERT INTO campaigns (name, status, goal, steps, tone, facts, autonomy, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (campaign_name, "active", a.get("campaign"), db.jdump(steps), a.get("tone"),
         db.jdump({"business": a.get("business"), "price": price, "raw_facts": a.get("facts")}),
         db.jdump(autonomy), time.time()),
    )
    db.run("UPDATE onboarding SET done = 1, campaign_id = ? WHERE id = 1", (cid,))
    note = ""
    if wants_auto_price and not price:
        note = " Note: your notes mention price replies but there is no fixed price, so price questions will be escalated until you record one in Settings."
    return {"type": "confirmed", "campaign": campaign_name,
            "message": "Done, the boundaries are saved. Automatic replies are off unless you explicitly enabled the price-only checkbox." + note + " Try the offline simulator next. Real Gmail and model setup are optional and remain unverified until you test them with a safe inbox."}


EDITABLE_KEYS = {"business", "facts", "price", "campaign", "tone", "autonomy"}


def onboarding_edit(key, text):
    """Edit one onboarding answer after setup. Re-derives the campaign's facts,
    price, autonomy and step plan so the stored boundaries match the new answer."""
    if key not in EDITABLE_KEYS:
        return {"ok": False, "detail": "This field cannot be edited"}
    text = (text or "").strip()
    if len(text) < 3:
        return {"ok": False, "detail": "The answer is too short"}
    if key == "price" and extract_price(text) is None and not any(m in text.lower() for m in NO_PRICE_MARKERS):
        return {"ok": False, "detail": "The price must be a clear number and currency, e.g.: 500 EGP, or: no fixed price"}
    st = onboarding_state()
    answers = st["answers"]
    answers[key] = text
    with db.transaction() as c:
        c.execute("UPDATE onboarding SET answers = ? WHERE id = 1", (db.jdump(answers),))
        if st["done"]:
            linked = st.get("campaign_id")
            camp = c.execute("SELECT * FROM campaigns WHERE id = ?", (linked,)).fetchone() if linked else None
            if not camp:
                camp = c.execute("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 1").fetchone()
            if camp:
                price = extract_price(answers.get("price"))
                wants_auto = _wants_auto_price(answers.get("autonomy"))
                steps = {"plan": parse_steps(answers.get("campaign") or ""), "mapping": []}
                c.execute("UPDATE campaigns SET facts = ?, autonomy = ?, tone = ?, steps = ?, name = ?, goal = ? WHERE id = ?",
                       (db.jdump({"business": answers.get("business"), "price": price, "raw_facts": answers.get("facts")}),
                        db.jdump({"version": 2, "auto_price": False, "note": "Facts edited; confirm automatic permission again in Settings."}),
                        (answers.get("tone")), db.jdump(steps),
                        (re.split(r"[,،]", answers.get("campaign") or "New campaign")[0][:60].strip() if key == "campaign" else camp["name"]),
                        (answers.get("campaign") if key == "campaign" else camp["goal"]), camp["id"]))
    return {"ok": True, "summary": onboarding_summary()}


DIGEST_HOUR = 9  # local time; configurable via CRA_DIGEST_HOUR


def scheduled_digest_tick(now=None):
    """Daily digest scheduler: generates today's digest once, at/after DIGEST_HOUR.
    Returns the digest the first time it fires each day, else None."""
    import os
    hour = int(os.environ.get("CRA_DIGEST_HOUR", DIGEST_HOUR))
    lt = time.localtime(now or time.time())
    today = time.strftime("%Y-%m-%d", lt)
    if lt.tm_hour < hour:
        return None
    if db.get_setting("digest_last_date") == today:
        return None
    d = digest()
    db.set_setting("digest_last_date", today)
    db.set_setting("digest_latest", db.jdump(d))
    return d


def digest():
    """Daily snapshot: what arrived, how it was classified, what the agent did,
    and the per-case list. pending_now is the live queue size right now, kept
    separate from the day's counts so a stale snapshot never hides new work."""
    today = time.strftime("%Y-%m-%d")
    start = time.mktime(time.strptime(today, "%Y-%m-%d"))
    excl = "('auto_reply', 'inbound_context', 'processing')"
    arrived = db.q(f"SELECT COUNT(*) c FROM messages WHERE direction='in' AND event_state='done' AND classification NOT IN {excl} AND created_at >= ?", (start,), one=True)["c"]
    auto_skipped = db.q("SELECT COUNT(*) c FROM messages WHERE direction='in' AND classification = 'auto_reply' AND created_at >= ?", (start,), one=True)["c"]
    by_class = db.q(f"SELECT classification, COUNT(*) c FROM messages WHERE direction='in' AND event_state='done' AND classification NOT IN {excl} AND created_at >= ? GROUP BY classification", (start,))
    def acount(kind, *statuses):
        ph = ",".join("?" * len(statuses))
        clock = "COALESCE(decision_at,created_at)" if any(s in ("sent","sent_simulated","approved_sent","approved_recorded") for s in statuses) else "created_at"
        return db.q(f"SELECT COUNT(*) c FROM actions WHERE kind = ? AND status IN ({ph}) AND {clock} >= ?", (kind, *statuses, start), one=True)["c"]
    cases = db.q(
        "SELECT a.kind, a.status, a.reason, t.contact_name, t.contact_email FROM actions a JOIN threads t ON t.id = a.thread_id WHERE COALESCE(a.decision_at,a.created_at) >= ? ORDER BY COALESCE(a.decision_at,a.created_at) DESC LIMIT 20", (start,))
    return {
        "date": today,
        "followups_pending": db.q("SELECT COUNT(*) c FROM actions WHERE status='followup_pending'", one=True)["c"],
        "generated_at": time.strftime("%H:%M"),
        "arrived": arrived,
        "auto_skipped": auto_skipped,
        "classifications": by_class,
        "replied_sent": acount("reply", "sent", "approved_sent") + acount("draft", "approved_sent") + acount("escalation", "approved_sent"),
        "replied_simulated": acount("reply", "sent_simulated", "approved_recorded") + acount("draft", "approved_recorded") + acount("escalation", "approved_recorded"),
        "send_failed": db.q("SELECT COUNT(*) c FROM actions WHERE send_state IN ('failed','uncertain','attempting')", one=True)["c"],
        "drafts_waiting": acount("draft", "pending") + acount("escalation", "pending"),
        "dnc_today": acount("dnc", "done"),
        "pending_now": db.q("SELECT COUNT(*) c FROM actions WHERE status IN ('pending','uncertain','sending')", one=True)["c"],
        "cases": cases,
    }


def campaign_stats(campaign_id):
    excl = "('auto_reply', 'inbound_context', 'processing')"
    replies = db.q(
        f"SELECT COUNT(*) c FROM messages m JOIN threads t ON t.id = m.thread_id WHERE t.campaign_id = ? AND m.direction = 'in' AND m.event_state='done' AND m.classification NOT IN {excl}",
        (campaign_id,), one=True)["c"]
    by_class = db.q(
        f"SELECT m.classification, COUNT(*) c FROM messages m JOIN threads t ON t.id = m.thread_id WHERE t.campaign_id = ? AND m.direction = 'in' AND m.event_state='done' AND m.classification NOT IN {excl} GROUP BY m.classification",
        (campaign_id,))
    auto_skipped = db.q(
        "SELECT COUNT(*) c FROM messages m JOIN threads t ON t.id = m.thread_id WHERE t.campaign_id = ? AND m.direction = 'in' AND m.classification = 'auto_reply'",
        (campaign_id,), one=True)["c"]
    interested = next((r["c"] for r in by_class if r["classification"] == "interested"), 0)
    awaiting = db.q(
        "SELECT COUNT(*) c FROM actions a JOIN threads t ON t.id = a.thread_id WHERE t.campaign_id = ? AND a.status IN ('pending','uncertain','sending')",
        (campaign_id,), one=True)["c"]
    dnc = db.q("SELECT COUNT(DISTINCT lower(contact_email)) c FROM threads WHERE campaign_id = ? AND do_not_contact = 1", (campaign_id,), one=True)["c"]
    last = db.q(
        "SELECT MAX(m.created_at) t FROM messages m JOIN threads t ON t.id = m.thread_id WHERE t.campaign_id = ?",
        (campaign_id,), one=True)["t"]
    return {"observed_replies": replies, "interested": interested, "awaiting_decision": awaiting,
            "by_classification": by_class, "auto_replies_skipped": auto_skipped, "do_not_contact": dnc,
            "last_activity": last,
            "totals_note": "Observed replies = human replies that arrived and were processed (auto-replies are skipped and never counted). Total campaign sends need a documented sending source and are not available here."}


def seed_demo():
    """Demo sample data so the dashboard shows a realistic multi-campaign picture.
    Labeled demo content; runs only on a fresh DB and can be disabled with CRA_SEED=0."""
    import os
    if os.environ.get("CRA_SEED", "1") != "1":
        return
    if db.q("SELECT * FROM campaigns LIMIT 1"):
        return
    cid = db.run(
        "INSERT INTO campaigns (name, status, goal, steps, tone, facts, autonomy, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("Sample: Re-engagement campaign", "paused", "Re-activate past customers",
         db.jdump({"plan": [{"order": 1, "name": "First re-engagement email"}, {"order": 2, "name": "Follow-up after one week"}], "mapping": []}), "Friendly",
         db.jdump({"price": None, "business": "Marketing consulting"}), db.jdump({"auto_price": False}), time.time() - 86400 * 3),
    )
    samples = [
        ("nadia@example.com", "Nadia", "Hi, can we talk about the new package?", "interested", "interested reply asking about details", "note", "done", "Sample interest recorded; no reply was generated or sent."),
        ("tarek@example.com", "Tarek", "Not now, call me after the start of the year.", "not_now", "follow-up-later request", "note", "done", "Recorded for follow-up; new sends need separate authorization."),
        ("dina@example.com", "Dina", "Please stop these messages.", "opt_out", "explicit stop request", "dnc", "done", "Marked on the local do-not-contact list."),
    ]
    for i, (email, name, body, cls, reason, akind, astatus, areason) in enumerate(samples):
        tid = db.run("INSERT INTO threads (campaign_id, external_ref, subject, contact_email, contact_name, do_not_contact, created_at) VALUES (?,?,?,?,?,?,?)",
                     (cid, f"seed-{i}", "Re: Re-engagement campaign", email, name, 1 if cls == "opt_out" else 0, time.time() - 86400 * 2))
        mid = db.run("INSERT INTO messages (thread_id, direction, body, classification, class_reason, confidence, external_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (tid, "in", body, cls, "Simulation: " + reason, 0.9, f"seed-msg-{i}", time.time() - 86400 * 2))
        db.run("INSERT INTO actions (thread_id, message_id, kind, status, body, reason, created_at) VALUES (?,?,?,?,?,?,?)",
               (tid, mid, akind, astatus, "", areason, time.time() - 86400 * 2))
