const inboxEl = document.getElementById("inbox");
const analysisEl = document.getElementById("analysis");
const plansEl = document.getElementById("plans");
const msgEl = document.getElementById("execute-msg");
const rawEl = document.getElementById("raw");
const equityEl = document.getElementById("equity");

let currentRaw = "";
let selectedId = "";
let liveAllowed = false;
const isAdmin = document.documentElement.classList.contains("is-admin");

function money(n) {
  const v = Number(n) || 0;
  return (v < 0 ? "-" : "") + "$" + Math.abs(v).toFixed(2);
}

function setFlow(n) {
  document.querySelectorAll(".step").forEach((el) => {
    const step = Number(el.getAttribute("data-step"));
    el.classList.toggle("current", step === n);
    el.classList.toggle("done", step < n);
  });
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
    const tag = s.still_good === true
      ? '<span class="tag">STILL GOOD</span>'
      : (s.still_good === false ? '<span class="tag late">late</span>' : "");
    div.innerHTML = `<div>${s.direction || "?"} ${s.binance_symbol || s.bitunix_symbol || "unknown"}${tag}</div>
      <div class="meta">${s.source} · ${s.chat || ""} · ${s.posted_at || s.received_at || ""}${s.still_good_reason ? " · " + s.still_good_reason : ""}</div>`;
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
  const listed = pair.tradeable ? "yes, tradeable" : (pair.note || "unchecked");
  analysisEl.innerHTML = `
    <div class="pair-hero">
      <div class="side">${rec.direction || "—"}</div>
      <h3>${rec.binance_symbol || "NO PAIR"}</h3>
      <p>from ${rec.bitunix_symbol || "unknown"} · listed ${listed}</p>
    </div>
    <div class="kv">
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
      <div class="rr"><small>Reward : risk</small>1 : ${Number(p.reward_risk).toFixed(2)}</div>
      <div class="wl">
        <div class="win"><small>If TP hits</small>${money(p.win_if_tp)}</div>
        <div class="loss"><small>If SL hits</small>−${money(p.loss_if_sl)}</div>
      </div>
      <div class="nums">${p.leverage}x · risk ${p.risk_pct}% · qty ${Number(p.qty).toPrecision(4)}</div>
      <div class="nums">SL ${p.stop} · TP ${p.take_profit}</div>
      <div class="nums">${p.policy_ok ? "policy pass" : p.policy_reason}</div>
    `;
    const btn = document.createElement("button");
    const send = document.getElementById("send-live") && document.getElementById("send-live").checked;
    btn.textContent = !p.policy_ok ? "Blocked" : (send && liveAllowed ? "Send this plan on Binance" : "Take this plan (dry-run)");
    btn.disabled = !p.policy_ok;
    if (p.policy_ok && send && liveAllowed) btn.className = "primary";
    btn.onclick = () => execute(rec.raw, p.id, p);
    card.appendChild(btn);
    plansEl.appendChild(card);
  });
  if (!rec.plans || !rec.plans.length) {
    plansEl.innerHTML = "<p class='hint'>No plans — need a direction, a stop, and an entry or mark price.</p>";
  }
}

async function loadRecommend(message) {
  currentRaw = message;
  setFlow(2);
  analysisEl.innerHTML = '<p class="hint">Mapping pair on Binance…</p>';
  try {
    const rec = await post("/api/recommend", { message, equity: Number(equityEl.value) || 1000 });
    renderAnalysis(rec);
    renderPlans(rec);
    setFlow(3);
  } catch (err) {
    analysisEl.innerHTML = `<p class="warn">${err.message}</p>`;
    plansEl.innerHTML = "";
  }
}

