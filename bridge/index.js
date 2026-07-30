/**
 * MiHomes WhatsApp Bridge — Baileys + Express HTTP API
 *
 * Connects to WhatsApp via Baileys Multi-Device protocol,
 * exposes a local HTTP API for the Python CLI to communicate with.
 *
 * Usage:
 *   cd bridge && npm install && npm start
 *
 * The bridge displays a QR code on first run for WhatsApp pairing.
 * Session credentials persist in ../mihomes-data/whatsapp-auth/
 */

const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, downloadMediaMessage, fetchLatestBaileysVersion, Browsers } = require('@whiskeysockets/baileys');
const express = require('express');
const fs = require('fs');
const path = require('path');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const { compactMessages } = require('./lib');

const PORT = process.env.BRIDGE_PORT || 7867;
const AUTH_DIR = process.env.AUTH_DIR || path.join(__dirname, '..', '.mihomes', 'whatsapp-auth');
const MEDIA_DIR = process.env.MEDIA_DIR || path.join(__dirname, '..', '.mihomes', 'media', 'whatsapp');

// Message store — last 1000 messages in memory, persisted to disk
const messageStore = [];
const MAX_MESSAGES = 1000;
const linkedGroups = new Map(); // groupJid -> propertySlug
const MESSAGES_FILE = path.join(AUTH_DIR, 'messages.jsonl');

const logger = pino({ level: 'warn' });
const app = express();
app.use(express.json());

let sock = null;
let connectionStatus = 'disconnected';
// M29: guard against stacking reconnect timers. Multiple 'close' events (or a
// close firing while a reconnect is already pending) would each queue another
// setTimeout(startConnection), spawning overlapping sockets. This flag ensures
// at most one reconnect is in flight.
let reconnecting = false;

// --- Baileys Connection ---

async function startConnection() {
  // The pending reconnect (if any) is now running; clear the guard so a failure
  // of this attempt can schedule the next one (M29).
  reconnecting = false;
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  fs.mkdirSync(MEDIA_DIR, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  const { version } = await fetchLatestBaileysVersion();
  console.log(`Using WA version: ${version.join('.')}`);

  sock = makeWASocket({
    version,
    auth: state,
    logger,
    browser: Browsers.macOS('Desktop'),
    printQRInTerminal: false,
    syncFullHistory: false,
    markOnlineOnConnect: false,
    getMessage: async () => ({ conversation: '' }),
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      connectionStatus = 'awaiting-qr';
      console.log('\n=== Scan this QR code with WhatsApp ===\n');
      qrcode.generate(qr, { small: true });
      // Store QR for API access
      app.locals.lastQR = qr;
    }

    if (connection === 'open') {
      connectionStatus = 'connected';
      reconnecting = false;  // healthy again — allow future reconnects
      console.log('WhatsApp connected successfully');
      app.locals.lastQR = null;
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const isLoggedOut = statusCode === DisconnectReason.loggedOut || statusCode === 401;
      connectionStatus = isLoggedOut ? 'logged-out' : 'reconnecting';
      console.log(`Connection closed. Status: ${statusCode}. Reconnecting: ${!isLoggedOut}`);
      if (!isLoggedOut && !reconnecting) {
        // Only schedule one reconnect at a time (M29).
        reconnecting = true;
        setTimeout(startConnection, 3000);
      }
    }
  });

  // Message handler
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    for (const msg of messages) {
      if (!msg.message || msg.key.fromMe) continue;

      const jid = msg.key.remoteJid;
      const isGroup = jid.endsWith('@g.us');
      const sender = isGroup ? msg.key.participant : jid;
      const senderName = msg.pushName || sender.split('@')[0];
      const text = msg.message.conversation
        || msg.message.extendedTextMessage?.text
        || msg.message.imageMessage?.caption
        || '';

      const hasMedia = !!(msg.message.imageMessage || msg.message.videoMessage || msg.message.documentMessage);
      let mediaPath = null;

      if (hasMedia) {
        try {
          const buffer = await downloadMediaMessage(msg, 'buffer', {});
          const ext = msg.message.imageMessage ? 'jpg'
            : msg.message.videoMessage ? 'mp4'
            : 'bin';
          // L10: albums deliver several images in the same millisecond from the
          // same sender — a Date.now()+sender name collided and later images
          // overwrote earlier ones. Add a short random suffix to disambiguate.
          const rand = Math.random().toString(36).slice(2, 8);
          const filename = `${Date.now()}-${sender.split('@')[0]}-${rand}.${ext}`;
          mediaPath = path.join(MEDIA_DIR, filename);
          fs.writeFileSync(mediaPath, buffer);
        } catch (e) {
          console.error('Failed to download media:', e.message);
        }
      }

      const record = {
        id: msg.key.id,
        timestamp: new Date(msg.messageTimestamp * 1000).toISOString(),
        jid,
        isGroup,
        sender,
        senderName,
        text,
        hasMedia,
        mediaPath,
        propertySlug: linkedGroups.get(jid) || null,
      };

      messageStore.push(record);
      if (messageStore.length > MAX_MESSAGES) {
        messageStore.shift();
      }

      // Persist to disk (append)
      try {
        fs.appendFileSync(MESSAGES_FILE, JSON.stringify(record) + '\n');
      } catch (e) {
        console.error('Failed to persist message:', e.message);
      }
    }
  });
}

