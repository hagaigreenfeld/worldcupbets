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
  const url = `${SPORT5_BASE}?type=${type}`;
  console.log(`[sport5Post] POST ${url}`);
  const res  = await fetch(url, {
    method:  "POST",
    headers: {
      "Content-Type": "application/json",
      "User-Agent":   "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
      "Origin":       "https://hevre.sport5.co.il",
      "Referer":      "https://hevre.sport5.co.il/",
    },
    body: JSON.stringify({ token, ...body }),
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

// Returns { token, guesses } — guesses contains all rounds/games for the logged-in user
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
    console.log(`[login] token obtained, ${(data.guesses || []).length} rounds in response`);
    return { token: data.token, guesses: data.guesses || [] };
  } catch (err) {
    console.error(`[login] Failed to parse JSON. Status: ${res.status}. Body preview: ${text.substring(0, 500)}`);
    throw err;
  }
}

async function getLeaderboard(token) {
  const data    = await sport5Post("getGroup", token, { membersGroup: GROUP_ID });
  const members = data.members || [];
  return members
    .sort((a, b) => (b.points || 0) - (a.points || 0))
    .map((m, i) => ({ rank: i + 1, name: m.name, points: m.points || 0 }));
}

// Extract the most recent STARTED game from the guesses array returned by login.
// "Started" means beggining <= now. The timestamp field is "beggining" (Sport5 API typo).
// Returns { gid, team1, team2, roundName, kickoff } or null.
function resolveLatestGame(guesses) {
  const all = listStartedGames(guesses);
  return all.length ? all[0] : null;
}

// Returns all started games sorted by kickoff descending.
function listStartedGames(guesses) {
  const now = Date.now();
  const games = [];

  for (const round of guesses) {
    for (const g of round.games || []) {
      const ts = g.beggining;
      if (!ts || ts > now) continue;
      games.push({
        gid:       g.gid,
        team1:     g.team1?.name || "",
        team2:     g.team2?.name || "",
        roundName: round.name || "",
        kickoff:   ts,
      });
    }
  }

  games.sort((a, b) => b.kickoff - a.kickoff);
  return games;
}

function gameLabel(game) {
  if (game.team1 && game.team2)
    return `${game.team1} vs ${game.team2}${game.roundName ? ` (${game.roundName})` : ""}`;
  return game.gid;
}

function formatGamePicker(games, offset, total, cmdHebrew) {
  const lines = games.map((g, i) => `${i + 1}. ${gameLabel(g)}`);
  let msg = `⚽ *בחר משחק ל${cmdHebrew}:*\n\n${lines.join("\n")}`;
  if (offset + games.length < total) msg += `\n\n_שלח *עוד* לעוד משחקים_`;
  msg += `\n\n_שלח מספר לבחירה_`;
  return msg;
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
  const { token } = await login(env);
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
*ניחושים* — בחר משחק ושלוף ניחושים
*תוצאות* — בחר משחק וקבל סיכום ניקוד
*עזרה* — הצגת פקודות זמינות
*סטטוס* — הבוט חי ומוכן ✅

_לאחר שליחת ניחושים/תוצאות תוצג רשימת משחקים — שלח מספר לבחירה או *עוד* לעוד משחקים_`;
}

async function handleStatus() {
  return "✅ הבוט פעיל ומחכה למשחקים!";
}

/**
 * Trigger GitHub Actions workflow_dispatch for kickoff or post-game mode.
 * gameId and gameLabel come from the WhatsApp message.
 */
// Returns true if this command was already triggered recently (within TTL seconds).
// Uses KV for persistence across requests. Falls back to allowing if KV not set up.
async function isDuplicate(env, key, ttlSeconds = 90) {
  if (!env.DEDUP_KV) return false;
  const existing = await env.DEDUP_KV.get(key);
  if (existing) return true;
  await env.DEDUP_KV.put(key, "1", { expirationTtl: ttlSeconds });
  return false;
}

async function triggerWorkflow(env, gameId, gameLabel, runMode, sender = "") {
  const repo = env.GITHUB_REPO; // e.g. "hagaigreenfeld/worldcupbets"
  if (!repo) throw new Error("GITHUB_REPO secret not set in Cloudflare");
  const token = (env.GITHUB_TOKEN || "").trim();
  if (!token) throw new Error("GITHUB_TOKEN secret not set in Cloudflare");

  const url  = `https://api.github.com/repos/${repo}/actions/workflows/worldcup.yml/dispatches`;
  console.log(`[triggerWorkflow] POST ${url} mode=${runMode} game=${gameId}`);

  const res  = await fetch(url, {
    method:  "POST",
    headers: {
      Authorization:  `Bearer ${token}`,
      "Content-Type": "application/json",
      Accept:         "application/vnd.github+json",
      "User-Agent":   "worldcupbets-bot",
    },
    body: JSON.stringify({
      ref:    "main",
      inputs: { game_id: gameId, game_label: gameLabel, run_mode: runMode, sender },
    }),
  });

  if (res.status !== 204) {
    const body = await res.text();
    console.error(`[triggerWorkflow] failed status=${res.status} body=${body}`);
    throw new Error(`GitHub API status ${res.status}: ${body.substring(0, 200)}`);
  }
  return res.status;
}

