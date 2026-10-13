# ✦ HelpDesk AI: AI-Powered Support Ticket System

A full-stack customer support ticketing system that uses AI to **triage, prioritize, route, and draft replies** for every ticket automatically. Customers submit issues. The AI categorizes each one, detects urgency and sentiment, writes a summary, and assigns it to the least-busy agent before a human ever looks at it.

Built from scratch with **zero frontend frameworks** and a **standard-library Python backend**. The only dependency is the Anthropic SDK.

## Features

**AI**
- 🧠 **Auto-triage:** every new ticket gets a category (billing, technical, account, bug, feature request, general), a priority (urgent → low), a sentiment rating, a one-line summary, and tags
- ✍️ **AI reply drafting:** one click writes a context-aware reply using the whole conversation history
- 💡 **Suggested first response:** generated during triage, ready to send
- 🔀 **Smart routing:** auto-assigns each ticket to the agent with the fewest open tickets
- 🛟 **Works offline:** with no API key, a built-in keyword engine handles triage, so the app always runs

**Helpdesk**
- 👥 Role-based access: **customers**, **agents**, and **admins**
- 🎫 Ticket lifecycle: open → in progress → waiting → resolved → closed, with automatic status changes on replies
- 💬 Conversation threads with **internal notes** (hidden from customers)
- 🔍 Search plus filters by status, priority, category, and assignee
- 📊 Dashboard with open/urgent counts, average resolution time, and breakdowns by status, priority, category, and sentiment
- 🕓 Full activity timeline for every ticket
- 🌗 Responsive UI with automatic dark mode

## Tech stack

| Layer    | Tech |
|----------|------|
| Frontend | Vanilla JavaScript SPA (hash routing), hand-written CSS |
| Backend  | Python 3 standard library (`http.server`, `ThreadingHTTPServer`), REST JSON API |
| Database | SQLite (WAL mode) |
| AI       | Claude (`claude-opus-5-5`) via the Anthropic Python SDK, with structured JSON outputs. Falls back to a local heuristic engine |
| Auth     | PBKDF2-SHA256 password hashing, bearer session tokens |

## Quick start

```bash
./run.sh
```

Then open **http://localhost:8000**.

To enable Claude-powered AI, set your API key before starting:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
./run.sh
```

### Demo accounts

| Role     | Email               | Password      |
|----------|---------------------|---------------|
| Admin    | admin@demo.com      | admin123      |
| Agent    | agent@demo.com      | agent123      |
| Customer | customer@demo.com   | customer123   |

Anyone can also sign up as a new customer.

## Project structure

```
ai-ticket-system/
├── server.py        # HTTP server, REST API, auth, SQLite data layer
├── ai.py            # Claude triage + reply drafting, local fallback engine
├── static/
│   ├── index.html   # SPA shell
│   ├── app.js       # All frontend views and logic
│   └── style.css    # Styles (light + dark)
├── smoke_test.py    # End-to-end API test
├── run.sh           # One-command setup + start
└── requirements.txt
```

## API

| Method | Endpoint | Who | Description |
|--------|----------|-----|-------------|
| POST | `/api/register` | public | Create customer account |
| POST | `/api/login` | public | Log in, returns token |
| POST | `/api/logout` | user | End session |
| GET  | `/api/me` | user | Current user + active AI engine |
| GET  | `/api/tickets?q=&status=&priority=&category=&assignee=me` | user | List/search tickets (customers see only their own) |
| POST | `/api/tickets` | user | Create ticket (AI triage runs in background) |
| GET  | `/api/tickets/:id` | user | Ticket + comments + activity |
| PATCH| `/api/tickets/:id` | agent | Update status, priority, category, assignee |
| POST | `/api/tickets/:id/comments` | user | Reply or internal note |
| POST | `/api/tickets/:id/ai-reply` | agent | Generate an AI reply draft |
| POST | `/api/tickets/:id/retriage` | agent | Re-run AI triage |
| GET  | `/api/agents` | agent | List agents |
| GET  | `/api/stats` | agent | Dashboard metrics |

## Testing

```bash
./run.sh                                  # in one terminal
.venv/bin/python smoke_test.py            # in another
```

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `PORT` | `8000` | Server port |
| `TICKETS_DB` | `./tickets.db` | SQLite database path |
| `ANTHROPIC_API_KEY` | none | Enables Claude AI (otherwise uses the local engine) |

## License

MIT
