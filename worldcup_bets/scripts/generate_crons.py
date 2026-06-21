"""
scripts/generate_crons.py
─────────────────────────
Fetches the full World Cup 2026 schedule from football-data.org
and prints ready-to-paste cron lines for the GitHub Actions workflow.

Usage:
  FOOTBALL_DATA_API_KEY=your_key python3 scripts/generate_crons.py

Output:
  - cron: '1 0 12 6 *'   # Argentina vs Norway  [Sat 12 Jun 00:01 UTC]
  - cron: '1 3 12 6 *'   # France vs Brazil      [Sat 12 Jun 03:01 UTC]
  ...
"""

import os
import sys
import requests
from datetime import datetime, timezone

BASE    = "https://api.football-data.org/v4"
COMP_ID = 2000


def main():
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY")
    headers = {}
    if api_key:
        headers["X-Auth-Token"] = api_key

    resp = requests.get(
        f"{BASE}/competitions/{COMP_ID}/matches",
        headers=headers,
        params={"status": "SCHEDULED,TIMED"},
        timeout=20,
    )

    if resp.status_code == 403:
        print("ERROR: football-data.org returned 403. Get a free key at https://www.football-data.org/client/register")
        sys.exit(1)

    resp.raise_for_status()
    matches = resp.json().get("matches", [])

    print(f"# World Cup 2026 — {len(matches)} scheduled games")
    print("# Paste these into .github/workflows/worldcup.yml under 'schedule:'")
    print()

    for m in matches:
        dt   = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        home = m["homeTeam"]["name"]
        away = m["awayTeam"]["name"]

        # Cron fires 1 minute after kickoff
        minute = dt.minute + 1
        hour   = dt.hour
        if minute >= 60:
            minute -= 60
            hour   += 1

        label = f"{home} vs {away}  [{dt.strftime('%a %d %b %H:%M UTC')}]"
        cron  = f"    - cron: '{minute:02d} {hour:02d} {dt.day:02d} {dt.month:02d} *'   # {label}"
        print(cron)


if __name__ == "__main__":
    main()
