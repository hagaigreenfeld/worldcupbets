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
 *   GITHUB_TOKEN         - Personal access token with actions:write scope
 *   GITHUB_REPO          - "owner/repo" e.g. "hagaigreenfeld/worldcupbets"
 */

const SPORT5_BASE = "https://hevre.sport5.co.il/server/data.php";
const GROUP_ID    = "6a202c81f6f70af684071fd4";

const NICKNAMES = {
  "Nir mish":        "בבה",
  "חיים אבירם":      "חיים",
  "adam aviram":     "אדם הראשון",
  "אלון גזית":       "גזה",
  "asaf gazit":      "גזה ג׳וניור",
  "חגי גרינפלד":     "חגי",
  "אדם אבירם":       "אדם השני",
  "מוטי דקל":        "מוטי",
  "בני אוחיון":      "בני",
  "אדיר":            "אדיר",
  "Eran Gazit":      "אח של גזה",
  "Avishay Shefer":  "אבישי",
  "Reshef Elias":    "רשף",
  "roi piro29":      "פירו",
  "Rom Mishali":     "דוד ג׳וניור",
  "סהר פירו":        "סהר פירו",
  "Yoav Pais":       "יואב",
  "יותם":            "יותם",
  "Eran Sandel":     "סנדל",
  "PIR0":            "פירו ג׳וניור",
};

function nickname(name) {
  return NICKNAMES[name.trim()] || name;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function sport5Post(type, token, body = {}) {
  const url     = `${SPORT5_BASE}?type=${type}`;
  const payload = new URLSearchParams({ token, ...body });
  console.log(`[sport5Post] POST ${url}`);
  const res  = await fetch(url, {
    method:  "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent":   "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
      "Origin":       "https://hevre.sport5.co.il",
      "Referer":      "https://hevre.sport5.co.il/",
    },
    body: payload.toString(),
  });
  console.log(`[sport5Post] status=${res.status}`);
  const text = await res.text();
  console.log(`[sport5Post] body preview: ${text.substring(0, 300)}`);
  try {
    return JSON.parse(text);
  } catch (err) {
    console.error(`[sport5Post] Failed to parse JSON. Status: ${res.status}. Body preview: ${text.substring(0, 500)}`);
    throw err;
  }
}

async function login(env) {
  const url = `${SPORT5_BASE}?type=loginUser`;
  console.log(`[login] POST ${url}`);
  const res  = await fetch(url, {
    method:  "POST",
    headers: {
      "Content-Type": "application/json",
      "User-Agent":   "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
      "Origin":       "https://hevre.sport5.co.il",
      "Referer":      "https://hevre.sport5.co.il/",
    },
    body:    JSON.stringify({ email: env.SPORT5_EMAIL, password: env.SPORT5_PASSWORD }),
  });
  console.log(`[login] status=${res.status}`);
  const text = await res.text();
  try {
    const data = JSON.parse(text);
    if (!data.token) throw new Error("Login failed: " + JSON.stringify(data));
    console.log(`[login] token obtained, keys in response: ${Object.keys(data).join(", ")}`);
    return data.token;
  } catch (err) {
    console.error(`[login] Failed to parse JSON. Status: ${res.status}. Body preview: ${text.substring(0, 500)}`);
    throw err;
  }
}

