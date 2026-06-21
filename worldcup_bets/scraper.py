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
        params={"type": "appUserLogin"},
        data={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "token" not in data:
        raise RuntimeError(f"Login failed: {data}")
    log.info("Login successful")
    return data["token"]


# ── API helpers ────────────────────────────────────────────────────────────────

def api_post(type_: str, token: str, **kwargs) -> dict:
    """POST to data.php with type + token + optional extra fields."""
    payload = {"token": token, **kwargs}
    resp = requests.post(
        BASE_URL,
        params={"type": type_},
        data=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_group_members(token: str) -> list[dict]:
    """Return list of group members with name, userId, points."""
    data = api_post("getGroup", token, groupId=GROUP_ID)
    members = data.get("members", [])
    log.info("Fetched %d group members", len(members))
    return members


def get_friend_guesses(token: str, friend_user_id: str) -> dict:
    """Return all guesses for a specific member (by their userId / _id.$oid)."""
    data = api_post("getFriendGuesses", token, friendId=friend_user_id, groupId=GROUP_ID)
    return data


def get_game_info() -> dict:
    """Fetch the global info.json (scoring rules, competition id, etc.)"""
    resp = requests.get(INFO_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ── Data extraction ────────────────────────────────────────────────────────────

def extract_bets_for_game(member: dict, guesses: dict, game_id: str) -> Optional[dict]:
    """
    From a member's full guesses object, pull out the bet for one specific game.

    guesses structure (observed):
    {
      "guesses": [
        {
          "name": "מחזור 1",
          "fid": 0,
          "games": [
            {
              "gid": "<game_id>",
              "team1": { "name": "...", "tid": "...", "ratio": 150 },
              "team2": { "name": "...", "tid": "...", "ratio": 80  },
              "guess": "team1" | "team2" | "draw" | null,
              "scoreGuess": "2:1",      # exact score guess
              "fixtureResult": "2:0",   # actual result (if finished)
              "pointsWon": 4,           # points earned
              "potentialPoints": 4,     # max possible points
              ...
            }
          ]
        }
      ]
    }
    """
    name    = member.get("name", "Unknown")
    user_id = member.get("_id", {}).get("$oid", member.get("userId", ""))

    for round_ in guesses.get("guesses", []):
        for game in round_.get("games", []):
            if game.get("gid") == game_id:
                team1   = game.get("team1", {}).get("name", "")
                team2   = game.get("team2", {}).get("name", "")
                return {
                    "player_name":      name,
                    "user_id":          user_id,
                    "team1":            team1,
                    "team2":            team2,
                    "guess_winner":     game.get("guess", ""),        # team1/team2/draw
                    "score_guess":      game.get("scoreGuess", ""),   # e.g. "2:1"
                    "actual_result":    game.get("fixtureResult", ""),
                    "points_won":       game.get("pointsWon", 0),
                    "potential_points": game.get("potentialPoints", 0),
                    "round_name":       round_.get("name", ""),
                }
    return None


def scrape_all_bets_for_game(token: str, game_id: str) -> list[dict]:
    """
    For all 20 group members, fetch their bets and return rows for the given game.
    """
    members = get_group_members(token)
    all_rows = []

    for member in members:
        uid = member.get("_id", {}).get("$oid", member.get("userId", ""))
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
