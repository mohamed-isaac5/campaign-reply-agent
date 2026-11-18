import os
import sys
import time

os.environ["CRA_DB"] = "/tmp/cra_test_api.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import db  # noqa: E402
from app import app  # noqa: E402


def test_review_shows_context_and_approve_records_edited_body():
    if os.path.exists("/tmp/cra_test_api.db"):
        os.remove("/tmp/cra_test_api.db")
    db.init()
    cid = db.run("INSERT INTO campaigns (name, status, goal, steps, tone, facts, autonomy, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 ("test", "active", "", "[]", "", db.jdump({}), db.jdump({}), time.time()))
    tid = db.run("INSERT INTO threads (campaign_id, external_ref, subject, contact_email, contact_name, created_at) VALUES (?,?,?,?,?,?)",
                 (cid, "r1", "subject", "c@x.com", "Karim", time.time()))
    mid = db.run("INSERT INTO messages (thread_id, direction, body, classification, class_reason, confidence, external_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (tid, "in", "Is a discount possible?", "question", "reason", 0.8, "rm1", time.time()))
    aid = db.run("INSERT INTO actions (thread_id, message_id, kind, status, body, reason, created_at) VALUES (?,?,?,?,?,?,?)",
                 (tid, mid, "draft", "pending", "first draft", "specific escalation reason", time.time()))
    client = TestClient(app)
    items = client.get("/api/review").json()
    assert items[0]["thread_messages"][0]["body"] == "Is a discount possible?"
    assert items[0]["campaign_name"] == "test"
    r = client.post(f"/api/review/{aid}", json={"decision": "approve", "body": "Only 10% off"}).json()
    assert r["final_body"] == "Only 10% off"
    out = db.q("SELECT * FROM messages WHERE direction = 'out' AND thread_id = ?", (tid,), one=True)
    assert out["body"] == "Only 10% off"
    again = client.post(f"/api/review/{aid}", json={"decision": "approve"}).json()
    assert again["ok"] is False  # no double-approve
