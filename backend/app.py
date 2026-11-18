"""FastAPI surface for the Campaign Reply Agent local web app."""
import json
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, StrictBool
from typing import Literal

import db
import engine
import gmail_client
import llm

app = FastAPI(title="Campaign Reply Agent (local)")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8792", "http://127.0.0.1:8792"], allow_methods=["*"], allow_headers=["*"])

SIM_PATH = os.path.join(os.path.dirname(__file__), "..", "simulator", "threads.json")


async def _digest_scheduler():
    import asyncio
    while True:
        try:
            await engine.resume_pending()
            engine.scheduled_digest_tick()
            db.set_setting("pipeline_health", db.jdump({"ok": True, "at_unix": __import__("time").time()}))
        except Exception as exc:
            db.set_setting("pipeline_health", db.jdump({"ok": False, "error": type(exc).__name__, "at_unix": __import__("time").time()}))
        await asyncio.sleep(30)


@app.on_event("startup")
async def startup():
    import asyncio
    db.init()
    engine.seed_demo()
    asyncio.create_task(_digest_scheduler())


class ChatIn(BaseModel):
    text: str


@app.get("/api/status")
def status():
    st = engine.onboarding_state()
    camps = db.q("SELECT id, name, status FROM campaigns ORDER BY created_at DESC")
    health = db.jload(db.get_setting("worker_health"), None)
    return {
        "llm": llm.mode(),
        "kill_switch": db.get_setting("kill_switch", "off"),
        "pending_now": db.q("SELECT COUNT(*) c FROM actions WHERE status IN ('pending','uncertain','sending')", one=True)["c"],
        "worker_health": health,
        "pipeline_health": db.jload(db.get_setting("pipeline_health"), None),
        "inbound_work_open": db.q("SELECT COUNT(*) c FROM messages WHERE event_state IN ('pending','processing')", one=True)["c"],
        "onboarding_done": bool(st["done"]),
        "campaigns": camps,
        "unresolved_sends": db.q("SELECT COUNT(*) c FROM actions WHERE status IN ('sending','uncertain')", one=True)["c"],
        "gmail": (lambda a: {"connected": a[0], "note": a[1] + " — the code is implemented but not yet tested against a live account" if a[0] else "Not connected: " + a[1]})(gmail_client.available()),
    }


@app.post("/api/chat")
def chat(inp: ChatIn):
    st = engine.onboarding_state()
    if not st["done"]:
        text = inp.text.strip()
        if text.lower() in ("ok", "confirm", "confirmed", "done", "yes", "looks good", "تمام", "أكد", "اوكي", "تم") and not engine.onboarding_next_question():
            return engine.onboarding_confirm()
        return engine.onboarding_answer(text)
    return {"type": "info", "message": "Setup is complete. Ask me for today's summary or open the dashboard.", "digest": engine.digest()}


@app.get("/api/campaigns")
def campaigns():
    out = []
    for c in db.q("SELECT * FROM campaigns ORDER BY created_at DESC"):
        out.append({"id": c["id"], "name": c["name"], "status": c["status"], "sample": c["name"].startswith("Sample:"), **engine.campaign_stats(c["id"])})
    return out


@app.get("/api/campaign-overview-stats")
def campaign_overview_stats(include_samples: bool = False):
    row = db.q("""SELECT COUNT(DISTINCT lower(trim(t.contact_email))) AS do_not_contact
                  FROM threads t JOIN campaigns c ON c.id=t.campaign_id
                  WHERE (t.do_not_contact=1 OR EXISTS (SELECT 1 FROM suppressions s WHERE lower(s.email)=lower(t.contact_email)))
                  AND (? OR substr(c.name,1,7) != 'Sample:')""", (include_samples,), one=True)
    return row


@app.get("/api/campaigns/{cid}")
def campaign_detail(cid: int):
    c = engine.get_campaign(cid)
    if not c:
        return {"error": "not found"}
    threads = db.q("SELECT * FROM threads WHERE campaign_id = ? ORDER BY created_at DESC", (cid,))
    for t in threads:
        t["messages"] = db.q("SELECT * FROM messages WHERE thread_id = ? ORDER BY created_at", (t["id"],))
        t["actions"] = db.q("SELECT * FROM actions WHERE thread_id = ? ORDER BY created_at", (t["id"],))
    return {"campaign": c, "stats": engine.campaign_stats(cid), "threads": threads}


@app.get("/api/review")
def review():
    items = db.q(
        "SELECT a.*, t.contact_name, t.contact_email, t.subject, t.campaign_id, t.gmail_thread_id FROM actions a JOIN threads t ON t.id = a.thread_id WHERE a.status IN ('pending','sending','uncertain') ORDER BY a.created_at DESC"
    )
    for it in items:
        it["thread_messages"] = db.q(
            "SELECT direction, body, classification, class_reason, confidence, created_at FROM messages WHERE thread_id = ? ORDER BY created_at",
            (it["thread_id"],),
        )
        latest = db.q("SELECT id FROM messages WHERE thread_id=? AND direction='in' AND COALESCE(classification,'') != 'auto_reply' ORDER BY created_at DESC,id DESC LIMIT 1", (it["thread_id"],), one=True)
        it["context_stale"] = not latest or latest["id"] != it["message_id"]
        camp = engine.get_campaign(it["campaign_id"])
        it["campaign_name"] = camp["name"] if camp else ""
        it["recorded_facts"] = camp.get("facts", {}) if camp else {}
        it["effective_rule"] = camp.get("autonomy", {}) if camp else {}
    return items


