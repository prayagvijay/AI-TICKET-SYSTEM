// HelpDesk AI - single-page frontend (vanilla JS, hash routing)
const state = { token: localStorage.getItem("token"), user: null, engine: "", agents: [] };
const $ = (sel, el = document) => el.querySelector(sel);
const app = $("#app");

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const label = (s) => (s || "").replace(/_/g, " ");
const isStaff = () => state.user && state.user.role !== "customer";
const STATUSES = ["open", "in_progress", "waiting", "resolved", "closed"];
const PRIORITIES = ["urgent", "high", "medium", "low"];
const CATEGORIES = ["billing", "technical", "account", "bug", "feature_request", "general"];
const SENTIMENT_ICON = { positive: "😊", neutral: "😐", negative: "😟", angry: "😡" };

function timeAgo(iso) {
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 2600);
}

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(state.token ? { Authorization: "Bearer " + state.token } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/login") { logoutLocal(); throw new Error("Session expired"); }
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function logoutLocal() {
  state.token = null; state.user = null;
  localStorage.removeItem("token");
  location.hash = "#/login";
}

const badge = {
  priority: (p) => `<span class="badge p-${p || "none"}">${p ? esc(p) : "untriaged"}</span>`,
  status: (s) => `<span class="badge s-${s}">${esc(label(s))}</span>`,
  category: (c) => (c ? `<span class="badge">${esc(label(c))}</span>` : ""),
};
const aiPending = (t) => t.ai_status === "pending" ? `<span class="badge pulse">✦ AI triaging…</span>` : "";

// ------------------------------------------------------------- layout

function renderNav() {
  const bar = $("#topbar");
  if (!state.user) { bar.hidden = true; return; }
  bar.hidden = false;
  const links = isStaff()
    ? [["#/dashboard", "Dashboard"], ["#/tickets", "All tickets"], ["#/tickets?assignee=me", "My queue"], ["#/new", "New ticket"]]
    : [["#/tickets", "My tickets"], ["#/new", "New ticket"]];
  $("#nav").innerHTML = links.map(([h, t]) => `<a href="${h}" class="${location.hash === h ? "active" : ""}">${t}</a>`).join("");
  $("#user-name").innerHTML = `${esc(state.user.name)} <span class="muted small">(${state.user.role})</span>`;
  $("#engine").textContent = "✦ " + state.engine;
}
$("#logout").onclick = async () => { try { await api("/logout", { method: "POST" }); } catch {} logoutLocal(); };

// ------------------------------------------------------------- views

function viewLogin() {
  let mode = "login";
  const draw = () => {
    app.innerHTML = `
      <div class="auth">
        <div class="brand"><span class="logo">✦</span> HelpDesk AI</div>
        <div class="panel">
          <div class="tabs"><button data-m="login" class="${mode === "login" ? "active" : ""}">Log in</button>
            <button data-m="register" class="${mode === "register" ? "active" : ""}">Sign up</button></div>
          <form id="auth-form">
            ${mode === "register" ? `<label>Name</label><input name="name" required>` : ""}
            <label>Email</label><input name="email" type="email" required autocomplete="username">
            <label>Password</label><input name="password" type="password" required autocomplete="current-password">
            <div class="error" id="err"></div>
            <button class="btn" style="width:100%;margin-top:6px">${mode === "login" ? "Log in" : "Create account"}</button>
          </form>
          <div class="demo">Demo accounts:<br>
            <button class="btn ghost sm" data-demo="agent@demo.com:agent123">Agent</button>
            <button class="btn ghost sm" data-demo="admin@demo.com:admin123">Admin</button>
            <button class="btn ghost sm" data-demo="customer@demo.com:customer123">Customer</button>
          </div>
        </div>
      </div>`;
    app.querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => { mode = b.dataset.m; draw(); }));
    app.querySelectorAll("[data-demo]").forEach((b) => (b.onclick = () => {
      const [e, p] = b.dataset.demo.split(":");
      mode = "login"; draw();
      $("[name=email]").value = e; $("[name=password]").value = p;
      $("#auth-form").requestSubmit();
    }));
    $("#auth-form").onsubmit = async (ev) => {
      ev.preventDefault();
      const body = Object.fromEntries(new FormData(ev.target));
      try {
        const data = await api(mode === "login" ? "/login" : "/register", { method: "POST", body });
        state.token = data.token; localStorage.setItem("token", data.token);
        await loadMe();
        location.hash = isStaff() ? "#/dashboard" : "#/tickets";
      } catch (e) { $("#err").textContent = e.message; }
    };
  };
  draw();
}