// --- Express API ---

app.get('/status', (req, res) => {
  res.json({
    status: connectionStatus,
    hasQR: !!app.locals.lastQR,
    linkedGroups: Object.fromEntries(linkedGroups),
    messageCount: messageStore.length,
  });
});

app.get('/qr', (req, res) => {
  if (app.locals.lastQR) {
    res.json({ qr: app.locals.lastQR });
  } else {
    res.json({ qr: null, status: connectionStatus });
  }
});

app.get('/', (req, res) => {
  const qrData = app.locals.lastQR || '';
  const statusMsg = {
    connected: '✅ Connected to WhatsApp',
    disconnected: '⏳ Connecting...',
    'awaiting-qr': '📱 Scan the QR code below with WhatsApp',
    reconnecting: '🔄 Reconnecting...',
    'logged-out': '❌ Logged out — restart bridge',
  }[connectionStatus] || connectionStatus;

  res.send(`<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MiHomes WhatsApp Bridge</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
  <style>
    body { font-family: sans-serif; display: flex; flex-direction: column; align-items: center;
           justify-content: center; min-height: 100vh; margin: 0; background: #f5f5f5; }
    h1 { color: #333; }
    #status { font-size: 1.2em; margin: 16px 0; }
    #qrbox { background: #fff; padding: 24px; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.1); }
    #qrbox canvas, #qrbox img { display: block; }
    #msg { color: #666; margin-top: 16px; font-size: 0.9em; }
  </style>
</head>
<body>
  <h1>MiHomes WhatsApp Bridge</h1>
  <div id="status">${statusMsg}</div>
  <div id="qrbox">
    ${qrData ? '<div id="qr"></div>' : '<p style="color:#999">No QR code yet — waiting for bridge to initialise...</p>'}
  </div>
  <p id="msg">This page auto-refreshes every 5 seconds.</p>
  <script>
    ${qrData ? `new QRCode(document.getElementById('qr'), { text: ${JSON.stringify(qrData)}, width: 256, height: 256 });` : ''}
    setTimeout(() => location.reload(), 5000);
  </script>
</body>
</html>`);
});

