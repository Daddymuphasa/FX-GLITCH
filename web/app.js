const briefingEl = document.getElementById("briefing");
const policyEl = document.getElementById("policy");
const casesEl = document.getElementById("cases");
const caseList = document.getElementById("case-list");

function show(el, data) {
  el.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
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

document.getElementById("btn-demo").onclick = async () => {
  briefingEl.textContent = "Running offline demo…";
  const data = await get("/api/demo");
  casesEl.hidden = false;
  caseList.innerHTML = "";
  data.cases.forEach((c) => {
    const row = document.createElement("div");
    row.className = "case";
    const mark = c.policy.allowed ? "ALLOW" : "BLOCK";
    row.innerHTML = `<span>${c.title}</span><span class="${c.policy.allowed ? "allow" : "block"}">${mark}</span><span>${c.policy.reason}</span>`;
    caseList.appendChild(row);
  });
  show(briefingEl, data.cases[0].briefing);
  show(policyEl, data.cases.map((c) => ({
    title: c.title,
    allowed: c.policy.allowed,
    reason: c.policy.reason,
  })));
};

document.getElementById("btn-live").onclick = async () => {
  briefingEl.textContent = "Fetching public Binance futures data…";
  try {
    const data = await get("/api/briefing?symbol=BTCUSDT");
    show(briefingEl, data);
    policyEl.textContent = "Briefing only. Run a cycle to propose + check policy.";
  } catch (err) {
    briefingEl.textContent = String(err.message) + "\nUse the judge demo if the public API is blocked.";
  }
};

document.getElementById("btn-cycle").onclick = async () => {
  policyEl.textContent = "Cycling…";
  try {
    const data = await post("/api/cycle", { symbol: "BTCUSDT", live: false });
    show(briefingEl, data.briefing);
    show(policyEl, { proposal: data.proposal, policy: data.policy, dry_run: data.dry_run });
  } catch (err) {
    policyEl.textContent = String(err.message);
  }
};

document.getElementById("btn-signal").onclick = async () => {
  const message = document.getElementById("signal").value;
  const data = await post("/api/cycle", { symbol: "BTCUSDT", message, live: false });
  show(briefingEl, data.briefing);
  show(policyEl, { proposal: data.proposal, policy: data.policy, dry_run: data.dry_run });
};

get("/api/hackathon").then((h) => {
  const ol = document.getElementById("steps");
  h.published_entry_steps.forEach((step) => {
    const li = document.createElement("li");
    li.textContent = step;
    ol.appendChild(li);
  });
});
