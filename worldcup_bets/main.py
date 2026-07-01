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

        # Scrape all players' bets for ALL upcoming games in one pass
        # (each player fetched once; all game bets extracted from that single call)
        all_gids  = [g["gid"] for g in all_upcoming]
        next_gids = [g["gid"] for g in next_few]
        log.info("▶ Scraping player bets for all %d upcoming games...", len(all_upcoming))
        bets_per_game = scraper.scrape_all_players_upcoming(token, all_gids)

        # Write full schedule + all bets to sheet
        if not args.dry_run:
            log.info("▶ Writing upcoming games to sheet (%d games)...", len(all_upcoming))
            sheets.write_upcoming_games(spreadsheet, all_upcoming)
            log.info("▶ Writing all upcoming player bets to sheet...")
            sheets.write_upcoming_bets(spreadsheet, all_upcoming, bets_per_game)

        # Analyze only the next few games for WhatsApp message
        next_bets     = {gid: bets_per_game.get(gid, []) for gid in next_gids}
        game_analyses = analyzer.coming_up_analysis(next_few, next_bets, leaderboard)

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

        # Try sheet first — bets written at game start are preferred
        spreadsheet = None
        if not args.dry_run:
            try:
                spreadsheet = sheets.get_sheet(os.environ["GOOGLE_SHEET_ID"])
                bets = sheets.read_bets_for_game(spreadsheet, game_label)
                if bets and not any(b.get("score_guess") for b in bets):
                    log.warning("Sheet bets have no score data — cache corrupt, re-scraping")
                    bets = []
                elif bets:
                    pot_nonzero = sum(1 for b in bets if b.get("potential_points"))
                    if pot_nonzero < len(bets) * 0.8:
                        log.warning(
                            "Sheet bets: only %d/%d have potential_points — re-scraping to fix",
                            pot_nonzero, len(bets),
                        )
                        bets = []
                elif bets:
                    log.info("▶ Read %d bets from sheet (no Sport5 call needed)", len(bets))
            except Exception as exc:
                log.warning("Could not read from sheet: %s — will scrape instead", exc)

        # Fall back to Sport5 scrape if sheet was empty or invalid
        if not bets:
            log.info("▶ Scraping bets from Sport5 for: %s", game_label)
            # force_potential=True: kickoff always shows ratio-based potential,
            # not mid-game gamepoints (which are 0 during live evaluation).
            bets, _ = scraper.run(game_id, email, password, force_potential=True)
            log.info("  Scraped %d player bets", len(bets))
            if not args.dry_run and spreadsheet:
                log.info("▶ Writing/updating kickoff bets in Google Sheets...")
                sheets.write_bets(spreadsheet, bets, game_label)
                # Always patch potential_points (handles case where rows existed but pot was 0)
                sheets.update_bets_potential_points(spreadsheet, bets, game_label)

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

    # Initial scrape only if the sheet has nothing usable.
    need_scrape = not bets or not any(b.get("actual_result") for b in bets)
    scraped = False
    if need_scrape:
        log.info("▶ Scraping bets from Sport5 for: %s  [mode=%s]", game_label, args.mode)
        bets, leaderboard = scraper.run(game_id, email, password)
        scraped = True
        log.info("  Scraped %d player bets", len(bets))
    else:
        log.info("▶ Read %d bets from sheet", len(bets))

    # ── Determine match status (football-data → time-based fallback) ─────────
    _sample = next((b for b in bets if b.get("team1")), bets[0] if bets else {})
    team1 = _sample.get("team1", "")
    team2 = _sample.get("team2", "")
    fd_status = os.environ.get("GAME_STATUS", "")
    live = sched.get_live_match(team1, team2, api_key=os.environ.get("FOOTBALL_DATA_API_KEY"))
    if not fd_status and live:
        fd_status = live.get("status", "")

    if fd_status:
        is_final = (fd_status == "FINISHED")
        log.info("Match status (football-data): %s → is_final=%s", fd_status, is_final)
    else:
        import time as _time
        FINISH_AFTER_MS = int(2.5 * 60 * 60 * 1000)  # 2.5h covers ET + penalties
        kickoff_ts = next((float(b["kickoff_ts"]) for b in bets if b.get("kickoff_ts")), 0)
        if kickoff_ts:
            is_final = (_time.time() * 1000 - kickoff_ts) >= FINISH_AFTER_MS
            log.info("Match status (time-based): kickoff %.0f min ago → is_final=%s",
                     (_time.time() * 1000 - kickoff_ts) / 60000, is_final)
        else:
            is_final = True
            log.info("Match status: no kickoff_ts — defaulting to FINISHED")

    is_live = fd_status in ("IN_PLAY", "PAUSED")

    # A FINISHED game: Sport5's result1/result2 is the authoritative 90-min
    # score (what bets are graded on). If we used the sheet, it may hold a
    # mid-game snapshot (e.g. 0:0) — re-scrape to get the real final result.
    if is_final and not scraped:
        log.info("▶ Game finished — re-scraping Sport5 for the final 90-min result")
        bets, leaderboard = scraper.run(game_id, email, password)
        scraped = True
        log.info("  Scraped %d player bets", len(bets))

    # For LIVE games only, patch the score with football-data (Sport5 lags
    # during play). For FINISHED games we keep Sport5's 90-min result rather
    # than football-data's fullTime, which includes extra time + penalties.
    if live and live.get("score") and is_live:
        old_result = next((b.get("actual_result") for b in bets if b.get("actual_result")), "")
        if live["score"] != old_result:
            log.info("Overriding live result %s → %s (football-data)", old_result or "—", live["score"])
        for b in bets:
            b["actual_result"] = live["score"]

    # ── POST-GAME mode ──────────────────────────────────────────────────────
    log.info("▶ Analyzing results...")
    analysis = analyzer.analyze(bets, leaderboard)
    summary = analysis["summary"]
    summary["is_final"] = is_final

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
        if scraped:
            log.info("▶ Writing results to Google Sheets...")
            sheets.write_all(analysis, game_label, spreadsheet=spreadsheet)
        else:
            log.info("▶ Sheet already up to date — skipping write")
        log.info("▶ Sending WhatsApp summary...")
        whatsapp.notify(analysis, game_label, what_if=what_if, position_movers=position_movers, bonus_bets=bonus_bets)
        log.info("✅ Done!")


if __name__ == "__main__":
    main()
