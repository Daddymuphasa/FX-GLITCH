import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import makeWASocket, { DisconnectReason, useMultiFileAuthState } from "baileys";
import QRCode from "qrcode";

const root = path.dirname(fileURLToPath(import.meta.url));
const authDir = path.join(root, "auth");
const qrFile = path.join(root, "qr.png");
await mkdir(authDir, { recursive: true });

const { state, saveCreds } = await useMultiFileAuthState(authDir);
const sock = makeWASocket({
  auth: state,
  printQRInTerminal: false,
  browser: ["FX-GLITCH", "Chrome", "126.0.0"],
});

sock.ev.on("creds.update", saveCreds);
sock.ev.on("connection.update", async (update) => {
  const { connection, lastDisconnect, qr } = update;
  if (qr) {
    await QRCode.toFile(qrFile, qr, { width: 560, margin: 2, color: { dark: "#000000", light: "#ffffff" } });
    console.log("QR_WRITTEN", qrFile);
  }
  if (connection === "open") {
    console.log("LINKED");
  }
  if (connection === "close") {
    const err = lastDisconnect?.error;
    const code = err?.output?.statusCode;
    console.log("CLOSED", code, String(err || ""));
    if (code === DisconnectReason.loggedOut) {
      process.exit(1);
    }
    if (code === DisconnectReason.restartRequired) {
      console.log("RESTART");
      process.exit(42);
    }
  }
});
