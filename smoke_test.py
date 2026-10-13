"""End-to-end smoke test. Usage: python smoke_test.py [base_url]"""
import json, sys, time, urllib.request, urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api"

def call(method, path, token=None, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: sys.exit(1)

s, d = call("POST", "/login", body={"email": "agent@demo.com", "password": "agent123"}); ok(s == 200, "agent login"); A = d["token"]
s, d = call("POST", "/login", body={"email": "customer@demo.com", "password": "customer123"}); ok(s == 200, "customer login"); C = d["token"]
ok(call("POST", "/login", body={"email": "agent@demo.com", "password": "nope"})[0] == 401, "bad password rejected")
s, d = call("GET", "/me", A); print("     engine:", d["ai_engine"])
s, d = call("GET", "/tickets", A); ok(s == 200 and len(d["tickets"]) >= 4, f"seeded tickets listed ({len(d['tickets'])})")
for t in d["tickets"]: print(f"     #{t['id']} {t['priority']:7} {t['category']:16} {t['sentiment']:8} -> {t['assignee_name']}  {t['subject']}")
s, d = call("POST", "/tickets", C, {"subject": "Refund please", "description": "I was charged for a plan I cancelled. Frustrated."})
ok(s == 201, "customer creates ticket"); tid = d["ticket"]["id"]
for _ in range(60):
    t = call("GET", f"/tickets/{tid}", A)[1]["ticket"]
    if t["ai_status"] != "pending": break
    time.sleep(1)
ok(t["ai_status"] == "done" and t["priority"] and t["assignee_name"], f"AI triaged: {t['category']}/{t['priority']}, assigned {t['assignee_name']}")
ok(call("POST", f"/tickets/{tid}/comments", A, {"body": "On it"})[0] == 201, "agent reply")
ok(call("GET", f"/tickets/{tid}", A)[1]["ticket"]["status"] == "waiting", "status auto -> waiting")
ok(call("POST", f"/tickets/{tid}/comments", A, {"body": "secret", "internal": True})[0] == 201, "internal note")
cust_view = call("GET", f"/tickets/{tid}", C)[1]
ok(all(not c["internal"] for c in cust_view["comments"]) and "ai_suggested_reply" not in cust_view["ticket"], "customer can't see internal notes/AI draft")
s, d = call("PATCH", f"/tickets/{tid}", A, {"status": "resolved", "assignee_id": 3}); ok(s == 200 and d["ticket"]["status"] == "resolved", "agent updates status/assignee")
s, d = call("POST", f"/tickets/{tid}/ai-reply", A); ok(s == 200 and d["reply"], "AI draft reply: " + d["reply"][:70] + "…")
s, d = call("GET", "/stats", A); ok(s == 200 and d["total"] >= 5, f"stats: {d['open']} open, {d['urgent']} urgent")
ok(call("GET", "/stats", C)[0] == 403, "customer blocked from stats")
ok(call("PATCH", f"/tickets/{tid}", C, {"status": "closed"})[0] == 403, "customer can't edit ticket")
ok(call("GET", "/tickets/1", None)[0] == 401, "anonymous blocked")
s, d = call("GET", "/tickets?priority=urgent", A); ok(all(t["priority"] == "urgent" for t in d["tickets"]), "priority filter")
print("\nAll checks passed.")
