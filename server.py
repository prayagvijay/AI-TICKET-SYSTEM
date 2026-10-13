"""AI Ticket System - backend.

Pure standard library: http.server + sqlite3. Run:  python server.py  (port 8000 by default)
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import ai

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("TICKETS_DB", os.path.join(BASE, "tickets.db"))
STATIC = os.path.join(BASE, "static")
PORT = int(os.environ.get("PORT", "8000"))
STATUSES = ["open", "in_progress", "waiting", "resolved", "closed"]

_local = threading.local()


def db():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _local.conn = conn
    return conn


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_pw(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${digest}"


def check_pw(password, stored):
    salt, _ = stored.split("$", 1)
    return secrets.compare_digest(hash_pw(password, salt), stored)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('customer','agent','admin')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY, subject TEXT NOT NULL, description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', priority TEXT, category TEXT, sentiment TEXT,
    ai_summary TEXT, ai_tags TEXT, ai_suggested_reply TEXT, ai_engine TEXT, ai_status TEXT DEFAULT 'pending',
    customer_id INTEGER NOT NULL REFERENCES users(id), assignee_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL, internal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    actor TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_customer ON tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);
"""

SEED_USERS = [
    ("Admin", "admin@demo.com", "admin123", "admin"),
    ("Alex Agent", "agent@demo.com", "agent123", "agent"),
    ("Sam Support", "sam@demo.com", "agent123", "agent"),
    ("Casey Customer", "customer@demo.com", "customer123", "customer"),
]
SEED_TICKETS = [
    ("Charged twice for my subscription", "I was charged twice this month for the Pro plan. Please refund the duplicate charge ASAP, this is unacceptable."),
    ("Can't log in after password reset", "I reset my password but now I'm locked out and can't sign in. I need access for a client meeting today."),
    ("Dark mode please", "Would be nice to have a dark mode in the dashboard. Love the product otherwise, thanks!"),
    ("API returns 500 on /export", "Our production integration is down: the /export API endpoint returns 500 errors since this morning. Critical for us."),
]


def init_db():
    conn = db()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        for name, email, pw, role in SEED_USERS:
            conn.execute("INSERT INTO users(name,email,password,role,created_at) VALUES(?,?,?,?,?)",
                         (name, email, hash_pw(pw), role, now()))
        cust = conn.execute("SELECT id FROM users WHERE email='customer@demo.com'").fetchone()[0]
        for subject, desc in SEED_TICKETS:
            tid = create_ticket_row(cust, subject, desc, "Casey Customer")
            run_triage(tid)  # synchronous on first boot so demo data is ready
    conn.commit()


# ---------------------------------------------------------------- domain helpers

def log_event(ticket_id, actor, action):
    db().execute("INSERT INTO events(ticket_id,actor,action,created_at) VALUES(?,?,?,?)", (ticket_id, actor, action, now()))


def create_ticket_row(customer_id, subject, description, actor_name):
    conn = db()
    ts = now()
    cur = conn.execute(
        "INSERT INTO tickets(subject,description,customer_id,created_at,updated_at) VALUES(?,?,?,?,?)",
        (subject, description, customer_id, ts, ts))
    log_event(cur.lastrowid, actor_name, "created the ticket")
    conn.commit()
    return cur.lastrowid


def pick_assignee(conn):
    """Auto-assign to the agent with the fewest active tickets."""
    row = conn.execute("""
        SELECT u.id, u.name FROM users u
        LEFT JOIN tickets t ON t.assignee_id = u.id AND t.status NOT IN ('resolved','closed')
        WHERE u.role IN ('agent','admin') GROUP BY u.id ORDER BY COUNT(t.id), u.id LIMIT 1""").fetchone()
    return row