async function viewTickets(params) {
  const f = { q: params.get("q") || "", status: params.get("status") || "", priority: params.get("priority") || "",
              category: params.get("category") || "", assignee: params.get("assignee") || "" };
  const opt = (vals, cur, all) => `<option value="">${all}</option>` + vals.map((v) => `<option value="${v}" ${v === cur ? "selected" : ""}>${label(v)}</option>`).join("");
  app.innerHTML = `
    <div class="page-head">
      <div><h1>${f.assignee === "me" ? "My queue" : isStaff() ? "All tickets" : "My tickets"}</h1>
        <div class="muted" id="count">Loading…</div></div>
      <a class="btn" href="#/new">+ New ticket</a>
    </div>
    <form class="filters" id="filters">
      <input name="q" placeholder="Search subject, description or #id…" value="${esc(f.q)}">
      <select name="status">${opt(STATUSES, f.status, "Any status")}</select>
      <select name="priority">${opt(PRIORITIES, f.priority, "Any priority")}</select>
      <select name="category">${opt(CATEGORIES, f.category, "Any category")}</select>
      ${isStaff() ? `<select name="assignee"><option value="">Anyone</option>
        <option value="me" ${f.assignee === "me" ? "selected" : ""}>Assigned to me</option>
        <option value="unassigned" ${f.assignee === "unassigned" ? "selected" : ""}>Unassigned</option></select>` : ""}
    </form>
    <div class="ticket-list" id="list"></div>`;
  const form = $("#filters");
  const apply = () => {
    const qs = new URLSearchParams([...new FormData(form)].filter(([, v]) => v)).toString();
    location.hash = "#/tickets" + (qs ? "?" + qs : "");
  };
  form.onchange = apply;
  form.onsubmit = (e) => { e.preventDefault(); apply(); };

  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const load = async () => {
    const { tickets } = await api("/tickets" + (qs ? "?" + qs : ""));
    const list = $("#list");
    if (!list) return false;
    $("#count").textContent = `${tickets.length} ticket${tickets.length === 1 ? "" : "s"}`;
    list.innerHTML = tickets.length ? tickets.map((t) => `
      <a class="ticket-row" href="#/tickets/${t.id}">
        <div class="bar p-${t.priority || "none"}"></div>
        <div>
          <div class="title"><span class="muted">#${t.id}</span> ${esc(t.subject)}</div>
          <div class="summary">${esc(t.ai_summary || t.description.slice(0, 140))}</div>
          <div class="meta">${badge.status(t.status)} ${badge.priority(t.priority)} ${badge.category(t.category)} ${aiPending(t)}
            ${isStaff() && t.sentiment ? `<span title="${t.sentiment}">${SENTIMENT_ICON[t.sentiment] || ""}</span>` : ""}
            ${t.ai_tags.map((g) => `<span class="tag">${esc(g)}</span>`).join("")}</div>
        </div>
        <div class="right">${timeAgo(t.updated_at)}<br>
          ${isStaff() ? esc(t.customer_name) + "<br>" : ""}
          ${t.assignee_name ? "→ " + esc(t.assignee_name) : "<i>unassigned</i>"}<br>💬 ${t.comment_count}</div>
      </a>`).join("") : `<div class="panel empty">No tickets found.</div>`;
    return tickets.some((t) => t.ai_status === "pending");
  };
  poll(load);
}

