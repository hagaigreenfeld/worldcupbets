/**
 * Cloudflare Worker — Sport5 Hevre WhatsApp Bot
 *
 * Handles two roles:
 *   1. Webhook: receives incoming WhatsApp messages from Twilio,
 *      parses Hebrew commands, responds with live data from Sport5 API.
 *   2. Push endpoint: GitHub Actions POSTs the game summary here after
 *      each game, and this worker broadcasts it to the WhatsApp group.
 *
 * Environment variables (set in Cloudflare dashboard or wrangler.toml secrets):
 *   TWILIO_ACCOUNT_SID   - your Twilio account SID
 *   TWILIO_AUTH_TOKEN    - your Twilio auth token
 *   TWILIO_FROM          - Twilio sandbox number e.g. "whatsapp:+14155238886"
 *   WHATSAPP_GROUP_ID    - the recipient number/group e.g. "whatsapp:+972501234567"
 *   SPORT5_EMAIL         - your Sport5 login email
 *   SPORT5_PASSWORD      - your Sport5 password
 *   PUSH_SECRET          - a secret token for the /push endpoint (any random string)
 *   GROUP_ID             - Sport5 group ID (6a202c81f6f70af684071fd4)
 */

const SPORT5_BASE = "https://hevre.sport5.co.il/server/data.php";
const GROUP_ID    = "6a202c81f6f70af684071fd4";

// ── Helpers ───────────────────────────────────────────────────────────────────

async function sport5Post(type, token, body = {}) {
  const form = new URLSearchParams({ token, ...body });
  const res  = await fetch(`${SPORT5_BASE}?type=${type}`, {
    method:  "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body:    form.toString(),
  });
  return res.json();
}

async function login(env) {
  const form = new URLSearchParams({
    email:    env.SPORT5_EMAIL,
    password: env.SPORT5_PASSWORD,
  });
  const res  = await fetch(`${SPORT5_BASE}?type=appUserLogin`, {
    method:  "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body:    form.toString(),
  });
  const data = await res.json();
  if (!data.token) throw new Error("Login failed: " + JSON.stringify(data));
  return data.token;
}

async function getLeaderboard(token) {
  const data = await sport5Post("getGroup", token, { groupId: GROUP_ID });
  const members = data.members || [];
  return members
    .sort((a, b) => (b.points || 0) - (a.points || 0))
    .map((m, i) => ({ rank: i + 1, name: m.name, points: m.points || 0 }));
}

async function sendWhatsApp(env, to, message) {
  const url  = `https://api.twilio.com/2010-04-01/Accounts/${env.TWILIO_ACCOUNT_SID}/Messages.json`;
  const auth = btoa(`${env.TWILIO_ACCOUNT_SID}:${env.TWILIO_AUTH_TOKEN}`);
  const body = new URLSearchParams({ From: env.TWILIO_FROM, To: to, Body: message });

  const res = await fetch(url, {
    method:  "POST",
    headers: {
      Authorization:  `Basic ${auth}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: body.toString(),
  });
  return res.json();
}

// TwiML response (for synchronous webhook reply)
function twimlReply(message) {
  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?>
<Response><Message>${escapeXml(message)}</Message></Response>`,
    { headers: { "Content-Type": "text/xml" } }
  );
}

function escapeXml(str) {
  return str
    .replace(/&/g,  "&amp;")
    .replace(/</g,  "&lt;")
    .replace(/>/g,  "&gt;")
    .replace(/"/g,  "&quot;");
}

// ── Command handlers ──────────────────────────────────────────────────────────

async function handleLeaderboard(env) {
  const token = await login(env);
  const board = await getLeaderboard(token);

  const medals = ["🥇", "🥈", "🥉"];
  const lines  = board.map((r, i) => {
    const medal = medals[i] || `${r.rank}.`;
    return `${medal} ${r.name} — ${r.points} נק'`;
  });

  return `⚽ *טבלת חבר'ה קדרון*\n\n${lines.join("\n")}\n\n🕐 עודכן עכשיו`;
}

async function handleHelp() {
  return `🤖 *פקודות הבוט*\n
*טבלה* — טבלת הניקוד הנוכחית
*עזרה* — הצגת פקודות זמינות
*סטטוס* — הבוט חי ומוכן ✅`;
}

async function handleStatus() {
  return "✅ הבוט פעיל ומחכה למשחקים!";
}

// Normalize Hebrew commands (handle various spellings/shortcuts)
function parseCommand(text) {
  const t = (text || "").trim().toLowerCase();
  if (["טבלה", "טבלת", "standings", "leaderboard", "דירוג", "תוצאות"].some(k => t.includes(k)))
    return "leaderboard";
  if (["עזרה", "help", "?", "פקודות"].some(k => t.includes(k)))
    return "help";
  if (["סטטוס", "status", "ping"].some(k => t.includes(k)))
    return "status";
  return null;
}

// ── Route handlers ────────────────────────────────────────────────────────────

/** POST /webhook — called by Twilio for incoming WhatsApp messages */
async function handleWebhook(request, env) {
  const body    = await request.text();
  const params  = new URLSearchParams(body);
  const msgBody = params.get("Body") || "";
  const from    = params.get("From") || "";

  console.log(`[webhook] from=${from} body="${msgBody}"`);

  const cmd = parseCommand(msgBody);
  if (!cmd) {
    // Unknown command — silent ignore or friendly nudge
    return twimlReply('לא הבנתי 🤔 שלח *עזרה* לרשימת פקודות');
  }

  try {
    let reply;
    if (cmd === "leaderboard") reply = await handleLeaderboard(env);
    else if (cmd === "help")   reply = await handleHelp();
    else if (cmd === "status") reply = await handleStatus();
    else reply = "פקודה לא מוכרת.";

    return twimlReply(reply);
  } catch (err) {
    console.error(err);
    return twimlReply("❌ שגיאה בשליפת הנתונים. נסה שוב בעוד רגע.");
  }
}

/**
 * POST /push — called by GitHub Actions after each game.
 * Body (JSON): { secret, message }
 * The `message` is the pre-formatted game summary string.
 */
async function handlePush(request, env) {
  let payload;
  try {
    payload = await request.json();
  } catch {
    return new Response("Bad JSON", { status: 400 });
  }

  if (payload.secret !== env.PUSH_SECRET) {
    return new Response("Unauthorized", { status: 401 });
  }

  const message = payload.message;
  if (!message) return new Response("No message", { status: 400 });

  // Send to the configured WhatsApp recipient (group or individual)
  const result = await sendWhatsApp(env, env.WHATSAPP_GROUP_ID, message);
  return new Response(JSON.stringify(result), {
    headers: { "Content-Type": "application/json" },
  });
}

/** GET /health */
function handleHealth() {
  return new Response(JSON.stringify({ ok: true, ts: Date.now() }), {
    headers: { "Content-Type": "application/json" },
  });
}

// ── Main router ───────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const url    = new URL(request.url);
    const method = request.method;

    if (url.pathname === "/health" && method === "GET")
      return handleHealth();

    if (url.pathname === "/webhook" && method === "POST")
      return handleWebhook(request, env);

    if (url.pathname === "/push" && method === "POST")
      return handlePush(request, env);

    return new Response("Not found", { status: 404 });
  },
};
