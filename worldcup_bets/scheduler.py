"""
scheduler.py — polls football-data.org every ~15 min and fires kickoff/post-game
messages automatically when game state transitions.

State is persisted in Google Sheets tab "Scheduler State" so restarts are safe.

Flow:
  1. Fetch all WC matches from football-data.org (current statuses)
  2. For each match that is IN_PLAY/PAUSED or FINISHED:
       a. Resolve the Sport5 game_id by fuzzy-matching team names
       b. Check "Scheduler State" sheet — has kickoff/postgame already been sent?
       c. Send the appropriate message and mark it done
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

import scraper
import analyzer
import sheets
import whatsapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
WC_COMPETITION     = 2000
STATE_TAB          = "Scheduler State"

# How long after a game starts do we still consider it "just kicked off"
# (handles cases where our poller was delayed)
KICKOFF_WINDOW_MINUTES = 30

# How long after a game finishes do we still try to send post-game
# (gives buffer for extra time, penalty shootouts)
POSTGAME_WINDOW_MINUTES = 60


# ── football-data.org ──────────────────────────────────────────────────────────

def get_active_matches(api_key: Optional[str] = None) -> list[dict]:
    """
    Return all WC matches that are currently actionable:
    IN_PLAY, PAUSED, or FINISHED within the last POSTGAME_WINDOW_MINUTES.
    Also includes SCHEDULED matches starting within KICKOFF_WINDOW_MINUTES
    (catches cases where our poller fired slightly early).
    """
    headers = {}
    if api_key:
        headers["X-Auth-Token"] = api_key

    url  = f"{FOOTBALL_DATA_BASE}/competitions/{WC_COMPETITION}/matches"
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    now = datetime.now(timezone.utc)
    out = []

    for m in resp.json().get("matches", []):
        status = m["status"]
        utc    = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        age    = (now - utc).total_seconds() / 60   # minutes since scheduled kickoff

        if status in ("IN_PLAY", "PAUSED"):
            out.append(_fd_row(m, utc))
        elif status == "FINISHED" and age < POSTGAME_WINDOW_MINUTES:
            out.append(_fd_row(m, utc))
        elif status in ("SCHEDULED", "TIMED") and -5 <= age <= KICKOFF_WINDOW_MINUTES:
            # Game should have started already (or is about to) — treat as kickoff candidate
            out.append(_fd_row(m, utc))

    log.info("Active/recent matches from football-data.org: %d", len(out))
    return out


def _fd_row(m: dict, utc: datetime) -> dict:
    score = m.get("score", {})
    ft    = score.get("fullTime", {})
    ht    = score.get("halfTime", {})
    return {
        "fd_id":    m["id"],
        "home":     m["homeTeam"]["name"],
        "away":     m["awayTeam"]["name"],
        "utc":      utc,
        "status":   m["status"],
        "score_ft": f"{ft.get('home',0)}:{ft.get('away',0)}" if ft.get("home") is not None else "",
        "score_ht": f"{ht.get('home',0)}:{ht.get('away',0)}" if ht.get("home") is not None else "",
    }


# ── Sport5 game resolution ─────────────────────────────────────────────────────

def get_sport5_games(token: str) -> list[dict]:
    """Pull the full game list from Sport5 via the first member's guesses."""
    group   = scraper.api_post("getGroup", token, membersGroup=scraper.GROUP_ID)
    members = group.get("members", [])
    if not members:
        raise RuntimeError("No Sport5 group members found")

    first_uid = members[0].get("_id", "")
    rounds    = scraper.get_friend_guesses(token, first_uid)
    games = []
    for round_ in rounds:
        for g in round_.get("games", []):
            games.append({
                "gid":        g.get("gid", ""),
                "team1":      g.get("team1", {}).get("name", ""),
                "team2":      g.get("team2", {}).get("name", ""),
                "round_name": round_.get("name", ""),
                "beggining":  g.get("beggining", 0),
            })
    return games


def fuzzy_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return a in b or b in a or (len(a) >= 5 and a[:5] == b[:5])


def resolve_sport5_game(fd_match: dict, sport5_games: list[dict]) -> Optional[dict]:
    """Find the Sport5 game dict that corresponds to a football-data match."""
    for sg in sport5_games:
        t1_ok = fuzzy_match(sg["team1"], fd_match["home"]) or fuzzy_match(sg["team1"], fd_match["away"])
        t2_ok = fuzzy_match(sg["team2"], fd_match["home"]) or fuzzy_match(sg["team2"], fd_match["away"])
        if t1_ok and t2_ok:
            return sg
    return None


def check_game_status(team1: str, team2: str, api_key: Optional[str] = None) -> str:
    """
    Look up the live status of a match by Sport5 team names (Hebrew).
    Returns football-data.org status string: FINISHED, IN_PLAY, PAUSED, SCHEDULED, etc.
    Returns "" if the match cannot be found.
    """
    try:
        headers = {}
        if api_key:
            headers["X-Auth-Token"] = api_key
        url  = f"{FOOTBALL_DATA_BASE}/competitions/{WC_COMPETITION}/matches"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
    except Exception as exc:
        log.warning("football-data.org status check failed: %s", exc)
        return ""

    for m in matches:
        home = m["homeTeam"]["name"]
        away = m["awayTeam"]["name"]
        if (fuzzy_match(team1, home) or fuzzy_match(team1, away)) and \
           (fuzzy_match(team2, home) or fuzzy_match(team2, away)):
            status = m["status"]
            log.info("Match status for %s vs %s: %s", team1, team2, status)
            return status

    log.warning("Could not find match in football-data.org for: %s vs %s", team1, team2)
    return ""