app.post('/send', async (req, res) => {
  const { phone, text, mediaPath } = req.body;
  if (!sock || connectionStatus !== 'connected') {
    return res.status(503).json({ error: 'Not connected to WhatsApp' });
  }
  try {
    const jid = phone.includes('@') ? phone : `${phone.replace(/[^0-9]/g, '')}@s.whatsapp.net`;
    if (mediaPath && fs.existsSync(mediaPath)) {
      const ext = path.extname(mediaPath).toLowerCase();
      const buffer = fs.readFileSync(mediaPath);
      if (['.jpg', '.jpeg', '.png'].includes(ext)) {
        await sock.sendMessage(jid, { image: buffer, caption: text || '' });
      } else {
        await sock.sendMessage(jid, { document: buffer, fileName: path.basename(mediaPath), caption: text || '' });
      }
    } else {
      await sock.sendMessage(jid, { text });
    }
    res.json({ success: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/send-group', async (req, res) => {
  const { groupJid, text } = req.body;
  if (!sock || connectionStatus !== 'connected') {
    return res.status(503).json({ error: 'Not connected to WhatsApp' });
  }
  try {
    await sock.sendMessage(groupJid, { text });
    res.json({ success: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.delete('/messages', (req, res) => {
  messageStore.length = 0;
  try {
    fs.writeFileSync(MESSAGES_FILE, '');
  } catch (e) {
    console.error('Failed to clear messages file:', e.message);
  }
  res.json({ success: true, message: 'Message buffer cleared' });
});

app.get('/messages', (req, res) => {
  const { since, groupJid, limit, order } = req.query;
  let msgs = [...messageStore];

  if (since) {
    const sinceDate = new Date(since);
    msgs = msgs.filter(m => new Date(m.timestamp) >= sinceDate);
  }
  if (groupJid) {
    msgs = msgs.filter(m => m.jid === groupJid);
  }
  const maxResults = parseInt(limit) || 100;
  // order=asc pages forward from `since` (oldest first) so a burst larger than
  // one page can be fully drained (spec M30). Default keeps the newest window.
  const page = order === 'asc' ? msgs.slice(0, maxResults) : msgs.slice(-maxResults);
  res.json({ messages: page });
});

app.get('/groups', async (req, res) => {
  if (!sock || connectionStatus !== 'connected') {
    return res.status(503).json({ error: 'Not connected' });
  }
  try {
    const groups = await sock.groupFetchAllParticipating();
    const result = Object.values(groups).map(g => ({
      jid: g.id,
      name: g.subject,
      participants: g.participants.length,
      linked: linkedGroups.has(g.id) ? linkedGroups.get(g.id) : null,
    }));
    res.json({ groups: result });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/link-group', (req, res) => {
  const { groupJid, propertySlug } = req.body;
  linkedGroups.set(groupJid, propertySlug);
  persistGroupLinks();
  res.json({ success: true });
});

app.post('/unlink-group', (req, res) => {
  const { groupJid } = req.body;
  linkedGroups.delete(groupJid);
  persistGroupLinks();
  res.json({ success: true });
});

function persistGroupLinks() {
  const linksFile = path.join(AUTH_DIR, 'group-links.json');
  const tmpFile = linksFile + '.tmp';
  try {
    fs.writeFileSync(tmpFile, JSON.stringify(Object.fromEntries(linkedGroups)));
    fs.renameSync(tmpFile, linksFile);
  } catch (e) {
    console.error('Failed to persist group links:', e.message);
    try { fs.unlinkSync(tmpFile); } catch (_) {}
  }
}

// Load persisted group links on startup
function loadGroupLinks() {
  const linksFile = path.join(AUTH_DIR, 'group-links.json');
  if (fs.existsSync(linksFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(linksFile, 'utf-8'));
      for (const [jid, slug] of Object.entries(data)) {
        linkedGroups.set(jid, slug);
      }
    } catch (e) {
      console.error('Failed to load group links:', e.message);
    }
  }
}

// Load persisted messages on startup, compacting the on-disk log (M29).
// The file is append-only and grows without bound, but only the newest
// MAX_MESSAGES are ever served — so we rewrite it to just that tail on startup.
function loadMessages() {
  if (fs.existsSync(MESSAGES_FILE)) {
    try {
      const content = fs.readFileSync(MESSAGES_FILE, 'utf-8');
      const { records, text } = compactMessages(content, MAX_MESSAGES);
      for (const rec of records) messageStore.push(rec);
      // Rewrite the file so it never outgrows the retained window.
      try {
        fs.writeFileSync(MESSAGES_FILE, text);
      } catch (e) {
        console.error('Failed to compact messages log:', e.message);
      }
      console.log(`Loaded ${messageStore.length} messages from disk (log compacted)`);
    } catch (e) {
      console.error('Failed to load messages:', e.message);
    }
  }
}

// --- Start ---

app.listen(PORT, () => {
  console.log(`MiHomes WhatsApp Bridge listening on http://localhost:${PORT}`);
  loadGroupLinks();
  loadMessages();
  startConnection();
});
