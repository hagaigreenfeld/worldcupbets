"""
scripts/backfill_all_bets.py
─────────────────────────────
Scrape all finished games from Sport5 and backfill the "All Bets" spreadsheet tab.

Usage:
  cd worldcup_bets
  python scripts/backfill_all_bets.py [--dry-run]

Requires env vars: SPORT5_EMAIL, SPORT5_PASSWORD, GOOGLE_SHEETS_KEY_JSON, GOOGLE_SHEET_ID

Logic:
  1. Login to Sport5 and fetch the first member's full guesses (all rounds/games)
  2. Identify finished games (result1/result2 present and non-empty)
  3. For each finished game not already in "All Bets", scrape all 20 players and write
  4. Game label format: "{team1} vs {team2} ({round_name})"
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timezone

# Allow running from the scripts/ subdirectory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scraper
import sheets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def get_all_sport5_games_with_results(token: str) -> list[dict]:
    """
    Fetch the first group member's guesses to get a full catalogue of games.
    Returns list of dicts for games that have actual results (finished).
    Each dict: { gid, team1, team2, round_name, result1, result2 }
    """
    group   = scraper.api_post("getGroup", token, membersGroup=scraper.GROUP_ID)
    members = group.get("members", [])
    if not members:
        raise RuntimeError("No group members found")

    first_uid = members[0].get("_id", "")
    log.info("Fetching full game catalogue via member %s (%s)", members[0].get("name"), first_uid)
    rounds = scraper.get_friend_guesses(token, first_uid)

    games = []
    for round_ in rounds:
        round_name = round_.get("name", "")
        for g in round_.get("games", []):
            gid     = g.get("gid", "")
            team1   = (g.get("team1") or {}).get("name", "")
            team2   = (g.get("team2") or {}).get("name", "")
            result1 = g.get("result1")
            result2 = g.get("result2")

            if not gid or not team1 or not team2:
                continue

            # Game is finished if actual scores are present
            has_result = (result1 is not None and result2 is not None
                          and str(result1) != "" and str(result2) != "")
            games.append({
                "gid":        gid,
                "team1":      team1,
                "team2":      team2,
                "round_name": round_name,
                "result1":    result1,
                "result2":    result2,
                "finished":   has_result,
            })

    log.info("Found %d total games, %d finished", len(games), sum(1 for g in games if g["finished"]))
    return games


def get_existing_game_labels(spreadsheet) -> set[str]:
    """Return set of game labels already in the All Bets tab."""
    try:
        ws   = sheets.ensure_tab(spreadsheet, "All Bets")
        rows = ws.get_all_values()
    except Exception as exc:
        log.warning("Could not read All Bets tab: %s", exc)
        return set()

    if len(rows) < 2:
        return set()

    labels = set()
    for row in rows[1:]:
        if row and row[0]:
            labels.add(row[0])
    return labels


def game_label_for(game: dict) -> str:
    label = f"{game['team1']} vs {game['team2']}"
    if game.get("round_name"):
        label += f" ({game['round_name']})"
    return label


def label_matches_teams(label: str, team1: str, team2: str) -> bool:
    """Check if a label contains both team names (handles format differences)."""
    return team1 in label and team2 in label


def main():
    parser = argparse.ArgumentParser(description="Backfill All Bets tab with historical game data")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, don't write")
    args = parser.parse_args()

    email    = os.environ["SPORT5_EMAIL"]
    password = os.environ["SPORT5_PASSWORD"]

    log.info("Logging in to Sport5...")
    token = scraper.get_token(email, password)

    log.info("Fetching game catalogue...")
    all_games = get_all_sport5_games_with_results(token)
    finished  = [g for g in all_games if g["finished"]]
    log.info("%d finished games to potentially backfill", len(finished))

    if args.dry_run:
        log.info("DRY RUN — connecting to sheet to check existing labels (no writes)")
        spreadsheet = sheets.get_sheet(os.environ["GOOGLE_SHEET_ID"])
        existing_labels = get_existing_game_labels(spreadsheet)
    else:
        spreadsheet     = sheets.get_sheet(os.environ["GOOGLE_SHEET_ID"])
        existing_labels = get_existing_game_labels(spreadsheet)

    log.info("Existing game labels in sheet: %d", len(existing_labels))
    for lbl in sorted(existing_labels):
        log.info("  ✓ %s", lbl)

    to_backfill = []
    for game in finished:
        label = game_label_for(game)
        # Check exact match first, then team-name match (handles different label formats)
        if label in existing_labels:
            log.info("SKIP (exact match): %s", label)
            continue
        # Check if any existing label covers the same two teams
        team_covered = any(
            label_matches_teams(existing, game["team1"], game["team2"])
            for existing in existing_labels
        )
        if team_covered:
            log.info("SKIP (teams already in sheet): %s", label)
            continue
        to_backfill.append(game)

    log.info("\n%d games to backfill:", len(to_backfill))
    for g in to_backfill:
        log.info("  → %s  (gid=%s, result=%s:%s)", game_label_for(g), g["gid"], g["result1"], g["result2"])

    if args.dry_run:
        log.info("\nDRY RUN — no writes performed. Re-run without --dry-run to backfill.")
        return

    if not to_backfill:
        log.info("Nothing to backfill. Sheet is up to date.")
        return

    log.info("\nStarting backfill of %d games...", len(to_backfill))
    for idx, game in enumerate(to_backfill, 1):
        label = game_label_for(game)
        log.info("[%d/%d] Scraping: %s (gid=%s)", idx, len(to_backfill), label, game["gid"])
        try:
            bets = scraper.scrape_all_bets_for_game(token, game["gid"])
            log.info("  Scraped %d player bets", len(bets))
            sheets.write_bets(spreadsheet, bets, label)
            log.info("  ✅ Written to All Bets")
        except Exception as exc:
            log.error("  ❌ Failed for %s: %s", label, exc)

        # Polite delay between games
        if idx < len(to_backfill):
            time.sleep(1.0)

    log.info("\nBackfill complete.")


if __name__ == "__main__":
    main()