async function getLeaderboard(token) {
  const data = await sport5Post("getGroup", token, { membersGroup: GROUP_ID });
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

/**
 * Fetch all games from Sport5 (via first member's guesses) and return the
 * one closest to now — i.e. the game that just started or most recently played.
 */
async function resolveLatestGame(token) {
  const groupData = await sport5Post("getGroup", token, { membersGroup: GROUP_ID });
  const members   = groupData.members || [];
  if (!members.length) throw new Error("No group members found");

  const firstUid = members[0]?._id?.$oid || members[0]?.userId || "";
  const guesses  = await sport5Post("getFriendGuesses", token,
                                    { friendId: firstUid, groupId: GROUP_ID });

  const now = Date.now();
  let best = null, bestDiff = Infinity;

  for (const round of guesses.guesses || []) {
    for (const g of round.games || []) {
      const raw = g.kickoff || g.startTime;
      if (!raw) continue;
      // Sport5 kickoff can be a Unix ms timestamp or an ISO string
      const ts   = typeof raw === "number" ? raw : (String(raw).includes("T") ? new Date(raw).getTime() : parseInt(raw));
      const diff = Math.abs(now - ts);
      if (diff < bestDiff) {
        bestDiff = diff;
        best = {
          gid:       g.gid,
          team1:     g.team1?.name || "",
          team2:     g.team2?.name || "",
          roundName: round.name || "",
        };
      }
    }
  }

  return best;
}

async function handleLeaderboard(env) {
  const token = await login(env);
  const board = await getLeaderboard(token);

  const medals = ["🥇", "🥈", "🥉"];
  const lines  = board.map((r, i) => {
    const medal = medals[i] || `${r.rank}.`;
    return `${medal} ${nickname(r.name)} - ${r.points}`;
  });

  return `⚽ *טבלת חבר'ה קדרון*\n\n${lines.join("\n")}\n\n🕐 עודכן עכשיו`;
}

async function handleHelp() {
  return `🤖 *פקודות הבוט*

*טבלה* — טבלת הניקוד הנוכחית
*ניחושים* — שליפת ניחושים למשחק האחרון/הנוכחי
*תוצאות* — סיכום ניקוד למשחק האחרון
*עזרה* — הצגת פקודות זמינות
*סטטוס* — הבוט חי ומוכן ✅`;
}

async function handleStatus() {
  return "✅ הבוט פעיל ומחכה למשחקים!";
}

/**
 * Trigger GitHub Actions workflow_dispatch for kickoff or post-game mode.
 * gameId and gameLabel come from the WhatsApp message.
 */
async function triggerWorkflow(env, gameId, gameLabel, runMode) {
  const repo = env.GITHUB_REPO; // e.g. "hagaigreenfeld/worldcupbets"
  const url  = `https://api.github.com/repos/${repo}/actions/workflows/worldcup.yml/dispatches`;

  const res = await fetch(url, {
    method:  "POST",
    headers: {
      Authorization:  `Bearer ${env.GITHUB_TOKEN}`,
      "Content-Type": "application/json",
      Accept:         "application/vnd.github+json",
      "User-Agent":   "worldcupbets-bot",
    },
    body: JSON.stringify({
      ref:    "main",
      inputs: { game_id: gameId, game_label: gameLabel, run_mode: runMode },
    }),
  });

  return res.status; // 204 = success
}

async function resolveGame(env, gameId, gameLabel) {
  if (gameId) return { gameId, gameLabel: gameLabel || gameId };

  const token = await login(env);
  const game  = await resolveLatestGame(token);
  if (!game) throw new Error("לא נמצא משחק");

  const label = game.team1 && game.team2
    ? `${game.team1} vs ${game.team2}${game.roundName ? ` (${game.roundName})` : ""}`
    : game.gid;
  return { gameId: game.gid, gameLabel: label };
}

async function handleKickoff(env, gameId, gameLabel) {
  let resolved;
  try {
    resolved = await resolveGame(env, gameId, gameLabel);
  } catch (err) {
    return `❌ לא הצלחתי למצוא משחק: ${err.message}`;
  }

  const status = await triggerWorkflow(env, resolved.gameId, resolved.gameLabel, "kickoff");
  if (status === 204) {
    return `⚽ *${resolved.gameLabel}*\n🚀 שולף ניחושים... תקבל הודעה בעוד ~30 שניות`;
  }
  return `❌ שגיאה בהפעלת הניחושים (status ${status}). בדוק את GITHUB_TOKEN.`;
}

async function handlePostGame(env, gameId, gameLabel) {
  let resolved;
  try {
    resolved = await resolveGame(env, gameId, gameLabel);
  } catch (err) {
    return `❌ לא הצלחתי למצוא משחק: ${err.message}`;
  }

  const status = await triggerWorkflow(env, resolved.gameId, resolved.gameLabel, "post-game");
  if (status === 204) {
    return `⚽ *${resolved.gameLabel}*\n📊 מחשב תוצאות... תקבל סיכום בעוד ~60 שניות`;
  }
  return `❌ שגיאה (status ${status}). בדוק את GITHUB_TOKEN.`;
}

// Parse "ניחושים abc123 ארגנטינה vs ברזיל" → { cmd, gameId, gameLabel }
function parseCommand(text) {
  const t = (text || "").trim();

  if (["טבלה", "טבלת", "standings", "leaderboard", "דירוג"].some(k => t.toLowerCase().includes(k)))
    return { cmd: "leaderboard" };
  if (["עזרה", "help", "?", "פקודות"].some(k => t.toLowerCase().includes(k)))
    return { cmd: "help" };
  if (["סטטוס", "status", "ping"].some(k => t.toLowerCase().includes(k)))
    return { cmd: "status" };

  // "ניחושים [game_id [label...]]" — game_id optional, defaults to latest game
  const kickoffMatch = t.match(/^ניחושים(?:\s+(\S+)(?:\s+(.+))?)?$/);
  if (kickoffMatch)
    return { cmd: "kickoff", gameId: kickoffMatch[1] || null, gameLabel: kickoffMatch[2] || null };

  // "תוצאות [game_id [label...]]" — game_id optional, defaults to latest game
  const postGameMatch = t.match(/^תוצאות(?:\s+(\S+)(?:\s+(.+))?)?$/);
  if (postGameMatch)
    return { cmd: "post-game", gameId: postGameMatch[1] || null, gameLabel: postGameMatch[2] || null };

  return { cmd: null };
}

// ── Route handlers ────────────────────────────────────────────────────────────

/** POST /webhook — called by Twilio for incoming WhatsApp messages */
async function handleWebhook(request, env) {
  const body    = await request.text();
  const params  = new URLSearchParams(body);
  const msgBody = params.get("Body") || "";
  const from    = params.get("From") || "";

  console.log(`[webhook] from=${from} body="${msgBody}"`);

  const { cmd, gameId, gameLabel } = parseCommand(msgBody);
  if (!cmd) {
    return twimlReply('לא הבנתי 🤔 שלח *עזרה* לרשימת פקודות');
  }

  try {
    let reply;
    if      (cmd === "leaderboard") reply = await handleLeaderboard(env);
    else if (cmd === "help")        reply = await handleHelp();
    else if (cmd === "status")      reply = await handleStatus();
    else if (cmd === "kickoff")     reply = await handleKickoff(env, gameId, gameLabel);
    else if (cmd === "post-game")   reply = await handlePostGame(env, gameId, gameLabel);
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