async function resolveGame(env, gameId, gameLabelArg) {
  if (gameId) return { gameId, gameLabel: gameLabelArg || gameId };

  const { guesses } = await login(env);
  const game = resolveLatestGame(guesses);
  if (!game) throw new Error("אין משחק שהתחיל עדיין");

  return { gameId: game.gid, gameLabel: gameLabel(game) };
}

const PICKER_PAGE_SIZE = 5;

// Show game picker and store pending state in KV.
async function showGamePicker(env, from, cmd, offset = 0) {
  const { guesses } = await login(env);
  const allGames = listStartedGames(guesses);
  if (!allGames.length) return "❌ אין משחקים שהתחילו עדיין";

  const page = allGames.slice(offset, offset + PICKER_PAGE_SIZE);
  const cmdHebrew = cmd === "kickoff" ? "ניחושים" : "תוצאות";

  if (env.DEDUP_KV) {
    await env.DEDUP_KV.put(
      `pending:${from}`,
      JSON.stringify({ cmd, games: allGames, offset }),
      { expirationTtl: 300 }
    );
  }

  return formatGamePicker(page, offset, allGames.length, cmdHebrew);
}

async function handleSelection(env, selection, from) {
  if (!env.DEDUP_KV) return "❌ KV לא מוגדר";

  const raw = await env.DEDUP_KV.get(`pending:${from}`);
  if (!raw) return "❓ לא נמצאה בחירה פעילה. שלח *ניחושים* או *תוצאות* להתחלה.";

  const state = JSON.parse(raw);

  if (selection === "עוד") {
    const newOffset = state.offset + PICKER_PAGE_SIZE;
    if (newOffset >= state.games.length) return "אין עוד משחקים.";
    return showGamePickerFromState(env, from, state.cmd, state.games, newOffset);
  }

  const idx = parseInt(selection, 10) - 1;
  const pageGames = state.games.slice(state.offset, state.offset + PICKER_PAGE_SIZE);
  if (isNaN(idx) || idx < 0 || idx >= pageGames.length)
    return `❌ בחירה לא תקינה. שלח מספר בין 1 ל-${pageGames.length}.`;

  const chosen = pageGames[idx];
  await env.DEDUP_KV.delete(`pending:${from}`);

  if (state.cmd === "kickoff")  return handleKickoff(env, chosen.gid, gameLabel(chosen), from);
  if (state.cmd === "post-game") return handlePostGame(env, chosen.gid, gameLabel(chosen), from);
  return "❌ פקודה לא מוכרת בסטייט.";
}

async function showGamePickerFromState(env, from, cmd, allGames, offset) {
  const page = allGames.slice(offset, offset + PICKER_PAGE_SIZE);
  const cmdHebrew = cmd === "kickoff" ? "ניחושים" : "תוצאות";

  if (env.DEDUP_KV) {
    await env.DEDUP_KV.put(
      `pending:${from}`,
      JSON.stringify({ cmd, games: allGames, offset }),
      { expirationTtl: 300 }
    );
  }

  return formatGamePicker(page, offset, allGames.length, cmdHebrew);
}

