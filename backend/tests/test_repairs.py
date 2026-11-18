"""Repair-round regressions. All external I/O is fake. No account calls or sends."""
import asyncio
import base64
import concurrent.futures
import os
import sys
import time
import threading
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import engine
import gmail_client
import worker
import llm
import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(gmail_client, "ENABLED", False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    db.init()
    db.run("INSERT INTO campaigns (name,status,facts,autonomy,created_at) VALUES (?,?,?,?,?)",
           ("Fictional repair test", "active", db.jdump({"price":"49.99 USD"}), db.jdump({"version":2,"auto_price":True}), time.time()))


def reply(eid="e1", body="How much does it cost?", **kw):
    return asyncio.run(engine.process_reply(1, eid, "Subject", "lead@example.com", "Lead", body, **kw))


def draft():
    reply(body="Is a discount available?")
    return db.q("SELECT * FROM actions", one=True)


@pytest.mark.parametrize("text", ["Never auto-reply to price questions; drafts only", "Do not answer pricing without me", "Draft price replies only", "Don't reply to prices", "ماتردش على الأسعار"])
def test_refusals_never_suggest_grant(text):
    assert not engine._wants_auto_price(text)


def test_free_text_never_grants_execution():
    answers = {"business":"Consulting", "facts":"49.99 USD", "price":"49.99 USD", "campaign":"Test campaign", "tone":"Friendly", "autonomy":"reply on your own to price questions"}
    db.run("UPDATE onboarding SET answers=?", (db.jdump(answers),))
    engine.onboarding_confirm()
    assert not engine.get_campaign(2)["autonomy"]["auto_price"]
    assert engine.set_rules(2, True)["ok"]
    assert engine.get_campaign(2)["autonomy"]["auto_price"]


@pytest.mark.parametrize("text,expected", [("49.99 USD","49.99 USD"), ("USD 1,299.50","1299.50 USD"), ("500 EGP","500 EGP"), ("0.01 EUR","0.01 EUR"), ("500 جنيه مصري","500 EGP"), ("$49.99",None), ("500 dollars",None), ("500 جنيه",None), ("49.99 CAD","49.99 CAD"), ("500",None), ("500 USD per month for 12 months",None), ("49.999 USD",None), ("-5 USD",None), ("0 USD",None), ("10-20 USD",None), ("1.299,50 EUR",None), ("12,34 USD",None)])
def test_price_is_exact_and_unambiguous(text, expected):
    assert engine.extract_price(text) == expected


def test_paused_campaign_never_auto_sends():
    db.run("UPDATE campaigns SET status='paused'")
    reply()
    a = db.q("SELECT * FROM actions", one=True)
    assert a["kind"] == "draft" and a["status"] == "pending"
    assert not app.resolve(a["id"], app.ResolveIn(decision="approve"))["ok"]


def test_low_confidence_never_auto_sends(monkeypatch):
    async def classify(*a):
        return {"intent":"interested","asks_price":True,"confidence":0.01,"reason":"low certainty"}
    monkeypatch.setattr(llm,"classify",classify)
    reply()
    assert db.q("SELECT kind FROM actions", one=True)["kind"] == "draft"


@pytest.mark.parametrize("raw", [{"intent":"invented","asks_price":True,"confidence":1,"reason":"x"}, {"intent":"interested","asks_price":"yes","confidence":1,"reason":"x"}, {"intent":"interested","asks_price":True,"confidence":float("nan"),"reason":"x"}, {"intent":"interested","asks_price":True,"confidence":3,"reason":"x"}, None])
def test_invalid_model_schema_fails_closed(raw):
    result = llm.validate_result(raw)
    assert result["intent"] == "unclear" and result["confidence"] == 0


@pytest.mark.parametrize("body", ["What is the price after a discount?", "How much does it cost and do you offer support?", "What is the price and can I cancel anytime?"])
def test_mixed_intent_never_auto_sends(body):
    reply(body=body)
    assert db.q("SELECT kind FROM actions",one=True)["kind"] == "draft"


def test_empty_edit_is_not_restored():
    a = draft()
    result = app.resolve(a["id"],app.ResolveIn(decision="approve",body=""))
    assert not result["ok"]
    assert db.q("SELECT status FROM actions",one=True)["status"] == "pending"
    assert not db.q("SELECT id FROM messages WHERE direction='out'")


def test_old_draft_cancelled_after_optout_across_threads_and_campaigns():
    a = draft()
    reply("stop", "Please stop emailing me", gmail_thread_id="another-thread")
    result = app.resolve(a["id"],app.ResolveIn(decision="approve",body="Do not send"))
    assert not result["ok"]
    assert db.q("SELECT status FROM actions WHERE id=?",(a["id"],),one=True)["status"] == "cancelled"
    assert db.q("SELECT * FROM suppressions",one=True)["email"] == "lead@example.com"


def test_kill_blocks_manual_approval():
    a = draft(); db.set_setting("kill_switch","on")
    assert not app.resolve(a["id"],app.ResolveIn(decision="approve"))["ok"]


def test_parallel_approval_has_only_one_outbound_attempt(monkeypatch):
    a = draft(); entered = threading.Event(); release = threading.Event(); calls=[]
    def sender(*args):
        calls.append(args); entered.set(); release.wait(timeout=3)
        return "sent_simulated","Fake local send",None
    monkeypatch.setattr(engine,"send_reply",sender)
    with concurrent.futures.ThreadPoolExecutor(2) as ex:
        first = ex.submit(app.resolve,a["id"],app.ResolveIn(decision="approve",body="Exact reviewed text"))
        assert entered.wait(timeout=3)
        second = ex.submit(app.resolve,a["id"],app.ResolveIn(decision="approve",body="Exact reviewed text"))
        second_result = second.result(timeout=3)
        release.set(); first_result=first.result(timeout=3)
    assert first_result["ok"] and not second_result["ok"] and len(calls)==1
    assert len(db.q("SELECT * FROM messages WHERE direction='out'"))==1
    assert db.q("SELECT reviewed_body FROM actions",one=True)["reviewed_body"]=="Exact reviewed text"


def test_interrupted_classification_resumes_without_lost_action(monkeypatch):
    original=llm.classify
    async def fail(*a): raise RuntimeError("local pre-action interruption")
    monkeypatch.setattr(llm,"classify",fail)
    with pytest.raises(RuntimeError): reply()
    assert db.q("SELECT event_state FROM messages",one=True)["event_state"]=="pending"
    monkeypatch.setattr(llm,"classify",original)
    asyncio.run(engine.resume_pending())
    assert len(db.q("SELECT * FROM actions"))==1
    assert reply()["status"]=="duplicate"


def test_hard_crash_inbound_lease_is_recovered(monkeypatch):
    t=engine.ensure_thread(1,"lead@example.com","Lead","s")
    db.run("INSERT INTO messages(thread_id,direction,body,external_id,created_at,event_state,lease_at,source) VALUES (?,?,?,?,?,?,?,?)",
           (t["id"],"in","How much does it cost?","crash",time.time(),"processing",time.time()-200,"simulator"))
    asyncio.run(engine.resume_pending())
    assert db.q("SELECT event_state FROM messages WHERE external_id='crash'",one=True)["event_state"]=="done"
    assert len(db.q("SELECT * FROM actions"))==1


def test_interruption_after_claim_never_blind_retries(monkeypatch):
    calls=[]
    def fail(*args): calls.append(args); raise RuntimeError("local uncertain I/O")
    monkeypatch.setattr(engine,"send_reply",fail)
    with pytest.raises(RuntimeError): reply()
    assert reply()["status"]=="duplicate"
    a=db.q("SELECT * FROM actions",one=True)
    assert a["status"]=="uncertain" and a["outbound_id"]
    assert not app.resolve(a["id"],app.ResolveIn(decision="approve"))["ok"]
    assert len(calls)==1


def test_gmail_thread_identity_not_contact_identity():
    reply("one","Question?",gmail_thread_id="first")
    reply("two","Question?",gmail_thread_id="second")
    assert {t["gmail_thread_id"] for t in db.q("SELECT * FROM threads")}=={"first","second"}


def message(mid="g1", thread="gt1", body="How much does it cost?", timestamp=1000):
    return {"id":mid,"threadId":thread,"internalDate":str(timestamp*1000),"snippet":"truncated price", "payload":{"mimeType":"text/plain","headers":[{"name":"From","value":"Lead <lead@example.com>"},{"name":"Message-ID","value":f"<{mid}@example.com>"}],"body":{"data":base64.urlsafe_b64encode(body.encode()).decode()}}}


def test_worker_contract_fullbody_and_context_before_classifier(monkeypatch):
    full=message()
    context=[{**gmail_client.parse_message(message("earlier",body="Please discuss our requirements",timestamp=500)),"direction":"in"}, {**gmail_client.parse_message(full),"direction":"in"}]
    monkeypatch.setattr(gmail_client,"fetch_thread_messages",lambda *a:context)
    seen=[]
    async def classify(body,campaign,history):
        seen.append((body,history));return {"intent":"question","reason":"review","confidence":0.9,"asks_price":False}
    monkeypatch.setattr(llm,"classify",classify)
    assert asyncio.run(worker._process_one(1,full))
    assert seen[0][0]=="How much does it cost?" and "Please discuss our requirements" in seen[0][1]
    assert db.q("SELECT created_at FROM messages WHERE external_id='earlier'",one=True)["created_at"]==500


def test_worker_refuses_snippet_as_fullbody(monkeypatch):
    full=message();full["payload"].pop("body")
    with pytest.raises(ValueError):asyncio.run(worker._process_one(1,full))


def test_sent_messages_never_enter_inbound_pipeline():
    full=message();full["labelIds"]=["SENT"]
    assert asyncio.run(worker._process_one(1,full)) is False
    assert not db.q("SELECT * FROM actions")


def test_multipart_html_fallback_and_plain_preference():
    full=message(body="full question beyond snippet")
    payload=full["payload"];plain=payload.pop("body")
    payload["mimeType"]="multipart/alternative"
    payload["parts"]=[{"mimeType":"text/plain","body":plain},{"mimeType":"text/html","body":{"data":base64.urlsafe_b64encode(b"<p>other</p>").decode()}}]
    assert gmail_client.parse_message(full)["body"]=="full question beyond snippet"
    payload["parts"]=payload["parts"][1:]
    assert gmail_client.parse_message(full)["body"]=="other"


def test_full_sync_reads_all_pages(monkeypatch):
    class Response:
        def __init__(self,value):self.value=value
        def execute(self):return self.value
    class Fake:
        def users(self):return self
        def labels(self):return self
        def messages(self):return self
        def list(self,**kw):
            if "labelIds" not in kw:return Response({"labels":[{"id":"Label_1","name":"campaigns"}]})
            return Response({"messages":[{"id":"later"}],"nextPageToken":"page2"} if "pageToken" not in kw else {"messages":[{"id":"older"}]})
        def get(self,**kw):return Response({"id":kw["id"],"internalDate":"2000" if kw["id"]=="later" else "1000"})
    monkeypatch.setattr(gmail_client,"_service",Fake)
    assert [m["id"] for m in gmail_client.full_sync_list()]==["older","later"]


def test_expired_recovery_captures_checkpoint_before_listing(monkeypatch):
    calls=[]
    monkeypatch.setattr(gmail_client,"fetch_new_messages_since",lambda *a:(_ for _ in ()).throw(gmail_client.HistoryExpired()))
    monkeypatch.setattr(gmail_client,"current_history_id",lambda:calls.append("checkpoint") or "new")
    monkeypatch.setattr(gmail_client,"full_sync_list",lambda:calls.append("list") or [])
    db.set_setting("gmail_history_id","old")
    asyncio.run(worker.process_new(1))
    assert calls==["checkpoint","list"]


def test_unfinished_event_holds_checkpoint(monkeypatch):
    db.set_setting("gmail_history_id","old")
    monkeypatch.setattr(gmail_client,"fetch_new_messages_since",lambda *a:([message()],"new"))
    async def fail(*a, **kw):raise RuntimeError("incomplete")
    monkeypatch.setattr(worker,"_process_one",fail)
    with pytest.raises(RuntimeError):asyncio.run(worker.process_new(1))
    assert db.get_setting("gmail_history_id")=="old"


def test_manual_simulated_reply_is_in_digest():
    a=draft()
    assert app.resolve(a["id"],app.ResolveIn(decision="approve",body="Reviewed answer"))["ok"]
    assert engine.digest()["replied_simulated"]==1


def test_onboarding_edit_revokes_auto_permission():
    db.run("UPDATE onboarding SET done=1,campaign_id=1,answers=?",(db.jdump({"price":"49.99 USD","facts":"49.99 USD","autonomy":"price"}),))
    assert engine.onboarding_edit("price","59.99 USD")["ok"]
    assert not engine.get_campaign(1)["autonomy"]["auto_price"]


def test_followup_is_actionable_not_claimed_scheduled():
    reply(body="not now, next month")
    notes=app.followups()
    assert notes and "next month" in notes[0]["request"]
    assert app.followup_handled(notes[0]["id"])["ok"]
    assert not app.followups()


def test_approval_is_counted_on_decision_day_not_inbound_day():
    a=draft()
    db.run("UPDATE actions SET created_at=? WHERE id=?",(time.time()-172800,a["id"]))
    assert app.resolve(a["id"],app.ResolveIn(decision="approve",body="Reviewed today"))["ok"]
    assert engine.digest()["replied_simulated"]==1


def test_dnc_counts_unique_contacts_not_gmail_threads():
    reply("one","Question?",gmail_thread_id="first")
    reply("two","Question?",gmail_thread_id="second")
    reply("three","Stop emailing me",gmail_thread_id="third")
    assert engine.campaign_stats(1)["do_not_contact"]==1


def test_stale_automatic_price_draft_cannot_use_edited_facts(monkeypatch):
    original=engine.execute_action
    monkeypatch.setattr(engine,"execute_action",lambda *a,**kw:None)
    reply()
    a=db.q("SELECT * FROM actions",one=True)
    assert a["kind"]=="reply"
    db.run("UPDATE campaigns SET facts=?",(db.jdump({"price":"59.99 USD"}),))
    monkeypatch.setattr(engine,"execute_action",original)
    assert not engine.execute_action(a["id"],automatic=True)["ok"]
    assert db.q("SELECT kind FROM actions",one=True)["kind"]=="draft"


def test_real_draft_contract_uses_context_and_rejects_unknown_facts(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER","openai")
    monkeypatch.setenv("LLM_API_KEY","fake-test-key-not-a-secret")
    prompts=[]
    async def fake(prompt):prompts.append(prompt);return {"body":"Recorded price is 49.99 USD. Which workflow concerns you?","fact_keys":["price"]}
    monkeypatch.setattr(llm,"_json_completion",fake)
    body,note=asyncio.run(llm.review_draft("What about our workflow?",engine.get_campaign(1),"Earlier workflow question","fallback"))
    assert "49.99 USD" in body and "Earlier workflow question" in prompts[0] and "AI-assisted" in note
    async def invalid(prompt):return {"body":"Invented support claim","fact_keys":["unsupported"]}
    monkeypatch.setattr(llm,"_json_completion",invalid)
    body,note=asyncio.run(llm.review_draft("x",engine.get_campaign(1),"context","fallback"))
    assert body=="fallback" and "unavailable or invalid" in note


def test_outbound_mime_has_exact_identity_and_thread_headers():
    from email import message_from_bytes
    raw=gmail_client._raw_reply("lead@example.com","Original subject","Reviewed body","<durable@local>","<parent@example.com>")
    parsed=message_from_bytes(base64.urlsafe_b64decode(raw))
    assert parsed["Message-ID"]=="<durable@local>"
    assert parsed["In-Reply-To"]=="<parent@example.com>"
    assert parsed["References"]=="<parent@example.com>"
    assert parsed["Subject"]=="Re: Original subject"


def test_full_sync_recovery_is_review_only_even_when_price_permitted():
    reply(source="gmail_recovery")
    assert db.q("SELECT kind FROM actions",one=True)["kind"]=="draft"


@pytest.mark.parametrize("text", ["Please stop emailing. I do not want a discount.","Unsubscribe me, what is the price?","Remove me, but thanks for the special price.","وقفلي الرسائل ومش عاوز خصم"])
def test_optout_is_independent_of_commercial_intent(text):
    a=draft()
    reply("stop-mixed",text)
    assert db.q("SELECT * FROM suppressions",one=True)
    assert db.q("SELECT classification FROM messages WHERE external_id='stop-mixed'",one=True)["classification"]=="opt_out"
    assert not app.resolve(a["id"],app.ResolveIn(decision="approve"))["ok"]


def test_older_draft_is_blocked_after_newer_request_change():
    a=draft()
    reply("changed","Actually ignore that previous request. We have changed our needs.")
    result=app.resolve(a["id"],app.ResolveIn(decision="approve",body="Old request answer"))
    assert not result["ok"] and result["status"]=="stale"
    assert not db.q("SELECT * FROM messages WHERE direction='out'")


def test_newer_gmail_context_blocks_old_draft_before_send(monkeypatch):
    reply("old","Discount?",gmail_thread_id="thread")
    a=db.q("SELECT * FROM actions",one=True)
    def sync(tid,gid):
        db.run("INSERT INTO messages(thread_id,direction,body,external_id,created_at,classification) VALUES (?,?,?,?,?,?)",(tid,"in","Changed requirements","new-context",time.time()+1,"inbound_context"))
    monkeypatch.setattr(gmail_client,"available",lambda:(True,"fake"))
    monkeypatch.setattr(worker,"_sync_thread_context",sync)
    assert not engine.execute_action(a["id"])["ok"]
    assert db.q("SELECT status FROM actions",one=True)["status"]=="stale"


def test_invalid_real_model_json_goes_to_review(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER","openai")
    monkeypatch.setenv("LLM_API_KEY","test-placeholder")
    async def invalid(*a):raise ValueError("not JSON")
    monkeypatch.setattr(llm,"_classify_real",invalid)
    result=asyncio.run(llm.classify("How much?",engine.get_campaign(1),""))
    assert result["intent"]=="unclear" and result["confidence"]==0
    assert not asyncio.run(llm.test_connection())["ok"]


def test_idle_pubsub_deadline_is_empty_not_worker_failure(monkeypatch):
    from google.cloud import pubsub_v1
    from google.api_core.exceptions import DeadlineExceeded
    closed=[]
    class Fake:
        def pull(self,**kw):raise DeadlineExceeded("idle")
        def close(self):closed.append(True)
    monkeypatch.setattr(pubsub_v1,"SubscriberClient",Fake)
    assert gmail_client.pull_pubsub()==[] and closed==[True]


def test_offline_support_draft_uses_literal_recorded_fact():
    campaign=engine.get_campaign(1)
    campaign["facts"]["raw_facts"]="The plan is monthly. Technical support is included. No phone support."
    draft_body=engine._grounded_draft("question","Is technical support included?",campaign,"Lead")
    assert '"Technical support is included."' in draft_body and 'sentence.strip' not in draft_body


def test_blank_optional_environment_values_use_provider_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER","openai")
    monkeypatch.setenv("LLM_API_KEY","test-placeholder")
    monkeypatch.setenv("LLM_MODEL","")
    monkeypatch.setenv("LLM_BASE_URL","")
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {"choices":[{"message":{"content":"{}"}}]}
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kw):calls.append((url,kw));return Response()
    monkeypatch.setattr(llm.httpx,"AsyncClient",Client)
    asyncio.run(llm._json_completion("local contract test"))
    assert calls[0][0].startswith("https://")
    assert calls[0][1]["json"]["model"]