class ResolveIn(BaseModel):
    decision: Literal["approve", "reject"]
    body: str | None = None  # operator-edited final reply text


@app.post("/api/review/{aid}")
def resolve(aid: int, inp: ResolveIn):
    if inp.decision == "approve":
        return engine.execute_action(aid, inp.body)
    with db.transaction() as c:
        row = c.execute("SELECT status FROM actions WHERE id=?", (aid,)).fetchone()
        if not row or row["status"] != "pending":
            return {"ok": False, "detail": "Only a pending draft can be rejected. In-progress or uncertain sends need reconciliation."}
        c.execute("UPDATE actions SET status='rejected',decision_at=? WHERE id=?", (__import__("time").time(), aid))
    return {"ok": True, "status": "rejected", "detail": "Rejected. No reply sent."}


@app.post("/api/review/{aid}/reconcile")
def reconcile(aid: int):
    return engine.reconcile_action(aid)


class ConfirmIn(BaseModel):
    auto_price: StrictBool = False


@app.post("/api/onboarding/confirm")
def confirm(inp: ConfirmIn):
    if engine.onboarding_state()["done"]:
        return {"ok": False, "detail": "Onboarding already confirmed"}
    if engine.onboarding_next_question():
        return {"ok": False, "detail": "Answer every onboarding question first"}
    return {"ok": True, **engine.onboarding_confirm(auto_price=inp.auto_price)}


@app.post("/api/campaigns/{cid}/rules")
def rules(cid: int, inp: ConfirmIn):
    return engine.set_rules(cid, inp.auto_price)


class CampaignState(BaseModel):
    status: Literal["active", "paused"]


@app.post("/api/campaigns/{cid}/state")
def campaign_state(cid: int, inp: CampaignState):
    db.run("UPDATE campaigns SET status=? WHERE id=?", (inp.status, cid))
    return {"ok": True, "status": inp.status}


@app.get("/api/activity")
def activity():
    return db.q("SELECT a.*,t.contact_name,t.contact_email FROM actions a JOIN threads t ON t.id=a.thread_id WHERE a.status NOT IN ('pending','sending','uncertain') ORDER BY COALESCE(a.decision_at,a.created_at) DESC LIMIT 50")


@app.get("/api/followups")
def followups():
    return db.q("SELECT a.*,t.contact_name,t.contact_email,m.body AS request FROM actions a JOIN threads t ON t.id=a.thread_id LEFT JOIN messages m ON m.id=a.message_id WHERE a.kind='note' AND a.status='followup_pending' ORDER BY a.created_at")


@app.post("/api/followups/{aid}/handled")
def followup_handled(aid: int):
    n = db.run_rowcount("UPDATE actions SET status='done',decision_at=? WHERE id=? AND status='followup_pending'", (__import__("time").time(), aid))
    return {"ok": n == 1, "detail": "Marked handled externally. No follow-up was sent or scheduled here."}


class KillIn(BaseModel):
    on: StrictBool


@app.post("/api/kill-switch")
def kill(inp: KillIn):
    db.set_setting("kill_switch", "on" if inp.on else "off")
    return {"kill_switch": "on" if inp.on else "off"}


@app.get("/api/onboarding")
def onboarding_get():
    return {"done": bool(engine.onboarding_state()["done"]), "summary": engine.onboarding_summary(),
            "answers": engine.onboarding_state()["answers"], "next_question": engine.onboarding_next_question(),
            "editable": sorted(engine.EDITABLE_KEYS), "campaign_id": engine.onboarding_state().get("campaign_id"),
            "rules": (engine.get_campaign(engine.onboarding_state().get("campaign_id")) or {}).get("autonomy", {})}


class EditIn(BaseModel):
    key: str
    value: str


@app.post("/api/onboarding/edit")
def onboarding_edit(inp: EditIn):
    return engine.onboarding_edit(inp.key, inp.value)


@app.get("/api/digest")
def digest():
    return engine.digest()


@app.get("/api/digest/latest")
def digest_latest():
    raw = db.get_setting("digest_latest")
    return db.jload(raw, None) or {"empty": True}


@app.post("/api/llm/test")
async def llm_test():
    return await llm.test_connection()


def _sim_events():
    with open(SIM_PATH, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/sim/peek")
def sim_peek():
    idx = int(db.get_setting("sim_index", "0"))
    events = _sim_events()
    return {"remaining": max(0, len(events) - idx), "total": len(events)}


@app.post("/api/sim/tick")
async def sim_tick():
    """Deliver the next simulated inbound reply through the real pipeline."""
    if not engine.onboarding_state()["done"]:
        return {"error": "Finish the onboarding session first"}
    idx = int(db.get_setting("sim_index", "0"))
    events = _sim_events()
    if idx >= len(events):
        return {"done": True, "detail": "The simulator has finished its recorded messages"}
    ev = events[idx]
    camp = db.q("SELECT id FROM campaigns ORDER BY created_at DESC LIMIT 1", one=True)
    result = await engine.process_reply(
        campaign_id=camp["id"], external_id=ev["external_id"], subject=ev["subject"],
        contact_email=ev["contact_email"], contact_name=ev["contact_name"], body=ev["body"],
    )
    db.set_setting("sim_index", str(idx + 1))
    return {"done": False, "event": ev["contact_name"], "result": result}
