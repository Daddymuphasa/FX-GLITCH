const inboxEl = document.getElementById("inbox");
const analysisEl = document.getElementById("analysis");
const plansEl = document.getElementById("plans");
const msgEl = document.getElementById("execute-msg");
const rawEl = document.getElementById("raw");
const equityEl = document.getElementById("equity");
const takeBtn = document.getElementById("btn-take");
const keysDlg = document.getElementById("keys-dialog");
const bookEl = document.getElementById("book");

let currentRaw = "";
let selectedId = "";
let selectedPlan = "mid";
let currentRec = null;
const isAdmin = document.documentElement.classList.contains("is-admin");
const KEYS = "fxg.binance";
const BOOK = "fxg.book";

function money(n) {
  const v = Number(n) || 0;
  return (v < 0 ? "-" : "") + "$" + Math.abs(v).toFixed(2);
}

function userKeys() {
  try {
    return JSON.parse(localStorage.getItem(KEYS) || "{}");
  } catch (_err) {
    return {};
  }
}

function hasUserKeys() {
  const k = userKeys();
  return !!(k.api_key && k.secret);
}

function loadBook() {
  try {
    const rows = JSON.parse(localStorage.getItem(BOOK) || "[]");
    return Array.isArray(rows) ? rows : [];
  } catch (_err) {
    return [];
  }
}

function saveFill(fill) {
  const rows = loadBook();
  rows.unshift(fill);
  localStorage.setItem(BOOK, JSON.stringify(rows.slice(0, 40)));
  renderBook();
}

function renderBook() {
  if (!bookEl) return;
  const rows = loadBook();
  if (!rows.length) {
    bookEl.innerHTML = "<p class='hint'>No tickets yet. Take a setup and it lands here.</p>";
    return;
  }
  bookEl.innerHTML = rows.map((t) => `
    <div class="item">
      <div class="pair">${t.direction || ""} ${t.symbol || ""} <span class="tag">${t.plan || ""}</span></div>
      <div class="meta">${t.leverage || ""}x · SL ${t.stop} · TP ${t.take_profit} · ${t.at || ""}</div>
    </div>
  `).join("");
}

function setFlow(_n) {}

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

function uniqueSignals(signals) {
  const seen = new Set();
  const out = [];
  (signals || []).forEach((s) => {
    const key = [s.binance_symbol || s.bitunix_symbol, s.direction, s.raw].join("|");
    if (seen.has(key)) return;
    seen.add(key);
    out.push(s);
  });
  out.sort((a, b) => Number(b.still_good === true) - Number(a.still_good === true));
  return isAdmin ? out : out.filter((s) => s.still_good !== false);
}

