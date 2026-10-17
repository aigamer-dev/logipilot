// Ops desk: lists tickets from /api/admin/tickets and posts staff replies to /api/tickets/{id}/reply.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const api = (window.LOGIPILOT_API || "") + "/api";
const $ = (id) => document.getElementById(id);
const pillClass = { open: "open", answered: "ok", closed: "no" };

// The token lives only for this browser tab, never in localStorage.
try { $("token").value = sessionStorage.getItem("lp-admin") || ""; } catch (_) {}

function headers() {
  return { "X-Admin-Token": $("token").value, "Content-Type": "application/json" };
}

function card(t) {
  const acts = (t.actions || []).map((a) => `${esc(a.kind)} <code>${esc(a.action_id)}</code>`).join(", ") || "none";
  const v = t.verification;
  const closed = t.status === "closed";
  return `<article class="ticket" data-id="${esc(t.ticket_id)}">
    <h3>${esc(t.ticket_id)}</h3>
    <div class="meta"><span class="pill ${pillClass[t.status] || ""}">${esc(t.status)}</span>
      <span>${esc(t.category || "general")}</span><span class="muted">${esc((t.tracking_ids || []).join(", "))}</span>
      <span class="muted">${t.created_at ? esc(new Date(t.created_at).toLocaleString()) : ""}</span></div>
    <blockquote>${esc(t.request)}</blockquote>
    <div><b>Why it was escalated:</b> ${esc(t.reason)}</div>
    <details><summary>What the AI did</summary>
      <div>Actions: ${acts}</div>
      ${v ? `<div class="muted">Verifier: resolves ${esc(v.resolves)}, grounded ${esc(v.grounded)}, policy ${esc(v.policy_ok)}</div>` : ""}
      <div class="ai">${esc(t.ai_answer)}</div></details>
    ${t.human_reply ? `<div><b>Your earlier reply:</b> ${esc(t.human_reply)}</div>` : ""}
    ${closed ? "" : `<label class="muted" for="r-${esc(t.ticket_id)}">Reply to the customer</label>
    <textarea id="r-${esc(t.ticket_id)}" maxlength="2000"></textarea>
    <div class="row"><button class="btn" data-close="0">Send reply</button><button class="btn primary" data-close="1">Send and close</button></div>`}
  </article>`;
}

async function load() {
  try { sessionStorage.setItem("lp-admin", $("token").value); } catch (_) {}
  $("msg").textContent = "Loading…";
  const r = await fetch(`${api}/admin/tickets?status=${encodeURIComponent($("status").value)}`, { headers: headers() }).catch(() => null);
  if (!r || !r.ok) {
    $("msg").textContent = r && r.status === 403 ? "That admin token isn't valid." : "Couldn't load tickets. Try again.";
    $("list").innerHTML = "";
    return;
  }
  const { tickets } = await r.json();
  $("msg").textContent = tickets.length ? `${tickets.length} ticket(s)` : "No tickets here.";
  $("list").innerHTML = tickets.map(card).join("");
}

$("auth").addEventListener("submit", (e) => { e.preventDefault(); load(); });
$("status").addEventListener("change", load);

$("list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-close]");
  if (!btn) return;
  const box = btn.closest(".ticket");
  const text = box.querySelector("textarea").value.trim();
  if (!text) { box.querySelector("textarea").focus(); $("msg").textContent = "Write a reply first."; return; }
  btn.disabled = true;
  const r = await fetch(`${api}/tickets/${encodeURIComponent(box.dataset.id)}/reply`, {
    method: "POST", headers: headers(), body: JSON.stringify({ message: text, close: btn.dataset.close === "1" }),
  }).catch(() => null);
  btn.disabled = false;
  $("msg").textContent = r && r.ok ? `Reply sent on ${box.dataset.id}.` : "Couldn't send the reply. Try again.";
  if (r && r.ok) load();
});