# ── Scheduler state (Google Sheets) ───────────────────────────────────────────

def load_state(spreadsheet) -> dict:
    """
    Returns dict keyed by sport5 game_id:
      { gid: { kickoff_sent: bool, postgame_sent: bool, ... } }
    """
    ws   = sheets.ensure_tab(spreadsheet, STATE_TAB)
    rows = ws.get_all_values()
    if not rows or len(rows) < 2:
        return {}

    headers = rows[0]
    state   = {}
    for row in rows[1:]:
        row_dict = dict(zip(headers, row))
        gid = row_dict.get("game_id", "")
        if gid:
            state[gid] = {
                "game_label":    row_dict.get("game_label", ""),
                "kickoff_sent":  row_dict.get("kickoff_sent", "").lower() == "true",
                "postgame_sent": row_dict.get("postgame_sent", "").lower() == "true",
            }
    return state


def save_state(spreadsheet, state: dict) -> None:
    ws      = sheets.ensure_tab(spreadsheet, STATE_TAB)
    headers = ["game_id", "game_label", "kickoff_sent", "kickoff_sent_at",
               "postgame_sent", "postgame_sent_at"]
    rows    = [headers]
    for gid, s in state.items():
        rows.append([
            gid,
            s.get("game_label", ""),
            str(s.get("kickoff_sent", False)),
            s.get("kickoff_sent_at", ""),
            str(s.get("postgame_sent", False)),
            s.get("postgame_sent_at", ""),
        ])
    ws.clear()
    ws.update(rows, value_input_option="USER_ENTERED")
    log.info("Scheduler state saved (%d games tracked)", len(state))


# ── Main scheduler loop ────────────────────────────────────────────────────────

def run():
    api_key  = os.environ.get("FOOTBALL_DATA_API_KEY")
    email    = os.environ["SPORT5_EMAIL"]
    password = os.environ["SPORT5_PASSWORD"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]

    # 1. Get active matches
    try:
        fd_matches = get_active_matches(api_key)
    except Exception as exc:
        log.error("football-data.org fetch failed: %s", exc)
        sys.exit(1)

    if not fd_matches:
        log.info("No active matches right now — nothing to do.")
        return

    # 2. Get Sport5 game catalogue + leaderboard (one login)
    log.info("Logging into Sport5...")
    token        = scraper.get_token(email, password)
    sport5_games = get_sport5_games(token)
    log.info("Sport5 game catalogue: %d games", len(sport5_games))

    # 3. Load persistent state from Sheets
    spreadsheet = sheets.get_sheet(sheet_id)
    state       = load_state(spreadsheet)
    now_str     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    state_dirty = False

    for fd in fd_matches:
        sg = resolve_sport5_game(fd, sport5_games)
        if not sg:
            log.warning("Could not resolve Sport5 game for: %s vs %s", fd["home"], fd["away"])
            continue

        gid        = sg["gid"]
        game_label = f"{sg['team1']} vs {sg['team2']}"
        if sg.get("round_name"):
            game_label += f" ({sg['round_name']})"

        game_state = state.setdefault(gid, {
            "game_label":       game_label,
            "kickoff_sent":     False,
            "kickoff_sent_at":  "",
            "postgame_sent":    False,
            "postgame_sent_at": "",
        })

        status = fd["status"]
        log.info("%-40s  fd_status=%-10s kickoff_sent=%s postgame_sent=%s",
                 game_label, status,
                 game_state["kickoff_sent"], game_state["postgame_sent"])

        # ── Kickoff message ──────────────────────────────────────────────────
        if not game_state["kickoff_sent"] and status in ("IN_PLAY", "PAUSED", "SCHEDULED", "TIMED"):
            log.info("  → Sending kickoff message for %s", game_label)
            try:
                bets, _ = scraper.run(gid, email, password)
                sheets.write_bets(spreadsheet, bets, game_label)
                whatsapp.notify_kickoff(bets, game_label)
                game_state["kickoff_sent"]    = True
                game_state["kickoff_sent_at"] = now_str
                state_dirty = True
                log.info("  ✅ Kickoff message sent")
            except Exception as exc:
                log.error("  ❌ Kickoff message failed: %s", exc)

        # ── Post-game message ────────────────────────────────────────────────
        if not game_state["postgame_sent"] and status == "FINISHED":
            log.info("  → Sending post-game message for %s", game_label)
            try:
                bets, leaderboard = scraper.run(gid, email, password)
                analysis          = analyzer.analyze(bets, leaderboard)
                analysis["summary"]["is_final"] = True  # scheduler only fires post-game on FINISHED
                sheets.write_all(analysis, game_label, sheet_id)

                what_if = None
                summary_s = analysis["summary"]
                if summary_s.get("actual_result") not in ("", "Not yet played"):
                    what_if = analyzer.what_if_analysis(analysis["enriched_bets"], summary_s["actual_result"])

                prev_board = sheets.read_previous_leaderboard_snapshot(spreadsheet, game_label)
                position_movers = analyzer.leaderboard_position_changes(
                    analysis["leaderboard"], prev_board
                ) if prev_board else None

                whatsapp.notify(analysis, game_label, what_if=what_if, position_movers=position_movers)
                game_state["postgame_sent"]    = True
                game_state["postgame_sent_at"] = now_str
                state_dirty = True
                log.info("  ✅ Post-game message sent")
            except Exception as exc:
                log.error("  ❌ Post-game failed: %s", exc)

    if state_dirty:
        save_state(spreadsheet, state)
    else:
        log.info("No state changes this run.")


if __name__ == "__main__":
    run()