function renderInbox(signals) {
  const rows = uniqueSignals(signals);
  inboxEl.innerHTML = "";
  if (!rows.length) {
    inboxEl.innerHTML = "<p class='hint'>No live setups yet. When the desk is watching Telegram, they land here.</p>";
    return rows;
  }
  rows.forEach((s) => {
    const div = document.createElement("div");
    div.className = "item" + (s.id === selectedId ? " active" : "");
    const tag = s.still_good === true
      ? '<span class="tag">OPEN</span>'
      : (s.still_good === false ? '<span class="tag late">late</span>' : "");
    div.innerHTML = `<div class="pair">${s.direction || "?"} ${s.binance_symbol || s.bitunix_symbol || "—" } ${tag}</div>
      <div class="meta">${s.still_good_reason || "Tap to size this trade"}</div>`;
    div.onclick = () => {
      selectedId = s.id;
      if (rawEl) rawEl.value = s.raw;
      loadRecommend(s.raw);
      renderInbox(signals);
      const trade = document.getElementById("trade");
      if (trade) trade.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    inboxEl.appendChild(div);
  });
  return rows;
}

function renderAnalysis(rec) {
  const pair = rec.pair || {};
  analysisEl.innerHTML = `
    <div class="pair-hero">
      <div class="side">${rec.direction || "—"}</div>
      <h3>${rec.binance_symbol || "NO PAIR"}</h3>
    </div>
    <div class="kv">
      <div><span>Entry</span><span>${rec.entry ?? "—"} ${rec.entry_is_market ? "market" : ""}</span></div>
      <div><span>Stop</span><span>${rec.stop ?? "—"}</span></div>
      <div><span>Take profit</span><span>${(rec.take_profits || []).join(" / ") || "—"}</span></div>
    </div>
    <p class="${rec.warnings && rec.warnings.length ? "warn" : "hint"}">${(rec.warnings || []).join(" · ") || ""}</p>
  `;
}

function renderPlans(rec) {
  plansEl.innerHTML = "";
  currentRec = rec;
  const plans = rec.plans || [];
  if (!plans.length) {
    plansEl.innerHTML = "<p class='hint'>Need a direction and a stop to size this.</p>";
    takeBtn.disabled = true;
    return;
  }
  if (!plans.some((p) => p.id === selectedPlan)) selectedPlan = "mid";
  plans.forEach((p) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pick " + p.id + (p.id === selectedPlan ? " on" : "");
    btn.disabled = !p.policy_ok;
    btn.innerHTML = `
      <h3>${p.label}</h3>
      <div class="wl"><span class="win">${money(p.win_if_tp)}</span><span class="loss">−${money(p.loss_if_sl)}</span></div>
      <div class="nums">${p.leverage}x · 1:${Number(p.reward_risk).toFixed(1)}</div>
    `;
    btn.onclick = () => {
      selectedPlan = p.id;
      renderPlans(rec);
    };
    plansEl.appendChild(btn);
  });
  const chosen = plans.find((p) => p.id === selectedPlan);
  takeBtn.disabled = !chosen || !chosen.policy_ok;
  takeBtn.textContent = chosen ? "Take " + chosen.label : "Take trade";
}

async function loadRecommend(message) {
  currentRaw = message;
  analysisEl.innerHTML = '<p class="hint">Sizing on Binance…</p>';
  try {
    const rec = await post("/api/recommend", { message, equity: Number(equityEl.value) || 1000 });
    renderAnalysis(rec);
    renderPlans(rec);
  } catch (err) {
    analysisEl.innerHTML = `<p class="warn">${err.message}</p>`;
    plansEl.innerHTML = "";
    takeBtn.disabled = true;
  }
}

async function execute(message, plan, details) {
  const sendBox = document.getElementById("send-live");
  const keys = isAdmin ? userKeys() : {};
  const wantLive = !!(isAdmin && sendBox && sendBox.checked && keys.api_key);
  msgEl.textContent = "Taking…";
  try {
    const data = await post("/api/execute", {
      message,
      plan,
      equity: Number(equityEl.value) || 1000,
      confirm: true,
      live: wantLive,
      api_key: wantLive ? (keys.api_key || "") : "",
      secret: wantLive ? (keys.secret || "") : "",
    });
    const p = data.plan;
    const rec = data.recommendation || {};
    saveFill({
      direction: rec.direction,
      symbol: rec.binance_symbol,
      plan: p.label,
      leverage: p.leverage,
      stop: p.stop,
      take_profit: p.take_profit,
      at: new Date().toISOString().slice(11, 16) + " UTC",
    });
    msgEl.textContent = `${p.label} taken on your desk. ${p.leverage}x ${rec.binance_symbol || ""}. SL ${p.stop} TP ${p.take_profit}.`;
  } catch (err) {
    msgEl.textContent = err.message;
  }
}

