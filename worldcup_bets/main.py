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
import scheduler as sched

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
        choices=["kickoff", "post-game", "coming-up"],
        default=os.environ.get("RUN_MODE", "post-game"),
        help="'kickoff' = pre-game bet cluster message; 'post-game' = full results + sheets",
    )
    parser.add_argument(
        "--num-games",
        type=int,
        default=int(os.environ.get("NUM_GAMES", "0")) or None,
        help="For coming-up mode: number of upcoming games to show (default 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results to stdout, skip writing to Sheets / WhatsApp",
    )
    args = parser.parse_args()

    game_id    = args.game_id
    game_label = args.game_label or game_id

    if not game_id and args.mode != "coming-up":
        log.error("No --game-id provided and GAME_ID env var not set.")
        sys.exit(1)

    email    = os.environ["SPORT5_EMAIL"]
    password = os.environ["SPORT5_PASSWORD"]

    # ── COMING-UP mode ──────────────────────────────────────────────────────
    if args.mode == "coming-up":
        NEXT_N_GAMES = args.num_games or 1

        log.info("▶ Logging in to Sport5...")
        token = scraper.get_token(email, password)

        log.info("▶ Fetching upcoming games schedule...")
        all_upcoming  = scraper.get_upcoming_games_with_odds(token, n=999)
        next_few      = all_upcoming[:NEXT_N_GAMES]

        if not all_upcoming:
            log.info("No upcoming games found — nothing to do.")
            return

        spreadsheet = None
        leaderboard = []
        if not args.dry_run:
            spreadsheet = sheets.get_sheet(os.environ["GOOGLE_SHEET_ID"])
            leaderboard = sheets.read_leaderboard(spreadsheet)

        # Scrape all players' current bets for the next few games
        next_gids = [g["gid"] for g in next_few]
        log.info("▶ Scraping player bets for %d upcoming games...", len(next_few))
        bets_per_game = scraper.scrape_all_players_upcoming(token, next_gids)

        # Write full schedule to sheet
        if not args.dry_run:
            log.info("▶ Writing upcoming games to sheet (%d games)...", len(all_upcoming))
            sheets.write_upcoming_games(spreadsheet, all_upcoming)

        # Analyze next few games
        game_analyses = analyzer.coming_up_analysis(next_few, bets_per_game, leaderboard)

        if args.dry_run:
            print(whatsapp.format_coming_up_message(game_analyses))
        else:
            log.info("▶ Sending coming-up WhatsApp message...")
            whatsapp.notify_coming_up(game_analyses)
            log.info("✅ Coming-up done!")
        return

    # ── KICKOFF mode ────────────────────────────────────────────────────────
    if args.mode == "kickoff":
        bets = []

        # Try sheet first — bets written at game start are immutable
        if not args.dry_run:
            try:
                spreadsheet = sheets.get_sheet(os.environ["GOOGLE_SHEET_ID"])
                bets = sheets.read_bets_for_game(spreadsheet, game_label)
                # Validate: if all bets have no score guess, data is corrupt — re-scrape
                if bets and not any(b.get("score_guess") for b in bets):
                    log.warning("Sheet bets have no score data — cache corrupt, re-scraping")
                    bets = []
                elif bets:
                    log.info("▶ Read %d bets from sheet (no Sport5 call needed)", len(bets))
            except Exception as exc:
                log.warning("Could not read from sheet: %s — will scrape instead", exc)

        # Fall back to Sport5 scrape if sheet was empty
        if not bets:
            log.info("▶ Scraping bets from Sport5 for: %s", game_label)
            bets, _ = scraper.run(game_id, email, password)
            log.info("  Scraped %d player bets", len(bets))
            if not args.dry_run:
                log.info("▶ Writing kickoff bets to Google Sheets...")
                sheets.write_bets(spreadsheet, bets, game_label)

        bonus_bets = []
        if not args.dry_run:
            try:
                bonus_bets = sheets.read_bonus_bets(spreadsheet)
            except Exception as exc:
                log.warning("Could not load bonus bets: %s", exc)

        if args.dry_run:
            print(whatsapp.format_kickoff_message(bets, game_label))
        else:
            log.info("▶ Sending kickoff WhatsApp message...")
            whatsapp.notify_kickoff(bets, game_label, bonus_bets=bonus_bets)
            log.info("✅ Kickoff done!")
        return

    # ── POST-GAME: sheet-first, scrape only when needed ──────────────────────
    spreadsheet = sheets.get_sheet(os.environ["GOOGLE_SHEET_ID"]) if not args.dry_run else None
    bets        = []
    leaderboard = []

    if spreadsheet:
        bets        = sheets.read_bets_for_game(spreadsheet, game_label)
        leaderboard = sheets.read_leaderboard(spreadsheet)

    # Determine if we need a fresh scrape:
    # - No bets in sheet yet, OR
    # - Bets exist but have no actual_result (game just ended, points not yet updated)
    need_scrape = not bets or not any(b.get("actual_result") for b in bets)

    if need_scrape:
        log.info("▶ Scraping bets from Sport5 for: %s  [mode=%s]", game_label, args.mode)
        bets, leaderboard = scraper.run(game_id, email, password)
        log.info("  Scraped %d player bets", len(bets))
    else:
        log.info("▶ Read %d bets from sheet (no Sport5 call needed)", len(bets))

    # ── POST-GAME mode ──────────────────────────────────────────────────────
    log.info("▶ Analyzing results...")
    analysis = analyzer.analyze(bets, leaderboard)
    summary = analysis["summary"]

    # Check live match status from football-data.org
    # GAME_STATUS env var overrides (set by scheduler or manual trigger).
    # football-data.org uses English team names; Sport5 uses Hebrew — fuzzy match
    # rarely succeeds here, so treat unknown status as FINISHED in post-game mode
    # (manual post-game is only triggered after the game is done).
    fd_status = os.environ.get("GAME_STATUS", "")
    if not fd_status:
        team1 = summary.get("team1", "")
        team2 = summary.get("team2", "")
        fd_status = sched.check_game_status(
            team1, team2, api_key=os.environ.get("FOOTBALL_DATA_API_KEY")
        )
    log.info("Match status: %s", fd_status or "unknown — defaulting to FINISHED for post-game")
    summary["is_final"] = (fd_status == "FINISHED") if fd_status else True

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

    # What-if — always show (title changes based on is_final)
    what_if = None
    if summary.get("actual_result") not in ("", "Not yet played"):
        what_if = analyzer.what_if_analysis(analysis["enriched_bets"], summary["actual_result"])

    # Position movers vs previous game leaderboard
    position_movers = None
    if spreadsheet:
        try:
            prev_board = sheets.read_previous_leaderboard_snapshot(spreadsheet, game_label)
            if prev_board:
                position_movers = analyzer.leaderboard_position_changes(
                    analysis["leaderboard"], prev_board
                )
        except Exception as exc:
            log.warning("Could not load previous leaderboard snapshot: %s", exc)

    if args.dry_run:
        log.info("DRY RUN — skipping Google Sheets write.")
        import json
        print(json.dumps(analysis, indent=2, ensure_ascii=False, default=str))
    else:
        bonus_bets = []
        try:
            bonus_bets = sheets.read_bonus_bets(spreadsheet)
        except Exception as exc:
            log.warning("Could not load bonus bets: %s", exc)
        if need_scrape:
            log.info("▶ Writing results to Google Sheets...")
            sheets.write_all(analysis, game_label, spreadsheet=spreadsheet)
        else:
            log.info("▶ Sheet already up to date — skipping write")
        log.info("▶ Sending WhatsApp summary...")
        whatsapp.notify(analysis, game_label, what_if=what_if, position_movers=position_movers, bonus_bets=bonus_bets)
        log.info("✅ Done!")


if __name__ == "__main__":
    main()