async function handleKickoff(env, gameId, gameLabelArg, sender) {
  if (!gameId) return showGamePicker(env, sender, "kickoff");

  let resolved;
  try {
    resolved = await resolveGame(env, gameId, gameLabelArg);
  } catch (err) {
    return `❌ לא הצלחתי למצוא משחק: ${err.message}`;
  }

  const dedupKey = `kickoff:${resolved.gameId}`;
  if (await isDuplicate(env, dedupKey, 180)) {
    return `⚽ *${resolved.gameLabel}*\n⏳ כבר בטיפול, תקבל הודעה בעוד רגע...`;
  }

  try {
    await triggerWorkflow(env, resolved.gameId, resolved.gameLabel, "kickoff", sender);
  } catch (err) {
    return `❌ שגיאה בהפעלת הניחושים: ${err.message}`;
  }
  return `⚽ *${resolved.gameLabel}*\n🚀 שולף ניחושים... תקבל הודעה בעוד ~30 שניות`;
}

async function handlePostGame(env, gameId, gameLabelArg, sender) {
  if (!gameId) return showGamePicker(env, sender, "post-game");

  let resolved;
  try {
    resolved = await resolveGame(env, gameId, gameLabelArg);
  } catch (err) {
    return `❌ לא הצלחתי למצוא משחק: ${err.message}`;
  }

  const dedupKey = `postgame:${resolved.gameId}`;
  if (await isDuplicate(env, dedupKey, 180)) {
    return `⚽ *${resolved.gameLabel}*\n⏳ כבר בטיפול, תקבל סיכום בעוד רגע...`;
  }

  try {
    await triggerWorkflow(env, resolved.gameId, resolved.gameLabel, "post-game", sender);
  } catch (err) {
    return `❌ שגיאה בהפעלת התוצאות: ${err.message}`;
  }
  return `⚽ *${resolved.gameLabel}*\n📊 מחשב תוצאות... תקבל סיכום בעוד ~60 שניות`;
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

  // "ניחושים [game_id [label...]]" — game_id optional, defaults to picker
  const kickoffMatch = t.match(/^ניחושים(?:\s+(\S+)(?:\s+(.+))?)?$/);
  if (kickoffMatch)
    return { cmd: "kickoff", gameId: kickoffMatch[1] || null, gameLabel: kickoffMatch[2] || null };

  // "תוצאות [game_id [label...]]" — game_id optional, defaults to picker
  const postGameMatch = t.match(/^תוצאות(?:\s+(\S+)(?:\s+(.+))?)?$/);
  if (postGameMatch)
    return { cmd: "post-game", gameId: postGameMatch[1] || null, gameLabel: postGameMatch[2] || null };

  // Number selection (1-9) or "עוד" — used when a picker is active
  if (/^[1-9]$/.test(t)) return { cmd: "selection", selection: t };
  if (t === "עוד")        return { cmd: "selection", selection: "עוד" };

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

  const { cmd, gameId, gameLabel, selection } = parseCommand(msgBody);
  if (!cmd) {
    return twimlReply('לא הבנתי 🤔 שלח *עזרה* לרשימת פקודות');
  }

  try {
    let reply;
    if      (cmd === "leaderboard") reply = await handleLeaderboard(env);
    else if (cmd === "help")        reply = await handleHelp();
    else if (cmd === "status")      reply = await handleStatus();
    else if (cmd === "kickoff")     reply = await handleKickoff(env, gameId, gameLabel, from);
    else if (cmd === "post-game")   reply = await handlePostGame(env, gameId, gameLabel, from);
    else if (cmd === "selection")   reply = await handleSelection(env, selection, from);
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

  // Send to specified `to`, or fall back to the configured group number
  const to = payload.to || env.WHATSAPP_GROUP_ID;
  const twilioRes = await sendWhatsApp(env, to, message);
  const sid    = twilioRes.sid || "(no sid)";
  const status = twilioRes.status || twilioRes.error_message || "(unknown)";
  const msgLen = message.length;
  console.log(`[push] to=${to} msg_len=${msgLen} twilio_sid=${sid} twilio_status=${status}`);
  if (twilioRes.error_code || twilioRes.error_message) {
    console.error(`[push] Twilio error: code=${twilioRes.error_code} msg=${twilioRes.error_message}`);
    return new Response(JSON.stringify(twilioRes), { status: 502, headers: { "Content-Type": "application/json" } });
  }
  return new Response(JSON.stringify(twilioRes), {
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