function renderAccount(snap) {
  if (!isAdmin) return;
  const box = document.getElementById("qr-box");
  const status = document.getElementById("qr-status");
  const pwWrap = document.getElementById("pw-wrap");
  const pill = document.getElementById("tg-status");
  if (snap.qr) {
    box.innerHTML = `<img alt="Telegram login QR" src="${snap.qr}" />`;
  } else if (snap.qr_url) {
    box.textContent = snap.qr_url;
  } else if (snap.status === "linked" || snap.status === "watching") {
    box.textContent = "Already linked";
  } else {
    box.textContent = snap.error || "QR";
  }
  pill.classList.remove("ok", "warn");
  if (snap.status === "linked" || snap.status === "watching") {
    if (qrPoll) { clearInterval(qrPoll); qrPoll = null; }
    const name = (snap.user && (snap.user.username || snap.user.first_name)) || "account";
    const watch = snap.watch && snap.watch.title ? ` watching ${snap.watch.title}` : "";
    status.textContent = `Linked as ${name}.${watch} New group posts go to every user on fxglitch.xyz.`;
    pill.textContent = "telegram: linked";
    pill.classList.add("ok");
  } else if (snap.status === "need_api") {
    status.textContent = "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env, then restart.";
    pill.classList.add("warn");
  } else if (snap.status === "need_library") {
    status.textContent = "Run: pip install telethon qrcode";
    pill.classList.add("warn");
  } else if (snap.status === "vercel") {
    status.textContent = "Scan Telegram on this PC at http://127.0.0.1:8765 — not on the public site.";
    pill.classList.add("warn");
  } else if (snap.status === "need_password") {
    status.textContent = "2FA password needed.";
    pwWrap.classList.remove("hidden");
    document.getElementById("btn-password").classList.remove("hidden");
  } else if (snap.status === "need_scan") {
    const age = snap.qr_age != null ? ` Code is ${snap.qr_age}s old.` : "";
    status.textContent = "Scan THIS QR now (Telegram → Settings → Devices → Link Desktop Device)." + age;
    pill.classList.add("warn");
    startQrPoll();
  } else {
    status.textContent = snap.error || ("Status: " + (snap.status || "idle"));
  }
}

async function refreshInbox() {
  let signals = [];
  let box = { signals: [], account: null };
  try {
    box = await get("/api/inbox");
    signals = box.signals || [];
  } catch (_err) {}
  const onlyDemo = signals.length === 1 && signals[0].id === "demo-kaito";
  if (!signals.length || onlyDemo) {
    try {
      const live = await (await fetch("/live-signals.json")).json();
      if (live.signals && live.signals.length) signals = live.signals;
    } catch (_err) {}
    try {
      const remote = await get("/api/signals");
      if (remote.signals && remote.signals.length && remote.signals[0].id !== "demo-kaito") {
        signals = remote.signals;
      }
    } catch (_err) {}
  }
  box.signals = signals;
  renderInbox(signals);
  if (box.account) renderAccount(box.account);
  return box;
}

function fillChats(data) {
  const sel = document.getElementById("chat-list");
  if (!sel) return;
  sel.innerHTML = "";
  const chats = data.chats || [];
  chats.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.title + " (" + c.id + ")";
    sel.appendChild(opt);
  });
  const wanted = data.watch && data.watch.chat_id;
  if (wanted) sel.value = String(wanted);
  if (!chats.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = data.error || "No groups yet";
    sel.appendChild(opt);
  }
}

function paintKeysButton() {
  const btn = document.getElementById("btn-keys");
  btn.textContent = hasUserKeys() ? "Binance connected" : "Connect Binance";
}

async function boot() {
  renderBook();
  paintKeysButton();
  const savedEq = userKeys().equity;
  if (savedEq) equityEl.value = savedEq;
  document.getElementById("user-key").value = userKeys().api_key || "";
  document.getElementById("user-secret").value = userKeys().secret || "";
  document.getElementById("send-live").checked = !!userKeys().live;
  try {
    await get("/api/health");
  } catch (_err) {}
  let box = { signals: [] };
  try {
    box = await refreshInbox();
  } catch (_err) {
    return;
  }
  const account = box.account || {};
  if (isAdmin && (account.status === "linked" || account.status === "watching")) {
    try { fillChats(await get("/api/telegram?action=chats")); } catch (_err) {}
    const hint = document.getElementById("inbox-hint");
    if (hint) hint.textContent = "Loading today's trades…";
    try {
      await scanToday();
      box = await refreshInbox();
    } catch (_err) {}
  }
  const rows = uniqueSignals(box.signals || []);
  const pick = rows.find((s) => s.still_good === true) || rows[0];
  if (pick) {
    selectedId = pick.id;
    if (rawEl) rawEl.value = pick.raw;
    renderInbox(box.signals);
    loadRecommend(pick.raw);
  }
  setInterval(refreshInbox, 8000);
}

