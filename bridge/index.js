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

const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, downloadMediaMessage } = require('@whiskeysockets/baileys');
const express = require('express');
const fs = require('fs');
const path = require('path');
const pino = require('pino');
const qrcode = require('qrcode-terminal');

const PORT = process.env.BRIDGE_PORT || 7867;
const AUTH_DIR = process.env.AUTH_DIR || path.join(__dirname, '..', '.mihomes', 'whatsapp-auth');
const MEDIA_DIR = process.env.MEDIA_DIR || path.join(__dirname, '..', '.mihomes', 'media', 'whatsapp');

// Message store — last 1000 messages in memory, flushed to disk
const messageStore = [];
const MAX_MESSAGES = 1000;
const linkedGroups = new Map(); // groupJid -> propertySlug

const logger = pino({ level: 'warn' });
const app = express();
app.use(express.json());

let sock = null;
let connectionStatus = 'disconnected';

// --- Baileys Connection ---

async function startConnection() {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  fs.mkdirSync(MEDIA_DIR, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  sock = makeWASocket({
    auth: state,
    logger,
    printQRInTerminal: false,
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
      console.log('WhatsApp connected successfully');
      app.locals.lastQR = null;
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      connectionStatus = shouldReconnect ? 'reconnecting' : 'logged-out';
      console.log(`Connection closed. Status: ${statusCode}. Reconnecting: ${shouldReconnect}`);
      if (shouldReconnect) {
        setTimeout(startConnection, 5000);
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
          const filename = `${Date.now()}-${sender.split('@')[0]}.${ext}`;
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

app.get('/messages', (req, res) => {
  const { since, groupJid, limit } = req.query;
  let msgs = [...messageStore];

  if (since) {
    const sinceDate = new Date(since);
    msgs = msgs.filter(m => new Date(m.timestamp) >= sinceDate);
  }
  if (groupJid) {
    msgs = msgs.filter(m => m.jid === groupJid);
  }
  const maxResults = parseInt(limit) || 100;
  res.json({ messages: msgs.slice(-maxResults) });
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
  // Persist links
  const linksFile = path.join(AUTH_DIR, 'group-links.json');
  fs.writeFileSync(linksFile, JSON.stringify(Object.fromEntries(linkedGroups)));
  res.json({ success: true });
});

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

// --- Start ---

app.listen(PORT, () => {
  console.log(`MiHomes WhatsApp Bridge listening on http://localhost:${PORT}`);
  loadGroupLinks();
  startConnection();
});
