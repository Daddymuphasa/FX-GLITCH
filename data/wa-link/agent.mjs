import path from "node:path";
import { fileURLToPath } from "node:url";
import makeWASocket, { DisconnectReason, useMultiFileAuthState } from "baileys";
import QRCode from "qrcode";

const DESK = process.env.FXG_DESK || "http://187.124.113.6:8765";
const root = path.dirname(fileURLToPath(import.meta.url));
const authDir = path.join(root, "auth");

function msgText(m) {
  const msg = m.message || {};
  return (
    msg.conversation ||
    msg.extendedTextMessage?.text ||
    msg.imageMessage?.caption ||
    ""
  ).trim();
}

async function desk(pathname, opts = {}) {
  const res = await fetch(DESK + pathname, {
    method: opts.body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("desk not JSON: " + text.slice(0, 80));
  }
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    browser: ["FX-GLITCH", "Chrome", "126.0.0"],
  });
  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    if (update.qr) {
      QRCode.toFile(path.join(root, "qr.png"), update.qr, {
        width: 560,
        margin: 2,
        color: { dark: "#000000", light: "#ffffff" },
      }).catch((err) => console.log("QR_WRITE_FAILED", String(err)));
      console.log("QR_WRITTEN", path.join(root, "qr.png"));
    }
    if (update.connection === "open") console.log("AGENT_LINKED");
    if (update.connection === "close") {
      const code = update.lastDisconnect?.error?.output?.statusCode;
      console.log("AGENT_CLOSED", code);
      if (code !== DisconnectReason.loggedOut) {
        setTimeout(start, 3000);
      }
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    for (const m of messages || []) {
      if (!m.message || m.key?.fromMe) continue;
      const jid = m.key.remoteJid;
      if (!jid || jid.endsWith("@g.us") || jid.endsWith("@broadcast") || jid.endsWith("@newsletter")) continue;
      const text = msgText(m);
      if (!text) continue;
      try {
        const out = await desk("/api/whatsapp", { body: { action: "chat", jid, text } });
        const reply = out.reply || out.error;
        if (reply) await sock.sendMessage(jid, { text: String(reply) });
      } catch (err) {
        await sock.sendMessage(jid, { text: "Desk error: " + String(err.message || err).slice(0, 200) });
      }
    }
  });

  setInterval(async () => {
    try {
      const box = await desk("/api/whatsapp?action=alerts");
      const alerts = box.alerts || [];
      const jids = box.jids || [];
      if (!alerts.length || !jids.length) return;
      for (const text of alerts) {
        for (const jid of jids) {
          await sock.sendMessage(jid, { text: String(text) });
        }
        console.log("RESULT", String(text).slice(0, 80), "to", jids.length);
      }
    } catch (err) {
      console.log("POLL", String(err.message || err).slice(0, 120));
    }
  }, 8000);
}

start().catch((err) => {
  console.error(err);
  process.exit(1);
});
