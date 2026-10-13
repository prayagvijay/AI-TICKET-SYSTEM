"""AI layer: ticket triage + reply drafting.

Uses Claude when credentials are available (ANTHROPIC_API_KEY etc.),
otherwise falls back to a local keyword-based engine so the app always works.
"""
import json
import os
import re

MODEL = "claude-opus-5-5"
CATEGORIES = ["billing", "technical", "account", "bug", "feature_request", "general"]
PRIORITIES = ["low", "medium", "high", "urgent"]
SENTIMENTS = ["positive", "neutral", "negative", "angry"]

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "priority": {"type": "string", "enum": PRIORITIES},
        "sentiment": {"type": "string", "enum": SENTIMENTS},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "suggested_reply": {"type": "string"},
    },
    "required": ["category", "priority", "sentiment", "summary", "tags", "suggested_reply"],
    "additionalProperties": False,
}

_client = None
_client_error = None


def _get_client():
    global _client, _client_error
    if _client is not None or _client_error is not None:
        return _client
    try:
        import anthropic

        has_creds = any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")) \
            or os.path.isdir(os.path.expanduser("~/.config/anthropic"))
        if not has_creds:
            raise RuntimeError("no Anthropic credentials found")
        _client = anthropic.Anthropic()
    except Exception as e:  # missing SDK or creds -> local engine
        _client_error = str(e)
    return _client


def engine_name():
    return f"Claude ({MODEL})" if _get_client() else "Local heuristic engine"


def _call_claude(system, prompt, schema=None, effort="low"):
    client = _get_client()
    kwargs = dict(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    output_config = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kwargs["output_config"] = output_config
    resp = client.beta.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    return next(b.text for b in resp.content if b.type == "text")


# ---------------------------------------------------------------- triage

def triage(subject, description):
    if _get_client():
        try:
            text = _call_claude(
                "You are a senior customer-support triage agent. Classify the ticket, judge urgency "
                "(urgent = outage/security/data loss/payment blocked for many; high = a customer is blocked; "
                "medium = degraded; low = questions/cosmetic), write a one-sentence summary, 2-5 short "
                "lowercase tags, and a warm, concrete first reply to the customer (no placeholders like [Name]).",
                f"Subject: {subject}\n\nDescription:\n{description}",
                TRIAGE_SCHEMA,
            )
            data = json.loads(text)
            data["engine"] = "claude"
            return data
        except Exception as e:
            print(f"[ai] Claude triage failed, using local engine: {e}")
    return _local_triage(subject, description)


def draft_reply(ticket, comments):
    if _get_client():
        try:
            history = "\n".join(f"[{c['author_role']}] {c['author_name']}: {c['body']}" for c in comments)
            return _call_claude(
                "You are a helpful, empathetic support agent. Write the next reply to the customer. "
                "Be concise, specific and actionable. Plain text only, no placeholders, no subject line.",
                f"Ticket #{ticket['id']}: {ticket['subject']}\nCategory: {ticket['category']}\n"
                f"Priority: {ticket['priority']}\n\nOriginal message:\n{ticket['description']}\n\n"
                f"Conversation so far:\n{history or '(none)'}",
                effort="medium",
            ).strip()
        except Exception as e:
            print(f"[ai] Claude reply failed, using local engine: {e}")
    return _local_reply(ticket)


# ---------------------------------------------------------------- local fallback engine

_KEYWORDS = {
    "billing": ["invoice", "charge", "charged", "refund", "payment", "billing", "subscription", "price", "card", "plan"],
    "account": ["login", "log in", "password", "account", "sign in", "signin", "2fa", "email change", "locked", "username"],
    "bug": ["bug", "error", "crash", "broken", "doesn't work", "not working", "fails", "exception", "glitch", "500"],
    "technical": ["api", "integration", "setup", "install", "configure", "server", "slow", "timeout", "sync", "database"],
    "feature_request": ["feature", "would be nice", "please add", "suggestion", "request", "could you add", "wish"],
}
_URGENT = ["urgent", "asap", "immediately", "outage", "down", "security", "breach", "hacked", "data loss", "production", "critical"]
_HIGH = ["blocked", "can't", "cannot", "unable", "not working", "charged twice", "locked out", "important"]
_ANGRY = ["ridiculous", "terrible", "worst", "unacceptable", "angry", "furious", "scam", "awful", "!!!"]
_NEG = ["frustrated", "annoyed", "disappointed", "problem", "issue", "bad", "unhappy"]
_POS = ["thanks", "thank you", "love", "great", "awesome", "appreciate"]

_REPLIES = {
    "billing": "I've pulled up your billing records and I'm reviewing the charge you mentioned. If anything was billed incorrectly, we'll issue a refund to your original payment method within 5-7 business days. I'll confirm here as soon as it's processed.",
    "account": "I'm sorry you're having trouble getting into your account. As a first step, please try the 'Forgot password' link on the sign-in page. If that doesn't work, reply here and I'll verify your identity and restore access manually.",
    "bug": "Thanks for reporting this, and sorry for the disruption. I've logged it with our engineering team. Could you share the steps that trigger it, your browser/app version, and a screenshot if possible? That will help us fix it faster.",
    "technical": "Thanks for the details. I'm looking into this now. Could you share any error messages or logs you're seeing, plus your configuration? In the meantime, our setup guide covers the most common causes of this behaviour.",
    "feature_request": "Thank you for the suggestion! I've passed it on to our product team, who review every request when planning the roadmap. I'll update this ticket if it gets scheduled.",
    "general": "Thanks for reaching out! I've received your message and I'm looking into it. I'll get back to you shortly with an update.",
}


def _has(text, words):
    return sum(1 for w in words if w in text)


def _local_triage(subject, description):
    text = f"{subject} {description}".lower()
    scores = {cat: _has(text, kws) for cat, kws in _KEYWORDS.items()}
    category = max(scores, key=scores.get) if max(scores.values()) > 0 else "general"

    if _has(text, _URGENT):
        priority = "urgent"
    elif _has(text, _HIGH):
        priority = "high"
    elif category in ("feature_request", "general"):
        priority = "low"
    else:
        priority = "medium"

    if _has(text, _ANGRY):
        sentiment = "angry"
    elif _has(text, _NEG):
        sentiment = "negative"
    elif _has(text, _POS):
        sentiment = "positive"
    else:
        sentiment = "neutral"

    tags = sorted({kw for kws in _KEYWORDS.values() for kw in kws if kw in text and " " not in kw})[:5]
    first = re.split(r"(?<=[.!?])\s", description.strip())[0] if description.strip() else subject
    summary = (first[:180] + "…") if len(first) > 180 else first
    reply = _REPLIES[category]
    if sentiment in ("angry", "negative"):
        reply = "I'm really sorry about this experience — I understand how frustrating it is. " + reply
    return {
        "category": category, "priority": priority, "sentiment": sentiment,
        "summary": summary, "tags": tags or [category], "suggested_reply": reply, "engine": "local",
    }


def _local_reply(ticket):
    return _REPLIES.get(ticket.get("category") or "general", _REPLIES["general"])