document.getElementById("btn-ingest").onclick = async () => {
  const message = rawEl.value.trim();
  if (!message) return;
  await post("/api/inbox", { message, source: "paste" });
  const box = await refreshInbox();
  selectedId = box.signals[0] && box.signals[0].id;
  renderInbox(box.signals);
  loadRecommend(message);
};

document.getElementById("btn-take").onclick = () => {
  if (!currentRaw || !currentRec) return;
  const details = (currentRec.plans || []).find((p) => p.id === selectedPlan);
  execute(currentRaw, selectedPlan, details);
};

document.getElementById("btn-keys").onclick = () => keysDlg.showModal();
document.getElementById("keys-form").addEventListener("submit", (ev) => {
  if (ev.submitter && ev.submitter.id === "btn-save-keys") {
    localStorage.setItem(KEYS, JSON.stringify({
      api_key: document.getElementById("user-key").value.trim(),
      secret: document.getElementById("user-secret").value.trim(),
      equity: Number(equityEl.value) || 1000,
      live: document.getElementById("send-live").checked,
    }));
    paintKeysButton();
    if (currentRec) renderPlans(currentRec);
  }
});

equityEl.onchange = () => {
  const k = userKeys();
  k.equity = Number(equityEl.value) || 1000;
  localStorage.setItem(KEYS, JSON.stringify(k));
  if (currentRaw) loadRecommend(currentRaw);
};

let qrPoll = null;
function startQrPoll() {
  if (qrPoll) return;
  qrPoll = setInterval(async () => {
    try {
      const snap = await get("/api/telegram?action=status");
      renderAccount(snap);
      if (snap.status !== "need_scan") {
        clearInterval(qrPoll);
        qrPoll = null;
      }
    } catch (_err) {}
  }, 2000);
}

document.getElementById("btn-qr").onclick = async () => {
  document.getElementById("qr-status").textContent = "Minting a fresh QR…";
  renderAccount(await get("/api/telegram?action=qr"));
  startQrPoll();
};
document.getElementById("btn-password").onclick = async () => {
  renderAccount(await post("/api/telegram", { action: "password", password: document.getElementById("tg-password").value }));
};
document.getElementById("btn-chats").onclick = async () => {
  const data = await get("/api/telegram?action=chats");
  renderAccount(data);
  fillChats(data);
};

async function scanToday() {
  const hint = document.getElementById("inbox-hint");
  if (hint) hint.textContent = "Reading today's group messages…";
  const data = await get("/api/telegram?action=scan");
  if (data.account) renderAccount(data.account);
  await refreshInbox();
  const kept = (data.signals || []).filter((s) => s.still_good);
  if (hint) {
    hint.textContent = data.ok
      ? `${data.kept} still good to enter.`
      : (data.error || "scan failed");
  }
  if (kept[0]) {
    selectedId = kept[0].id;
    if (rawEl) rawEl.value = kept[0].raw;
    loadRecommend(kept[0].raw);
  }
  return data;
}

document.getElementById("btn-scan").onclick = async () => {
  try { await scanToday(); } catch (err) {
    const hint = document.getElementById("inbox-hint");
    if (hint) hint.textContent = err.message;
  }
};

document.getElementById("btn-watch").onclick = async () => {
  const sel = document.getElementById("chat-list");
  const status = document.getElementById("qr-status");
  if (!sel.value || !/^-?\d+$/.test(sel.value)) {
    if (status) status.textContent = "Pick a group first.";
    return;
  }
  renderAccount(await post("/api/telegram", {
    action: "watch",
    chat_id: sel.value,
    title: sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : "",
  }));
};

boot();
