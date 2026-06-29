"""
Sport5 Hevre - World Cup Bets Scraper
Calls the Sport5 API to fetch group members and their bets,
then writes everything to Google Sheets and analyzes results.

API Base: https://hevre.sport5.co.il/server/data.php
Group ID: 6a202c81f6f70af684071fd4
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
BASE_URL   = "https://hevre.sport5.co.il/server/data.php"
GROUP_ID   = "6a202c81f6f70af684071fd4"
INFO_URL   = "https://hevre.sport5.co.il/data/info.json"

# ── Auth ───────────────────────────────────────────────────────────────────────

def get_token(email: str, password: str) -> str:
    """Login and return JWT token."""
    log.info("Logging in as %s", email)
    resp = requests.post(
        BASE_URL,
        params={"type": "loginUser"},
        json={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "token" not in data:
        raise RuntimeError(f"Login failed: {data}")
    log.info("Login successful")
    return data["token"]


# ── API helpers ────────────────────────────────────────────────────────────────

SPORT5_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Origin":     "https://hevre.sport5.co.il",
    "Referer":    "https://hevre.sport5.co.il/",
    "Content-Type": "application/json",
}


def api_post(type_: str, token: str, **kwargs) -> dict:
    """POST to data.php with JSON body. getGroup requires JSON (not form-encoded)."""
    payload = {"token": token, **kwargs}
    resp = requests.post(
        BASE_URL,
        params={"type": type_},
        json=payload,
        headers=SPORT5_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_group_members(token: str) -> list[dict]:
    """Return list of group members with name, _id, points."""
    data = api_post("getGroup", token, membersGroup=GROUP_ID)
    members = data.get("members", [])
    log.info("Fetched %d group members", len(members))
    return members


def get_friend_guesses(token: str, friend_user_id: str) -> list:
    """
    Return all rounds (list) for a specific member.
    Uses user/auid params — getFriendGuesses with friendId/groupId doesn't work.
    Bet score is in team1.team1Guessed / team2.team2Guessed (not scoreGuess).
    """
    resp = requests.post(
        BASE_URL,
        params={"type": "getFriendGuesses"},
        json={"user": friend_user_id, "auid": friend_user_id, "userEmail": ""},
        headers=SPORT5_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    # Returns a list of rounds directly (not wrapped in {"guesses": [...]})
    return data if isinstance(data, list) else []


def get_game_info() -> dict:
    """Fetch the global info.json (scoring rules, competition id, etc.)"""
    resp = requests.get(INFO_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ── Data extraction ────────────────────────────────────────────────────────────

def extract_bets_for_game(member: dict, rounds: list, game_id: str) -> Optional[dict]:
    """
    From a member's rounds list (returned by get_friend_guesses), pull out the
    bet for one specific game.

    Actual API structure (getFriendGuesses returns a list of rounds):
    [
      {
        "name": "מחזור 1",
        "games": [
          {
            "gid": "<game_id>",
            "team1": { "name": "...", "team1Guessed": "2", ... },
            "team2": { "name": "...", "team2Guessed": "1", ... },
            "result1": "2",        # actual result goals (post-game)
            "result2": "0",
            "gamepoints": 4,       # points earned
            "typeOfGuess": "exact" | "gettingthere" | "nothingyet" | "",
          }
        ]
      }
    ]
    """
    name    = member.get("name", "Unknown")
    user_id = member.get("_id", "")

    for round_ in rounds:
        for game in round_.get("games", []):
            if game.get("gid") == game_id:
                t1      = game.get("team1", {})
                t2      = game.get("team2", {})
                team1   = t1.get("name", "")
                team2   = t2.get("name", "")
                g1      = t1.get("team1Guessed")
                g2      = t2.get("team2Guessed")

                # Derive winner direction from guessed scores
                if g1 is not None and g2 is not None:
                    try:
                        g1i, g2i = int(g1), int(g2)
                        guess_winner = "team1" if g1i > g2i else ("team2" if g2i > g1i else "draw")
                    except (ValueError, TypeError):
                        guess_winner = ""
                    score_guess = f"{g1}:{g2}"
                else:
                    guess_winner = ""
                    score_guess  = ""

                # Actual result from result1/result2
                r1, r2 = game.get("result1"), game.get("result2")
                actual_result = f"{r1}:{r2}" if r1 is not None and r2 is not None else ""

                # result1/result2 can be 0 at game start (0:0), so we need typeOfGuess
                # to distinguish finished from in-progress.
                # "nothingyet" = in-progress but bet not matching yet — NOT finished.
                # Only "exact"/"gettingthere"/"miss" mean the game is definitively over.
                tog           = game.get("typeOfGuess") or ""
                game_finished = r1 is not None and r2 is not None and tog in ("exact", "gettingthere", "miss")
                points_won    = game.get("gamepoints", 0) or 0

                # For finished games use actual points; for live/upcoming use Sport5 projection.
                if game_finished:
                    pot = float(points_won)
                else:
                    pot = _calc_potential(game, guess_winner)

                return {
                    "player_name":      name,
                    "user_id":          user_id,
                    "team1":            team1,
                    "team2":            team2,
                    "guess_winner":     guess_winner,
                    "score_guess":      score_guess,
                    "actual_result":    actual_result,
                    "points_won":       points_won,
                    "potential_points": pot,
                    "round_name":       round_.get("name", ""),
                }
    return None


def _calc_potential(game: dict, guess_winner: str) -> float:
    """
    Max potential points for an exact-score guess (used in kickoff/ניחושים display).
    Sport5 formula (verified empirically):
      direction points = ratio × mult
      exact points     = ratio × mult + bonusExact  (additive, not multiplicative)
    Ratio mapping: ratio1=team1 wins, ratio2=team2 wins, ratio3=draw.
    Finished games use gamepoints directly — this is only for pre-game estimates.
    """
    if not guess_winner:
        return 0
    try:
        ratio_map = {
            "team1": game.get("ratio1", 0),
            "team2": game.get("ratio2", 0),
            "draw":  game.get("ratio3", 0),
        }
        ratio = ratio_map.get(guess_winner, 0) or 0
        fd    = game.get("fixturedata", {})
        mult  = fd.get("pointsMultplyer", 1) or 1
        bonus = fd.get("bonusExact", 4) or 4
        return round(ratio * mult + bonus, 1)
    except Exception:
        return 0


def scrape_all_bets_for_game(token: str, game_id: str) -> list[dict]:
    """
    For all 20 group members, fetch their bets and return rows for the given game.
    """
    members = get_group_members(token)
    all_rows = []

    for member in members:
        uid = member.get("_id", "")
        name = member.get("name", "?")
        log.info("  → Fetching bets for %s (%s)", name, uid)

        try:
            guesses = get_friend_guesses(token, uid)
            row = extract_bets_for_game(member, guesses, game_id)
            if row:
                all_rows.append(row)
            else:
                log.warning("    No bet found for game %s", game_id)
                all_rows.append({
                    "player_name":      name,
                    "user_id":          uid,
                    "team1":            "",
                    "team2":            "",
                    "guess_winner":     "N/A",
                    "score_guess":      "N/A",
                    "actual_result":    "",
                    "points_won":       0,
                    "potential_points": 0,
                    "round_name":       "",
                })
        except Exception as exc:
            log.error("    Error fetching for %s: %s", name, exc)

        time.sleep(0.3)   # be polite to the server

    return all_rows


def build_leaderboard(members: list[dict]) -> list[dict]:
    """
    Build current leaderboard from the group members list
    (points already computed by Sport5 server).
    """
    board = sorted(members, key=lambda m: m.get("points", 0), reverse=True)
    return [
        {
            "rank":   i + 1,
            "name":   m.get("name", "?"),
            "points": m.get("points", 0),
        }
        for i, m in enumerate(board)
    ]


# ── Entry point (used by main.py) ──────────────────────────────────────────────

def run(game_id: str, email: str, password: str) -> tuple[list[dict], list[dict]]:
    """
    Full scrape run.
    Returns (bets_rows, leaderboard_rows).
    """
    token   = get_token(email, password)
    bets    = scrape_all_bets_for_game(token, game_id)
    members = get_group_members(token)
    board   = build_leaderboard(members)
    return bets, board


def get_upcoming_games_with_odds(token: str, n: int = 999) -> list[dict]:
    """
    Return unplayed games sorted by kickoff time (earliest first).
    Each item includes team names, ratios, max potential points, and kickoff timestamp.
    n=999 means all remaining games; pass a smaller number to cap.
    """
    group   = api_post("getGroup", token, membersGroup=GROUP_ID)
    members = group.get("members", [])
    if not members:
        raise RuntimeError("No group members found")

    first_uid = members[0].get("_id", "")
    rounds    = get_friend_guesses(token, first_uid)

    upcoming = []
    for round_ in rounds:
        round_name = round_.get("name", "")
        for g in round_.get("games", []):
            r1, r2 = g.get("result1"), g.get("result2")
            if r1 is not None and str(r1) != "":
                continue  # already played

            gid   = g.get("gid", "")
            team1 = (g.get("team1") or {}).get("name", "")
            team2 = (g.get("team2") or {}).get("name", "")
            if not gid or not team1 or not team2:
                continue

            fd          = g.get("fixturedata") or {}
            mult        = float(fd.get("pointsMultplyer", 1) or 1)
            bonus_exact = float(fd.get("bonusExact", 4) or 4)
            ratio1      = float(g.get("ratio1", 0) or 0)
            ratio2      = float(g.get("ratio2", 0) or 0)
            ratio3      = float(g.get("ratio3", 0) or 0)
            kickoff_ts  = g.get("beggining", 0) or 0

            upcoming.append({
                "gid":          gid,
                "team1":        team1,
                "team2":        team2,
                "round_name":   round_name,
                "ratio1":       ratio1,
                "ratio2":       ratio2,
                "ratio3":       ratio3,
                "mult":         mult,
                "bonus_exact":  bonus_exact,
                "max_pts_team1": round(ratio1 * mult + bonus_exact, 1),
                "max_pts_draw":  round(ratio3 * mult + bonus_exact, 1),
                "max_pts_team2": round(ratio2 * mult + bonus_exact, 1),
                "kickoff_ts":   kickoff_ts,
            })

    upcoming.sort(key=lambda g: g["kickoff_ts"] or float("inf"))
    log.info("Upcoming games: %d total, returning up to %d", len(upcoming), n)
    return upcoming[:n]


def scrape_all_players_upcoming(token: str, game_gids: list[str]) -> dict[str, list[dict]]:
    """
    For multiple upcoming game IDs, return all players' current bets.
    Fetches each player's rounds once (efficient) and extracts bets for all games.
    Returns { gid: [bet_rows] }.
    """
    members = get_group_members(token)
    result  = {gid: [] for gid in game_gids}

    for member in members:
        uid  = member.get("_id", "")
        name = member.get("name", "?")
        log.info("  → Fetching upcoming bets for %s", name)

        try:
            rounds = get_friend_guesses(token, uid)
            for gid in game_gids:
                row = extract_bets_for_game(member, rounds, gid)
                result[gid].append(row if row else {
                    "player_name":      name,
                    "user_id":          uid,
                    "team1":            "",
                    "team2":            "",
                    "guess_winner":     "",
                    "score_guess":      "",
                    "actual_result":    "",
                    "points_won":       0,
                    "potential_points": 0,
                    "round_name":       "",
                })
        except Exception as exc:
            log.error("    Error fetching for %s: %s", name, exc)

        time.sleep(0.3)

    return result


if __name__ == "__main__":
    # Quick local test (set env vars before running)
    import sys
    EMAIL    = os.environ["SPORT5_EMAIL"]
    PASSWORD = os.environ["SPORT5_PASSWORD"]
    GAME_ID  = sys.argv[1] if len(sys.argv) > 1 else "test_game_id"

    bets, board = run(GAME_ID, EMAIL, PASSWORD)
    print("\n=== BETS ===")
    for row in bets:
        print(row)
    print("\n=== LEADERBOARD ===")
    for row in board:
        print(f"{row['rank']}. {row['name']} — {row['points']} pts")
