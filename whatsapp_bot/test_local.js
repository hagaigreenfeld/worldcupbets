/**
 * Local test — runs the worker logic directly without Twilio/WhatsApp.
 * Usage:
 *   SPORT5_EMAIL=... SPORT5_PASSWORD=... node test_local.js
 *
 * Tests: login → getGroup → getFriendGuesses → resolveLatestGame → parseCommand
 */

const SPORT5_BASE = "https://hevre.sport5.co.il/server/data.php";
const GROUP_ID    = "6a202c81f6f70af684071fd4";

const HEADERS = {
  "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
  "Origin":     "https://hevre.sport5.co.il",
  "Referer":    "https://hevre.sport5.co.il/",
};

async function postJson(type, body) {
  const res = await fetch(`${SPORT5_BASE}?type=${type}`, {
    method:  "POST",
    headers: { ...HEADERS, "Content-Type": "application/json" },
    body:    JSON.stringify(body),
  });
  const text = await res.text();
  try { return JSON.parse(text); }
  catch { throw new Error(`Non-JSON response (status ${res.status}): ${text.substring(0, 200)}`); }
}

async function postForm(type, body) {
  const res = await fetch(`${SPORT5_BASE}?type=${type}`, {
    method:  "POST",
    headers: { ...HEADERS, "Content-Type": "application/x-www-form-urlencoded" },
    body:    new URLSearchParams(body).toString(),
  });
  const text = await res.text();
  try { return JSON.parse(text); }
  catch { throw new Error(`Non-JSON response (status ${res.status}): ${text.substring(0, 200)}`); }
}

async function login(email, password) {
  console.log("\n▶ login (JSON)");
  const data = await postJson("loginUser", { email, password });
  if (!data.token) throw new Error("Login failed: " + JSON.stringify(data));
  console.log(`  ✅ token obtained, ${(data.guesses||[]).length} rounds in response`);
  return { token: data.token, guesses: data.guesses || [] };
}

async function testGetGroup(token) {
  console.log("\n▶ getGroup (JSON)");
  const data    = await postJson("getGroup", { token, membersGroup: GROUP_ID });
  const members = data.members || [];
  if (!members.length) throw new Error("getGroup returned 0 members");
  console.log(`  ✅ ${members.length} members, first: ${members[0].name}`);
  return members;
}

// Game list comes from loginUser response directly — no getFriendGuesses needed
function resolveLatestGame(guesses) {
  console.log("\n▶ resolveLatestGame (from login response guesses)");
  const now = Date.now();
  let best = null, bestDiff = Infinity;

  for (const round of guesses) {
    for (const g of round.games || []) {
      const ts = g.beggining; // Sport5 API typo — not kickoff/startTime
      if (!ts) continue;
      const diff = Math.abs(now - ts);
      if (diff < bestDiff) {
        bestDiff = diff;
        best = { gid: g.gid, team1: g.team1?.name, team2: g.team2?.name, roundName: round.name, ts, diff };
      }
    }
  }

  if (best) {
    const minsFromNow = Math.round(best.diff / 60000);
    console.log(`  ✅ Closest game: ${best.team1} vs ${best.team2}`);
    console.log(`     gid=${best.gid} | kickoff=${new Date(best.ts).toISOString()} | ${minsFromNow} min from now`);
  } else {
    console.log("  ❌ No game found");
  }
  return best;
}

function testParseCommand() {
  console.log("\n▶ parseCommand");
  const cases = [
    "ניחושים",
    "ניחושים abc123",
    "ניחושים abc123 ארגנטינה vs ברזיל",
    "תוצאות",
    "טבלה",
    "עזרה",
    "סטטוס",
    "blah",
  ];

  function parseCommand(text) {
    const t = (text || "").trim();
    if (["טבלה", "טבלת", "standings", "leaderboard", "דירוג"].some(k => t.toLowerCase().includes(k)))
      return { cmd: "leaderboard" };
    if (["עזרה", "help", "?", "פקודות"].some(k => t.toLowerCase().includes(k)))
      return { cmd: "help" };
    if (["סטטוס", "status", "ping"].some(k => t.toLowerCase().includes(k)))
      return { cmd: "status" };
    const kickoffMatch = t.match(/^ניחושים(?:\s+(\S+)(?:\s+(.+))?)?$/);
    if (kickoffMatch)
      return { cmd: "kickoff", gameId: kickoffMatch[1] || null, gameLabel: kickoffMatch[2] || null };
    const postGameMatch = t.match(/^תוצאות(?:\s+(\S+)(?:\s+(.+))?)?$/);
    if (postGameMatch)
      return { cmd: "post-game", gameId: postGameMatch[1] || null, gameLabel: postGameMatch[2] || null };
    return { cmd: null };
  }

  for (const c of cases) {
    const result = parseCommand(c);
    console.log(`  "${c}" → ${JSON.stringify(result)}`);
  }
}

async function main() {
  const email    = process.env.SPORT5_EMAIL;
  const password = process.env.SPORT5_PASSWORD;
  if (!email || !password) {
    console.error("Set SPORT5_EMAIL and SPORT5_PASSWORD env vars");
    process.exit(1);
  }

  try {
    const { token, guesses } = await login(email, password);
    await testGetGroup(token);
    resolveLatestGame(guesses);
    testParseCommand();
    console.log("\n✅ All tests passed");
  } catch (err) {
    console.error("\n❌ Test failed:", err.message);
    process.exit(1);
  }
}

main();