def run_triage(ticket_id):
    conn = db()
    t = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not t:
        return
    result = ai.triage(t["subject"], t["description"])
    assignee = pick_assignee(conn) if t["assignee_id"] is None else None
    conn.execute("""UPDATE tickets SET priority=?, category=?, sentiment=?, ai_summary=?, ai_tags=?,
                    ai_suggested_reply=?, ai_engine=?, ai_status='done', updated_at=?,
                    assignee_id=COALESCE(assignee_id, ?) WHERE id=?""",
                 (result["priority"], result["category"], result["sentiment"], result["summary"],
                  json.dumps(result["tags"]), result["suggested_reply"], result["engine"], now(),
                  assignee["id"] if assignee else None, ticket_id))
    log_event(ticket_id, "AI", f"triaged as {result['category']} / {result['priority']} priority ({result['engine']})")
    if assignee:
        log_event(ticket_id, "AI", f"auto-assigned to {assignee['name']}")
    conn.commit()


def triage_async(ticket_id):
    def work():
        try:
            run_triage(ticket_id)
        except Exception as e:
            print(f"[triage] ticket {ticket_id} failed: {e}")
            db().execute("UPDATE tickets SET ai_status='failed' WHERE id=?", (ticket_id,))
            db().commit()
    threading.Thread(target=work, daemon=True).start()


TICKET_SELECT = """
SELECT t.*, c.name AS customer_name, c.email AS customer_email, a.name AS assignee_name,
       (SELECT COUNT(*) FROM comments WHERE ticket_id=t.id) AS comment_count
FROM tickets t JOIN users c ON c.id=t.customer_id LEFT JOIN users a ON a.id=t.assignee_id
"""


def ticket_dict(row):
    d = dict(row)
    d["ai_tags"] = json.loads(d["ai_tags"]) if d.get("ai_tags") else []
    return d