async function execute(message, plan, details) {
  const sendBox = document.getElementById("send-live");
  const wantLive = !!(sendBox && sendBox.checked);
  if (wantLive) {
    const ok = window.confirm(
      "Send a MARKET order to Binance USDⓈ-M?\n\n" +
      (details ? `${details.label}: ${details.leverage}x ${details.qty} qty\nSL ${details.stop}  TP ${details.take_profit}\n` : "") +
      "This spends real margin if keys are set."
    );
    if (!ok) {
      msgEl.textContent = "Cancelled. Nothing sent.";
      return;
    }
  }
  msgEl.textContent = wantLive ? "Sending to Binance…" : "Building dry-run…";
  try {
    const data = await post("/api/execute", {
      message, plan, equity: Number(equityEl.value) || 1000, confirm: true, live: wantLive,
    });
    const p = data.plan;
    msgEl.textContent = data.dry_run
      ? `Dry-run ${p.label}: ${p.leverage}x ${data.recommendation && data.recommendation.direction} ${data.recommendation && data.recommendation.binance_symbol}. SL ${p.stop} TP ${p.take_profit}. Nothing sent.${data.hint ? " " + data.hint : ""}`
      : `Sent ${p.label} on Binance. Order ${data.order && data.order.id}`;
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
  const setup = document.getElementById("setup-tg");
  if (snap.qr) {
    box.innerHTML = `<img alt="Telegram login QR" src="${snap.qr}" />`;
  } else if (snap.qr_url) {
    box.textContent = snap.qr_url;
  } else {
    box.textContent = snap.error || "QR appears here";
  }
  pill.classList.remove("ok", "warn");
  if (snap.status === "linked" || snap.status === "watching") {
    if (qrPoll) { clearInterval(qrPoll); qrPoll = null; }
    const name = (snap.user && (snap.user.username || snap.user.first_name)) || "account";
    const watch = snap.watch && snap.watch.title ? ` watching ${snap.watch.title}` : "";
    status.textContent = `Linked as ${name}.${watch}`;
    pill.textContent = "telegram: linked";
    pill.classList.add("ok");
    if (setup && snap.status === "watching") setup.open = false;
  } else if (snap.status === "need_api") {
    status.textContent = "Set TELEGRAM_API_ID and TELEGRAM_API_HASH from https://my.telegram.org then restart python serve.py.";
    pill.textContent = "telegram: need API id";
    pill.classList.add("warn");
    if (setup) setup.open = true;
  } else if (snap.status === "need_library") {
    status.textContent = "Run: pip install telethon qrcode";
    pill.classList.add("warn");
  } else if (snap.status === "vercel") {
    status.textContent = snap.error;
    pill.textContent = "telegram: use local serve.py";
    pill.classList.add("warn");
  } else if (snap.status === "need_password") {
    status.textContent = "This account has 2FA. Enter the password.";
    pwWrap.classList.remove("hidden");
    document.getElementById("btn-password").classList.remove("hidden");
    if (setup) setup.open = true;
  } else if (snap.status === "need_scan") {
    const age = snap.qr_age != null ? ` This code is ${snap.qr_age}s old and refreshes every 20s.` : "";
    status.textContent = "Scan THIS code now (Telegram → Settings → Devices → Link Desktop Device)." + age + " Old codes say auth token expired.";
    pill.textContent = "telegram: scan QR";
    pill.classList.add("warn");
    if (setup) setup.open = true;
    startQrPoll();
  } else {
    status.textContent = snap.error || ("Status: " + (snap.status || "idle"));
  }
}

async function refreshInbox() {
  const box = await get("/api/inbox");
  renderInbox(box.signals || []);
  if (box.account) renderAccount(box.account);
  if (box.signals && box.signals.length && !currentRaw) setFlow(1);
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
  if (wanted) {
    sel.value = String(wanted);
  }
  if (!chats.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = data.error || "No groups yet — scan the QR first";
    sel.appendChild(opt);
  }
}

async function boot() {
  try {
    const health = await get("/api/health");
    liveAllowed = !!health.live_allowed;
    const hint = document.getElementById("live-hint");
    const bn = document.getElementById("bn-status");
    const mode = document.getElementById("mode-pill");
    bn.classList.remove("ok", "warn", "live");
    mode.classList.remove("ok", "warn", "live");
    if (!health.binance) {
      hint.textContent = isAdmin
        ? "Orders are dry-run until you put BINANCE_API_KEY and BINANCE_SECRET_KEY in .env and restart python serve.py. Then tick 'Send to Binance'."
        : "Same stop as the signal. Size, leverage and take-profit change with the plan. Nothing is sent until you confirm.";
      bn.textContent = "binance: no keys";
      bn.classList.add("warn");
      mode.textContent = "dry-run default";
    } else if (!liveAllowed) {
      hint.textContent = isAdmin
        ? "Binance keys are present but live send is disabled on the hosted site. Use http://127.0.0.1:8765 to send."
        : "Plans are dry-run on the public desk. Confirm a plan to see the sized order. Nothing is sent from this site.";
      bn.textContent = "binance: keys (hosted = dry-run)";
      bn.classList.add("ok");
      mode.textContent = "dry-run";
    } else {
      hint.textContent = "Keys loaded. Tick 'Send to Binance' and confirm to place a market order with SL/TP.";
      bn.textContent = "binance: keys ready";
      bn.classList.add("ok");
      mode.textContent = "local send ready";
      mode.classList.add("live");
    }
  } catch (_err) {
    const hint = document.getElementById("inbox-hint");
    if (hint) {
      hint.textContent = "Desk is not running. Double-click start-desk.bat in the FX-GLITCH folder (or run python serve.py).";
    }
  }
  let box = { signals: [] };
  try {
    box = await refreshInbox();
  } catch (_err) {
    return;
  }
  const account = box.account || {};
  if (isAdmin && (account.status === "linked" || account.status === "watching")) {
    try {
      fillChats(await get("/api/telegram?action=chats"));
    } catch (_err) {}
  }
  if (box.signals && box.signals[0]) {
    selectedId = box.signals[0].id;
    if (rawEl) rawEl.value = box.signals[0].raw;
    renderInbox(box.signals);
    loadRecommend(box.signals[0].raw);
  }
  setInterval(refreshInbox, 5000);
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

document.getElementById("send-live").onchange = () => {
  if (!currentRaw) return;
  loadRecommend(currentRaw);
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
  document.getElementById("qr-status").textContent = "Minting a fresh QR… scan as soon as it appears.";
  const snap = await get("/api/telegram?action=qr");
  renderAccount(snap);
  startQrPoll();
};

document.getElementById("btn-password").onclick = async () => {
  const password = document.getElementById("tg-password").value;
  const snap = await post("/api/telegram", { action: "password", password });
  renderAccount(snap);
};

document.getElementById("btn-chats").onclick = async () => {
  const data = await get("/api/telegram?action=chats");
  renderAccount(data);
  fillChats(data);
};

document.getElementById("btn-scan").onclick = async () => {
  const hint = document.getElementById("inbox-hint");
  hint.textContent = "Reading today's group messages…";
  try {
    const data = await get("/api/telegram?action=scan");
    if (data.account) renderAccount(data.account);
    await refreshInbox();
    const kept = (data.signals || []).filter((s) => s.still_good);
    hint.textContent = data.ok
      ? `Scanned ${data.scanned} setups today. ${data.kept} still good to enter. ${data.skipped || 0} non-signals skipped.`
      : (data.error || "scan failed");
    if (kept[0]) {
      selectedId = kept[0].id;
      rawEl.value = kept[0].raw;
      loadRecommend(kept[0].raw);
    }
  } catch (err) {
    hint.textContent = err.message;
  }
};

document.getElementById("btn-watch").onclick = async () => {
  const sel = document.getElementById("chat-list");
  const status = document.getElementById("qr-status");
  if (!sel.value || !/^-?\d+$/.test(sel.value)) {
    if (status) status.textContent = "Load my groups and pick the Bitunix chat first.";
    return;
  }
  const snap = await post("/api/telegram", {
    action: "watch",
    chat_id: sel.value,
    title: sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : "",
  });
  renderAccount(snap);
};

boot();
