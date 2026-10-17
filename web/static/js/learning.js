// Renders /api/learning: loop status, active rules with their evidence, decisions, eval set.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pct = (x) => (typeof x === "number" ? (x * 100).toFixed(0) + "%" : "—");
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "never");

async function load() {
  let d;
  try {
    const r = await fetch((window.LOGIPILOT_API || "") + "/api/learning");
    if (!r.ok) throw new Error(r.status);
    d = await r.json();
  } catch (e) {
    document.getElementById("state").textContent = "Couldn't load learning data right now. Try again in a moment.";
    return;
  }
  const st = d.state || {};
  const totalCases = Object.values(d.eval_cases || {}).reduce((a, b) => a + b, 0);
  document.getElementById("state").innerHTML = [
    ["Last cycle", when(st.last_run_at)],
    ["Triggered by", st.last_trigger || "—"],
    ["Active rules", (d.active_rules || []).length],
    ["Eval cases", totalCases],
    ["From your 👍", d.regression_cases_from_feedback || 0],
  ].map(([k, v]) => `<div class="card-s">${esc(k)}<b>${esc(v)}</b></div>`).join("");

  const rules = d.active_rules || [];
  document.getElementById("rules").innerHTML = rules.length
    ? `<table class="lp"><tr><th>Skill</th><th>Rule</th><th>Score before → after</th></tr>${rules
        .map((r) => `<tr><td>${esc(r.category)}</td><td>${esc(r.rule)}</td><td>${pct(r.baseline)} → ${pct(r.score)}</td></tr>`)
        .join("")}</table>`
    : `<p class="muted">No rules yet. They appear after feedback shows a pattern the eval set can confirm.</p>`;

  const runs = d.recent_runs || [];
  document.getElementById("runs").innerHTML = runs.length
    ? `<table class="lp"><tr><th>When</th><th>Skill</th><th>Proposed rule</th><th>Decision</th></tr>${runs
        .map((r) => `<tr><td>${esc(when(r.created_at))}</td><td>${esc(r.category)}</td><td>${esc(r.rule)}<div class="muted">${pct(r.baseline)} → ${pct(r.score)}</div></td><td><span class="pill ${r.decision === "promoted" ? "ok" : "no"}">${esc(r.decision)}</span></td></tr>`)
        .join("")}</table>`
    : `<p class="muted">No cycles have run yet.</p>`;

  const ev = Object.entries(d.eval_cases || {}).sort();
  document.getElementById("evals").innerHTML = ev.length
    ? `<div class="cards">${ev.map(([k, v]) => `<div class="card-s">${esc(k)}<b>${esc(v)}</b></div>`).join("")}</div>`
    : `<p class="muted">No eval cases.</p>`;
}
load();