def public_user(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"]}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- HTTP layer

ROUTES = []


def route(method, pattern, auth=True, staff=False):
    regex = re.compile("^" + re.sub(r":(\w+)", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, regex, fn, auth, staff))
        return fn
    return deco


MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "AITickets/1.0"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ApiError(400, "Invalid JSON body")

    def current_user(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        return db().execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                            (header[7:],)).fetchone()

    def dispatch(self, method):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return self.serve_static(url.path) if method == "GET" else self.send_json(404, {"error": "Not found"})
        try:
            for m, regex, fn, needs_auth, staff in ROUTES:
                match = regex.match(url.path)
                if m == method and match:
                    user = self.current_user()
                    if needs_auth and not user:
                        raise ApiError(401, "Please log in")
                    if staff and user["role"] == "customer":
                        raise ApiError(403, "Agents only")
                    query = {k: v[0] for k, v in parse_qs(url.query).items()}
                    body = self.read_json() if method in ("POST", "PATCH") else {}
                    status, payload = fn(user=user, body=body, query=query, **match.groupdict())
                    return self.send_json(status, payload)
            raise ApiError(404, "Not found")
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:
            print(f"[error] {method} {url.path}: {e!r}")
            self.send_json(500, {"error": "Internal server error"})

    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(os.path.realpath(STATIC)) or not os.path.isfile(full):
            full = os.path.join(STATIC, "index.html")  # SPA fallback
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")


def require(body, *fields):
    out = []
    for f in fields:
        v = (body.get(f) or "").strip() if isinstance(body.get(f), str) else body.get(f)
        if not v:
            raise ApiError(400, f"'{f}' is required")
        out.append(v)
    return out


def get_ticket_for(user, ticket_id):
    row = db().execute(TICKET_SELECT + " WHERE t.id=?", (ticket_id,)).fetchone()
    if not row or (user["role"] == "customer" and row["customer_id"] != user["id"]):
        raise ApiError(404, "Ticket not found")
    return row


# ---------------------------------------------------------------- routes: auth

def new_session(user_id):
    token = secrets.token_urlsafe(32)
    db().execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", (token, user_id, now()))
    db().commit()
    return token


@route("POST", "/api/login", auth=False)
def login(user, body, query):
    email, password = require(body, "email", "password")
    u = db().execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
    if not u or not check_pw(password, u["password"]):
        raise ApiError(401, "Invalid email or password")
    return 200, {"token": new_session(u["id"]), "user": public_user(u)}


@route("POST", "/api/register", auth=False)
def register(user, body, query):
    name, email, password = require(body, "name", "email", "password")
    email = email.lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ApiError(400, "Invalid email address")
    if len(password) < 6:
        raise ApiError(400, "Password must be at least 6 characters")
    try:
        cur = db().execute("INSERT INTO users(name,email,password,role,created_at) VALUES(?,?,?,?,?)",
                           (name, email, hash_pw(password), "customer", now()))
    except sqlite3.IntegrityError:
        raise ApiError(409, "An account with this email already exists")
    db().commit()
    u = db().execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return 201, {"token": new_session(u["id"]), "user": public_user(u)}


@route("POST", "/api/logout")
def logout(user, body, query):
    db().execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    db().commit()
    return 200, {"ok": True}


@route("GET", "/api/me")
def me(user, body, query):
    return 200, {"user": public_user(user), "ai_engine": ai.engine_name()}


@route("GET", "/api/agents", staff=True)
def agents(user, body, query):
    rows = db().execute("SELECT * FROM users WHERE role IN ('agent','admin') ORDER BY name").fetchall()
    return 200, {"agents": [public_user(r) for r in rows]}


# ---------------------------------------------------------------- routes: tickets

@route("GET", "/api/tickets")
def list_tickets(user, body, query):
    where, params = [], []
    if user["role"] == "customer":
        where.append("t.customer_id=?")
        params.append(user["id"])
    for field in ("status", "priority", "category"):
        if query.get(field):
            where.append(f"t.{field}=?")
            params.append(query[field])
    if query.get("assignee") == "me":
        where.append("t.assignee_id=?")
        params.append(user["id"])
    elif query.get("assignee") == "unassigned":
        where.append("t.assignee_id IS NULL")
    if query.get("q"):
        where.append("(t.subject LIKE ? OR t.description LIKE ? OR t.ai_summary LIKE ? OR CAST(t.id AS TEXT)=?)")
        like = f"%{query['q']}%"
        params += [like, like, like, query["q"].lstrip("#")]
    sql = TICKET_SELECT + (" WHERE " + " AND ".join(where) if where else "") + """
        ORDER BY CASE t.status WHEN 'resolved' THEN 1 WHEN 'closed' THEN 2 ELSE 0 END,
                 CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                 t.updated_at DESC"""
    rows = db().execute(sql, params).fetchall()
    return 200, {"tickets": [ticket_dict(r) for r in rows]}


@route("POST", "/api/tickets")
def create_ticket(user, body, query):
    subject, description = require(body, "subject", "description")
    if len(subject) > 200:
        raise ApiError(400, "Subject is too long (max 200 characters)")
    tid = create_ticket_row(user["id"], subject, description, user["name"])
    triage_async(tid)
    return 201, {"ticket": ticket_dict(get_ticket_for(user, tid))}


@route("GET", "/api/tickets/:tid")
def get_ticket(user, body, query, tid):
    t = get_ticket_for(user, tid)
    sql = """SELECT c.*, u.name AS author_name, u.role AS author_role FROM comments c
             JOIN users u ON u.id=c.author_id WHERE c.ticket_id=?"""
    if user["role"] == "customer":
        sql += " AND c.internal=0"
    comments = [dict(r) for r in db().execute(sql + " ORDER BY c.id", (tid,)).fetchall()]
    events = [dict(r) for r in db().execute("SELECT * FROM events WHERE ticket_id=? ORDER BY id", (tid,)).fetchall()]
    d = ticket_dict(t)
    if user["role"] == "customer":
        for k in ("ai_suggested_reply", "sentiment"):
            d.pop(k, None)
    return 200, {"ticket": d, "comments": comments, "events": events if user["role"] != "customer" else []}


@route("PATCH", "/api/tickets/:tid", staff=True)
def update_ticket(user, body, query, tid):
    t = get_ticket_for(user, tid)
    allowed = {"status": STATUSES, "priority": ai.PRIORITIES, "category": ai.CATEGORIES}
    sets, params = [], []
    for field, values in allowed.items():
        if field in body and body[field] != t[field]:
            if body[field] not in values:
                raise ApiError(400, f"Invalid {field}")
            sets.append(f"{field}=?")
            params.append(body[field])
            log_event(tid, user["name"], f"changed {field} from {t[field] or '—'} to {body[field]}")
            if field == "status":
                sets.append("resolved_at=?")
                params.append(now() if body[field] in ("resolved", "closed") else None)
    if "assignee_id" in body and body["assignee_id"] != t["assignee_id"]:
        aid = body["assignee_id"] or None
        name = "nobody"
        if aid:
            a = db().execute("SELECT * FROM users WHERE id=? AND role IN ('agent','admin')", (aid,)).fetchone()
            if not a:
                raise ApiError(400, "Invalid assignee")
            name = a["name"]
        sets.append("assignee_id=?")
        params.append(aid)
        log_event(tid, user["name"], f"assigned to {name}")
    if sets:
        sets.append("updated_at=?")
        params += [now(), tid]
        db().execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id=?", params)
        db().commit()
    return 200, {"ticket": ticket_dict(get_ticket_for(user, tid))}


@route("POST", "/api/tickets/:tid/comments")
def add_comment(user, body, query, tid):
    t = get_ticket_for(user, tid)
    (text,) = require(body, "body")
    internal = 1 if body.get("internal") and user["role"] != "customer" else 0
    db().execute("INSERT INTO comments(ticket_id,author_id,body,internal,created_at) VALUES(?,?,?,?,?)",
                 (tid, user["id"], text, internal, now()))
    new_status = t["status"]
    if not internal:
        if user["role"] == "customer" and t["status"] in ("waiting", "resolved"):
            new_status = "open"
        elif user["role"] != "customer" and t["status"] == "open":
            new_status = "waiting"
    if new_status != t["status"]:
        log_event(tid, "System", f"status changed from {t['status']} to {new_status}")
    db().execute("UPDATE tickets SET updated_at=?, status=? WHERE id=?", (now(), new_status, tid))
    db().commit()
    return 201, {"ok": True}


@route("POST", "/api/tickets/:tid/ai-reply", staff=True)
def ai_reply(user, body, query, tid):
    t = get_ticket_for(user, tid)
    comments = [dict(r) for r in db().execute(
        """SELECT c.body, u.name AS author_name, u.role AS author_role FROM comments c
           JOIN users u ON u.id=c.author_id WHERE c.ticket_id=? AND c.internal=0 ORDER BY c.id""", (tid,)).fetchall()]
    return 200, {"reply": ai.draft_reply(dict(t), comments)}


@route("POST", "/api/tickets/:tid/retriage", staff=True)
def retriage(user, body, query, tid):
    get_ticket_for(user, tid)
    db().execute("UPDATE tickets SET ai_status='pending' WHERE id=?", (tid,))
    db().commit()
    triage_async(int(tid))
    return 202, {"ok": True}


@route("GET", "/api/stats", staff=True)
def stats(user, body, query):
    conn = db()
    count = lambda sql, *p: conn.execute(sql, p).fetchone()[0]
    by = lambda col: {r[0] or "untriaged": r[1] for r in conn.execute(
        f"SELECT {col}, COUNT(*) FROM tickets GROUP BY {col}").fetchall()}
    avg = conn.execute("""SELECT AVG((julianday(resolved_at)-julianday(created_at))*24) FROM tickets
                          WHERE resolved_at IS NOT NULL""").fetchone()[0]
    return 200, {
        "total": count("SELECT COUNT(*) FROM tickets"),
        "open": count("SELECT COUNT(*) FROM tickets WHERE status NOT IN ('resolved','closed')"),
        "urgent": count("SELECT COUNT(*) FROM tickets WHERE priority='urgent' AND status NOT IN ('resolved','closed')"),
        "mine": count("SELECT COUNT(*) FROM tickets WHERE assignee_id=? AND status NOT IN ('resolved','closed')", user["id"]),
        "avg_resolution_hours": round(avg, 1) if avg is not None else None,
        "by_status": by("status"), "by_priority": by("priority"),
        "by_category": by("category"), "by_sentiment": by("sentiment"),
    }


def main():
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"AI Ticket System running at http://localhost:{PORT}  (AI engine: {ai.engine_name()})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")


if __name__ == "__main__":
    main()