function viewNew() {
  app.innerHTML = `
    <div class="page-head"><div><h1>New ticket</h1><div class="muted">Describe your issue — our AI will categorize, prioritize and route it instantly.</div></div></div>
    <form class="panel" id="new-form" style="max-width:720px">
      <label>Subject</label><input name="subject" maxlength="200" required placeholder="e.g. I was charged twice this month">
      <label>Description</label><textarea name="description" required rows="8" placeholder="Tell us what happened, what you expected, and any error messages…"></textarea>
      <div class="error" id="err"></div>
      <button class="btn">Submit ticket</button>
    </form>`;
  $("#new-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const btn = ev.target.querySelector("button");
    btn.disabled = true;
    try {
      const { ticket } = await api("/tickets", { method: "POST", body: Object.fromEntries(new FormData(ev.target)) });
      toast("Ticket created — AI is triaging it");
      location.hash = `#/tickets/${ticket.id}`;
    } catch (e) { $("#err").textContent = e.message; btn.disabled = false; }
  };
}

async function viewTicket(id) {
  app.innerHTML = `<div class="muted">Loading…</div>`;
  if (isStaff() && !state.agents.length) state.agents = (await api("/agents")).agents;
  let draft = "";

  const load = async () => {
    const { ticket: t, comments, events } = await api(`/tickets/${id}`);
    if (!location.hash.startsWith(`#/tickets/${id}`)) return false;
    const existing = $("#reply");
    if (existing) draft = existing.value;
    const sel = (name, vals, cur) => `<select data-field="${name}">${vals.map((v) => `<option value="${v}" ${v === cur ? "selected" : ""}>${label(v)}</option>`).join("")}</select>`;
    app.innerHTML = `
      <div class="page-head"><div>
        <a href="#/tickets" class="small">← Back to tickets</a>
        <h1><span class="muted">#${t.id}</span> ${esc(t.subject)}</h1>
        <div class="meta" style="display:flex;gap:6px;flex-wrap:wrap">${badge.status(t.status)} ${badge.priority(t.priority)} ${badge.category(t.category)} ${aiPending(t)}
          <span class="muted small">opened ${timeAgo(t.created_at)} by ${esc(t.customer_name)}</span></div>
      </div></div>
      <div class="detail">
        <div>
          <div class="msg"><div class="msg-head"><span><b>${esc(t.customer_name)}</b> · original request</span><span>${new Date(t.created_at).toLocaleString()}</span></div>
            <div class="msg-body">${esc(t.description)}</div></div>
          <div class="thread">${comments.map((c) => `
            <div class="msg ${c.author_role !== "customer" ? "staff" : ""} ${c.internal ? "internal" : ""}">
              <div class="msg-head"><span><b>${esc(c.author_name)}</b> ${c.author_role !== "customer" ? "· support" : ""} ${c.internal ? "· 🔒 internal note" : ""}</span>
                <span>${new Date(c.created_at).toLocaleString()}</span></div>
              <div class="msg-body">${esc(c.body)}</div></div>`).join("")}</div>
          ${t.status === "closed" ? `<div class="panel muted" style="margin-top:16px">This ticket is closed.</div>` : `
          <div class="panel composer">
            <textarea id="reply" placeholder="${isStaff() ? "Write a reply to the customer…" : "Add a reply…"}"></textarea>
            <div class="composer-actions">
              ${isStaff() ? `<button class="btn ai sm" id="ai-draft">✦ AI draft reply</button>
                ${t.ai_suggested_reply ? `<button class="btn ghost sm" id="use-suggest">Use triage suggestion</button>` : ""}
                <label class="check"><input type="checkbox" id="internal"> Internal note</label>` : ""}
              <span class="spacer"></span>
              <button class="btn" id="send">Send</button>
            </div>
          </div>`}
        </div>
        <div class="side">
          ${isStaff() ? `
          <div class="panel">
            <h3>Properties</h3>
            <div class="kv">
              <span>Status</span>${sel("status", STATUSES, t.status)}
              <span>Priority</span>${sel("priority", PRIORITIES, t.priority || "medium")}
              <span>Category</span>${sel("category", CATEGORIES, t.category || "general")}
              <span>Assignee</span><select data-field="assignee_id"><option value="">Unassigned</option>
                ${state.agents.map((a) => `<option value="${a.id}" ${a.id === t.assignee_id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
              <span>Customer</span><span>${esc(t.customer_name)}<br><span class="muted small">${esc(t.customer_email)}</span></span>
            </div>
          </div>
          <div class="panel ai-card">
            <h2>✦ AI insights</h2>
            ${t.ai_status === "pending" ? `<div class="muted"><span class="spinner"></span> Analyzing ticket…</div>` : `
            <div class="kv">
              <span class="muted">Sentiment</span><span style="text-transform:capitalize">${SENTIMENT_ICON[t.sentiment] || ""} ${esc(t.sentiment || "—")}</span>
              <span class="muted">Engine</span><span>${esc(t.ai_engine || "—")}</span>
            </div>
            <h3 style="margin-top:14px">Summary</h3><div class="small">${esc(t.ai_summary || "—")}</div>
            ${t.ai_tags.length ? `<div style="margin-top:10px;display:flex;gap:4px;flex-wrap:wrap">${t.ai_tags.map((g) => `<span class="tag">${esc(g)}</span>`).join("")}</div>` : ""}
            ${t.ai_suggested_reply ? `<h3 style="margin-top:14px">Suggested reply</h3><div class="suggest">${esc(t.ai_suggested_reply)}</div>` : ""}
            <button class="btn ghost sm" id="retriage">↻ Re-run triage</button>`}
          </div>
          <div class="panel"><h3>Activity</h3><ul class="timeline">
            ${events.map((e) => `<li><b>${esc(e.actor)}</b> ${esc(e.action)}<br>${timeAgo(e.created_at)}</li>`).join("")}</ul></div>
          ` : `
          <div class="panel"><h3>Details</h3><div class="kv">
            <span class="muted">Status</span><span>${badge.status(t.status)}</span>
            <span class="muted">Priority</span><span>${badge.priority(t.priority)}</span>
            <span class="muted">Category</span><span>${esc(label(t.category) || "—")}</span>
            <span class="muted">Agent</span><span>${esc(t.assignee_name || "Being assigned…")}</span></div>
            ${t.status !== "resolved" && t.status !== "closed" ? `<button class="btn ghost sm" style="margin-top:12px" id="cust-resolve">Mark as resolved</button>` : ""}</div>`}
        </div>
      </div>`;

    const reply = $("#reply");
    if (reply) reply.value = draft;
    app.querySelectorAll("[data-field]").forEach((s) => (s.onchange = async () => {
      const v = s.dataset.field === "assignee_id" ? (s.value ? Number(s.value) : null) : s.value;
      try { await api(`/tickets/${id}`, { method: "PATCH", body: { [s.dataset.field]: v } }); toast("Updated"); load(); }
      catch (e) { toast(e.message); }
    }));
    const on = (sel, fn) => { const el = $(sel); if (el) el.onclick = fn; };
    on("#send", async () => {
      const body = reply.value.trim();
      if (!body) return toast("Write something first");
      try {
        await api(`/tickets/${id}/comments`, { method: "POST", body: { body, internal: $("#internal")?.checked } });
        draft = ""; reply.value = ""; toast("Sent"); load();
      } catch (e) { toast(e.message); }
    });
    on("#use-suggest", () => { reply.value = t.ai_suggested_reply; reply.focus(); });
    on("#ai-draft", async (ev) => {
      const b = ev.target; b.disabled = true; b.innerHTML = `<span class="spinner"></span> Drafting…`;
      try { reply.value = (await api(`/tickets/${id}/ai-reply`, { method: "POST" })).reply; reply.focus(); }
      catch (e) { toast(e.message); }
      b.disabled = false; b.textContent = "✦ AI draft reply";
    });
    on("#retriage", async () => { await api(`/tickets/${id}/retriage`, { method: "POST" }); toast("Re-running AI triage"); poll(load); });
    on("#cust-resolve", async () => {
      await api(`/tickets/${id}/comments`, { method: "POST", body: { body: "✅ Customer marked this issue as resolved." } });
      toast("Thanks! Marked as resolved"); load();
    });
    return t.ai_status === "pending";
  };
  poll(load);
}

async function viewDashboard() {
  const s = await api("/stats");
  const chart = (title, obj, order) => {
    const entries = (order ? order.filter((k) => k in obj).map((k) => [k, obj[k]]) : Object.entries(obj).sort((a, b) => b[1] - a[1]));
    const max = Math.max(1, ...entries.map((e) => e[1]));
    return `<div class="panel"><h3>${title}</h3>${entries.map(([k, v]) => `
      <div class="hbar"><span>${esc(label(k))}</span><div class="track"><div class="fill" style="width:${(v / max) * 100}%"></div></div><span class="n">${v}</span></div>`).join("") || '<div class="muted">No data</div>'}</div>`;
  };
  app.innerHTML = `
    <div class="page-head"><div><h1>Dashboard</h1><div class="muted">Welcome back, ${esc(state.user.name)}</div></div>
      <a class="btn" href="#/tickets?assignee=me">Open my queue</a></div>
    <div class="stats">
      <a class="panel stat" href="#/tickets"><div class="num">${s.open}</div><div class="lbl">Open tickets</div></a>
      <a class="panel stat" href="#/tickets?priority=urgent"><div class="num" style="color:var(--urgent)">${s.urgent}</div><div class="lbl">Urgent & open</div></a>
      <a class="panel stat" href="#/tickets?assignee=me"><div class="num">${s.mine}</div><div class="lbl">Assigned to me</div></a>
      <div class="panel stat"><div class="num">${s.avg_resolution_hours ?? "—"}${s.avg_resolution_hours != null ? "<span class='small muted'> h</span>" : ""}</div><div class="lbl">Avg. resolution time</div></div>
      <div class="panel stat"><div class="num">${s.total}</div><div class="lbl">Total tickets</div></div>
    </div>
    <div class="charts">
      ${chart("By status", s.by_status, STATUSES)}
      ${chart("By priority", s.by_priority, [...PRIORITIES, "untriaged"])}
      ${chart("By category", s.by_category)}
      ${chart("Customer sentiment (AI)", s.by_sentiment, ["positive", "neutral", "negative", "angry", "untriaged"])}
    </div>`;
}

// ------------------------------------------------------------- polling + router

let pollTimer = null;
async function poll(load) {
  clearTimeout(pollTimer);
  const tick = async () => {
    try { if (await load()) pollTimer = setTimeout(tick, 1500); } catch (e) { toast(e.message); }
  };
  tick();
}

async function loadMe() {
  const data = await api("/me");
  state.user = data.user; state.engine = data.ai_engine;
}

async function router() {
  clearTimeout(pollTimer);
  const [path, qs] = (location.hash.slice(1) || "/").split("?");
  const params = new URLSearchParams(qs || "");
  if (!state.token) { renderNav(); return viewLogin(); }
  if (!state.user) { try { await loadMe(); } catch { return; } }
  renderNav();
  try {
    const m = path.match(/^\/tickets\/(\d+)$/);
    if (m) return await viewTicket(m[1]);
    if (path === "/tickets") return await viewTickets(params);
    if (path === "/new") return viewNew();
    if (path === "/dashboard" && isStaff()) return await viewDashboard();
    location.hash = isStaff() ? "#/dashboard" : "#/tickets";
  } catch (e) {
    app.innerHTML = `<div class="panel empty">${esc(e.message)}<br><br><a href="#/tickets">Back to tickets</a></div>`;
  }
}

window.addEventListener("hashchange", router);
router();
