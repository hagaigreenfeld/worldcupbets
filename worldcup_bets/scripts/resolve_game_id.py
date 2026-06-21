"""
scripts/resolve_game_id.py
──────────────────────────
Run by GitHub Actions on scheduled triggers to figure out WHICH game
is starting right now and output the sport5 game_id + label
into $GITHUB_OUTPUT so the main workflow step can use them.

Strategy:
  1. Fetch today's fixtures from football-data.org
  2. Find the one closest to current UTC time (within ±10 minutes)
  3. Match team names to Sport5's internal IDs via the member guesses API
     (we fetch one member's guesses which contains all game metadata)

Outputs (written to $GITHUB_OUTPUT):
  game_id=<sport5_gid>
  game_label=<human_label>
"""

import os
import sys
import json
import logging
import requests
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
SPORT5_BASE        = "https://hevre.sport5.co.il/server/data.php"
GROUP_ID           = "6a202c81f6f70af684071fd4"
WC_COMPETITION     = 2000


def post_sport5(type_: str, token: str, **kwargs):
    resp = requests.post(
        SPORT5_BASE,
        params={"type": type_},
        data={"token": token, **kwargs},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def login() -> str:
    email    = os.environ["SPORT5_EMAIL"]
    password = os.environ["SPORT5_PASSWORD"]
    resp = requests.post(
        SPORT5_BASE,
        params={"type": "appUserLogin"},
        data={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "token" not in data:
        raise RuntimeError(f"Login failed: {data}")
    return data["token"]


def get_sport5_games(token: str) -> list[dict]:
    """
    Fetch all games Sport5 knows about by pulling the first group member's guesses.
    Returns flat list of game dicts, each with gid, team1.name, team2.name, kickoff.
    """
    # Get first member
    group = post_sport5("getGroup", token, groupId=GROUP_ID)
    members = group.get("members", [])
    if not members:
        raise RuntimeError("No group members found")

    first_uid = members[0].get("_id", {}).get("$oid", members[0].get("userId", ""))
    guesses   = post_sport5("getFriendGuesses", token, friendId=first_uid, groupId=GROUP_ID)

    games = []
    for round_ in guesses.get("guesses", []):
        for g in round_.get("games", []):
            games.append({
                "gid":        g.get("gid", ""),
                "team1":      g.get("team1", {}).get("name", ""),
                "team2":      g.get("team2", {}).get("name", ""),
                "kickoff":    g.get("kickoff", "") or g.get("startTime", ""),
                "round_name": round_.get("name", ""),
            })
    return games


def get_fd_todays_matches(api_key: str | None) -> list[dict]:
    headers = {}
    if api_key:
        headers["X-Auth-Token"] = api_key

    url    = f"{FOOTBALL_DATA_BASE}/competitions/{WC_COMPETITION}/matches"
    params = {"status": "SCHEDULED,LIVE"}
    resp   = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()

    now   = datetime.now(timezone.utc)
    today = now.date()
    out   = []
    for m in resp.json().get("matches", []):
        dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        if dt.date() == today:
            out.append({
                "home": m["homeTeam"]["name"],
                "away": m["awayTeam"]["name"],
                "utc":  dt,
            })
    return out


def fuzzy_match(name_a: str, name_b: str) -> bool:
    """Very simple partial match (handles name variants like 'Argentina' vs 'Argentina NT')."""
    a = name_a.lower().strip()
    b = name_b.lower().strip()
    return a in b or b in a or a[:6] == b[:6]


def find_current_game(sport5_games: list[dict], fd_matches: list[dict]) -> dict | None:
    now = datetime.now(timezone.utc)

    for fd in fd_matches:
        diff = abs((fd["utc"] - now).total_seconds())
        if diff > 600:   # only games within 10 min of current time
            continue
        for sg in sport5_games:
            if (fuzzy_match(sg["team1"], fd["home"]) or fuzzy_match(sg["team1"], fd["away"])) and \
               (fuzzy_match(sg["team2"], fd["home"]) or fuzzy_match(sg["team2"], fd["away"])):
                return sg

    # Fallback: return the Sport5 game whose kickoff timestamp is nearest to now
    if sport5_games:
        def dist(g):
            k = g.get("kickoff", "")
            if not k:
                return 999999
            try:
                dt = datetime.fromisoformat(str(k).replace("Z", "+00:00")) if "T" in str(k) \
                     else datetime.fromtimestamp(int(k)/1000, tz=timezone.utc)
                return abs((dt - now).total_seconds())
            except Exception:
                return 999999

        nearest = min(sport5_games, key=dist)
        if dist(nearest) < 3600:   # within 1 hour
            return nearest

    return None


def write_output(key: str, value: str):
    """Write to $GITHUB_OUTPUT if running in Actions, else print."""
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"{key}={value}\n")
    else:
        print(f"{key}={value}")


def main():
    token = login()
    log.info("Logged in. Fetching Sport5 game list...")
    sport5_games = get_sport5_games(token)
    log.info("  Found %d Sport5 games", len(sport5_games))

    api_key = os.environ.get("FOOTBALL_DATA_API_KEY")
    try:
        fd_matches = get_fd_todays_matches(api_key)
        log.info("  Football-data.org today: %d matches", len(fd_matches))
    except Exception as exc:
        log.warning("Could not fetch football-data.org: %s", exc)
        fd_matches = []

    game = find_current_game(sport5_games, fd_matches)

    if not game:
        log.error("Could not resolve a game ID for current time. Exiting.")
        sys.exit(1)

    game_id    = game["gid"]
    game_label = f"{game['team1']} vs {game['team2']}"
    if game.get("round_name"):
        game_label += f" ({game['round_name']})"

    log.info("Resolved game: %s  (id=%s)", game_label, game_id)
    write_output("game_id",    game_id)
    write_output("game_label", game_label)


if __name__ == "__main__":
    main()
