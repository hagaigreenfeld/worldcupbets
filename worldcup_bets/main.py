"""
main.py — orchestrates the full pipeline:
  1. Read game_id from env / argument
  2. Login to Sport5 and scrape bets
  3. Analyze results
  4. Write to Google Sheets

Called by GitHub Actions after each game kickoff.
"""

import os
import sys
import logging
import argparse

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


def main():
    parser = argparse.ArgumentParser(description="World Cup Bets Scraper")
    parser.add_argument(
        "--game-id",
        default=os.environ.get("GAME_ID", ""),
        help="Sport5 internal game ID (gid field)",
    )
    parser.add_argument(
        "--game-label",
        default=os.environ.get("GAME_LABEL", ""),
        help="Human-readable label, e.g. 'Argentina vs Norway (17/06)'",
    )
    parser.add_argument(
        "--mode",
        choices=["kickoff", "post-game"],
        default=os.environ.get("RUN_MODE", "post-game"),
        help="'kickoff' = pre-game bet cluster message; 'post-game' = full results + sheets",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results to stdout, skip writing to Sheets / WhatsApp",
    )
    args = parser.parse_args()

    game_id    = args.game_id
    game_label = args.game_label or game_id

    if not game_id:
        log.error("No --game-id provided and GAME_ID env var not set.")
        sys.exit(1)

    email    = os.environ["SPORT5_EMAIL"]
    password = os.environ["SPORT5_PASSWORD"]

    # ── Scrape ──────────────────────────────────────────────────────────────
    log.info("▶ Scraping bets for game: %s  [mode=%s]", game_label, args.mode)
    bets, leaderboard = scraper.run(game_id, email, password)
    log.info("  Scraped %d player bets", len(bets))

    # ── KICKOFF mode ────────────────────────────────────────────────────────
    if args.mode == "kickoff":
        if args.dry_run:
            log.info("DRY RUN — kickoff message preview only")
            print(whatsapp.format_kickoff_message(bets, game_label))
        else:
            log.info("▶ Writing kickoff bets to Google Sheets...")
            spreadsheet = sheets.get_sheet(os.environ["GOOGLE_SHEET_ID"])
            sheets.write_bets(spreadsheet, bets, game_label)
            log.info("▶ Sending kickoff WhatsApp message...")
            whatsapp.notify_kickoff(bets, game_label)
            log.info("✅ Kickoff done!")
        return

    # ── POST-GAME mode ──────────────────────────────────────────────────────
    log.info("▶ Analyzing results...")
    analysis = analyzer.analyze(bets, leaderboard)
    summary  = analysis["summary"]

    log.info("  Game:    %s", summary.get("game", "?"))
    log.info("  Result:  %s", summary.get("actual_result", "Pending"))
    log.info("  Exact:   %d  Correct: %d  Wrong: %d",
             summary.get("total_exact", 0),
             summary.get("total_correct", 0),
             summary.get("total_wrong", 0))
    log.info("  🏆 Top: %s (%s pts)", summary.get("top_earner"), summary.get("top_points"))

    print("\n" + "═" * 60)
    print(f"  ⚽  {summary.get('game', game_label)}")
    print(f"  📊  Result: {summary.get('actual_result', '⏳ Pending')}")
    print("═" * 60)
    for r in analysis["leaderboard"][:5]:
        print(f"  {r['rank']:2}. {r['name']:<22} {r['points']} pts  {r.get('rank_delta','')}")
    print("═" * 60 + "\n")

    if args.dry_run:
        log.info("DRY RUN — skipping Google Sheets write.")
        import json
        print(json.dumps(analysis, indent=2, ensure_ascii=False, default=str))
    else:
        log.info("▶ Writing to Google Sheets...")
        sheets.write_all(analysis, game_label)
        log.info("▶ Sending WhatsApp summary...")
        whatsapp.notify(analysis, game_label)
        log.info("✅ Done!")


if __name__ == "__main__":
    main()
