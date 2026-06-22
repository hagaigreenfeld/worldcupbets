"""
schedule.py — fetch and manage the World Cup 2026 game schedule.

Uses the football-data.org free API (competition id 2000 = FIFA World Cup).
Also cross-references against the Sport5 info.json to get their internal game IDs.

Free tier: 10 req/min, no auth header required for basic data.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
SPORT5_INFO_URL    = "https://hevre.sport5.co.il/data/info.json"
WC2026_COMPETITION = 2000   # FIFA World Cup on football-data.org


def get_todays_matches(api_key: Optional[str] = None) -> list[dict]:
    """
    Return today's World Cup matches from football-data.org.
    Each item: { id, homeTeam, awayTeam, utcDate, status, score }
    """
    headers = {}
    if api_key:
        headers["X-Auth-Token"] = api_key

    url = f"{FOOTBALL_DATA_BASE}/competitions/{WC2026_COMPETITION}/matches"
    params = {"status": "SCHEDULED,LIVE,IN_PLAY,PAUSED,FINISHED"}

    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    matches = resp.json().get("matches", [])

    # Filter to today (UTC)
    today = datetime.now(timezone.utc).date()
    todays = []
    for m in matches:
        match_dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        if match_dt.date() == today:
            todays.append({
                "fd_id":     m["id"],
                "home_team": m["homeTeam"]["name"],
                "away_team": m["awayTeam"]["name"],
                "kickoff":   m["utcDate"],
                "status":    m["status"],
                "score":     m.get("score", {}),
            })

    log.info("Today's WC matches: %d", len(todays))
    return todays


def get_sport5_game_ids() -> dict:
    """
    Fetch Sport5's info.json and extract all game IDs.
    Returns dict: { "TeamA_TeamB": "sport5_game_id", ... }
    """
    resp = requests.get(SPORT5_INFO_URL, timeout=10)
    resp.raise_for_status()
    info = resp.json()
    # The guesses data is on the user object — we use the schedule from
    # the member's guesses which we already fetched in scraper.py
    # This function is a stub; game_id comes from the GitHub Actions input
    return info


def get_next_game_info() -> Optional[dict]:
    """
    Returns the next unplayed game today (or None).
    Useful for cron jobs that run just before kickoff.
    """
    try:
        matches = get_todays_matches(os.environ.get("FOOTBALL_DATA_API_KEY"))
        upcoming = [m for m in matches if m["status"] in ("SCHEDULED", "TIMED")]
        if not upcoming:
            return None
        return upcoming[0]
    except Exception as exc:
        log.warning("Could not fetch schedule from football-data.org: %s", exc)
        return None


if __name__ == "__main__":
    import json
    games = get_todays_matches()
    print(json.dumps(games, indent=2, ensure_ascii=False))
