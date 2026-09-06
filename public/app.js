const inboxEl = document.getElementById("inbox");
const analysisEl = document.getElementById("analysis");
const plansEl = document.getElementById("plans");
const msgEl = document.getElementById("execute-msg");
const rawEl = document.getElementById("raw");
const equityEl = document.getElementById("equity");

let currentRaw = "";
let selectedId = "";

function money(n) {
  const v = Number(n) || 0;
  return (v < 0 ? "-" : "") + "$" + Math.abs(v).toFixed(2);
}

async function get(url) {
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function renderInbox(signals) {
  inboxEl.innerHTML = "";
  signals.forEach((s) => {
    const div = document.createElement("div");
    div.className = "item" + (s.id === selectedId ? " active" : "");
    div.innerHTML = `<div>${s.direction || "?"} ${s.binance_symbol || s.bitunix_symbol || "unknown"}</div>
      <div class="meta">${s.source} · ${s.chat || ""} · ${s.received_at || ""}</div>`;
    div.onclick = () => {
      selectedId = s.id;
      rawEl.value = s.raw;
      loadRecommend(s.raw);
      renderInbox(signals);
    };
    inboxEl.appendChild(div);
  });
}

function renderAnalysis(rec) {
  const pair = rec.pair || {};
  analysisEl.innerHTML = `
    <div class="kv">
      <div><span>Bitunix</span><span>${rec.bitunix_symbol || "—"}</span></div>
      <div><span>Binance</span><span>${rec.binance_symbol || "—"}</span></div>
      <div><span>Listed</span><span>${pair.tradeable ? "yes, tradeable" : (pair.note || "unchecked")}</span></div>
      <div><span>Side</span><span>${rec.direction || "—"}</span></div>
      <div><span>Entry</span><span>${rec.entry ?? "—"} ${rec.entry_is_market ? "(mark/CMP)" : ""}</span></div>
      <div><span>Stop</span><span>${rec.stop ?? "—"}</span></div>
      <div><span>TPs</span><span>${(rec.take_profits || []).join(" / ") || "—"}</span></div>
      <div><span>Group leverage</span><span>${rec.signal_leverage ?? "—"}x</span></div>
      <div><span>Equity</span><span>${money(rec.equity)}</span></div>
    </div>
    <p class="${rec.warnings && rec.warnings.length ? "warn" : "hint"}">${(rec.warnings || []).join(" · ") || rec.notes[0]}</p>
  `;
}

function renderPlans(rec) {
  plansEl.innerHTML = "";
  msgEl.textContent = "";
  (rec.plans || []).forEach((p) => {
    const card = document.createElement("div");
    card.className = "card " + p.id;
    card.innerHTML = `
      <h3>${p.label}</h3>
      <p class="hint">${p.blurb}</p>
      <div class="rr">1 : ${Number(p.reward_risk).toFixed(2)}</div>
      <div class="nums win">TP hit ${money(p.win_if_tp)}</div>
      <div class="nums loss">SL hit −${money(p.loss_if_sl)}</div>
      <div class="nums">${p.leverage}x · risk ${p.risk_pct}% · qty ${Number(p.qty).toPrecision(4)}</div>
      <div class="nums">SL ${p.stop} · TP ${p.take_profit}</div>
      <div class="nums">${p.policy_ok ? "policy pass" : p.policy_reason}</div>
    `;
    const btn = document.createElement("button");
    btn.textContent = p.policy_ok ? "Take this plan (dry-run)" : "Blocked";
    btn.disabled = !p.policy_ok;
    btn.onclick = () => execute(rec.raw, p.id);
    card.appendChild(btn);
    plansEl.appendChild(card);
  });
  if (!rec.plans || !rec.plans.length) {
    plansEl.innerHTML = "<p class='hint'>No plans — need a direction, a stop, and an entry or mark price.</p>";
  }
}

async function loadRecommend(message) {
  currentRaw = message;
  analysisEl.textContent = "Mapping to Binance…";
  try {
    const rec = await post("/api/recommend", { message, equity: Number(equityEl.value) || 1000 });
    renderAnalysis(rec);
    renderPlans(rec);
  } catch (err) {
    analysisEl.innerHTML = `<p class="warn">${err.message}</p>`;
    plansEl.innerHTML = "";
  }
}

async function execute(message, plan) {
  msgEl.textContent = "Building order…";
  try {
    const data = await post("/api/execute", {
      message, plan, equity: Number(equityEl.value) || 1000, confirm: true, live: false,
    });
    const p = data.plan;
    msgEl.textContent = data.dry_run
      ? `Dry-run ${p.label}: ${p.leverage}x ${data.recommendation.direction} ${data.recommendation.binance_symbol}. SL ${p.stop} TP ${p.take_profit}. Nothing sent.`
      : `Sent ${p.label}. Order ${data.order && data.order.id}`;
  } catch (err) {
    msgEl.textContent = err.message;
  }
}

async function boot() {
  const box = await get("/api/inbox");
  document.getElementById("tg-status").textContent = box.telegram ? "telegram: token present" : "telegram: not connected";
  renderInbox(box.signals || []);
  if (box.signals && box.signals[0]) {
    selectedId = box.signals[0].id;
    rawEl.value = box.signals[0].raw;
    renderInbox(box.signals);
    loadRecommend(box.signals[0].raw);
  }
}

document.getElementById("btn-ingest").onclick = async () => {
  const message = rawEl.value.trim();
  if (!message) return;
  await post("/api/inbox", { message, source: "paste" });
  const box = await get("/api/inbox");
  selectedId = box.signals[0] && box.signals[0].id;
  renderInbox(box.signals);
  loadRecommend(message);
};

boot();
