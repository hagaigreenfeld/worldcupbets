"""
Local test runner — no WhatsApp, no Google Sheets, no GitHub Actions.
Just scrapes bets and prints the formatted messages.

Usage:
  python3 test_run.py kickoff [game_id]
  python3 test_run.py post-game [game_id]
  python3 test_run.py midgame [game_id] [score]   # simulate mid-game with what-if + position movers

If game_id is omitted, uses the hardcoded DEFAULT_GAME_ID below.
For midgame, score defaults to DEFAULT_MIDGAME_SCORE.
"""

import os, sys, logging
import scraper, whatsapp, analyzer, sheets

logging.basicConfig(level=logging.WARNING)  # suppress INFO spam

EMAIL    = os.environ.get("SPORT5_EMAIL",    "hagaigreenfeld@gmail.com")
PASSWORD = os.environ.get("SPORT5_PASSWORD", "Worldcuphagai12++")
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")

# Last known game — update this as needed
DEFAULT_GAME_ID    = "156a1e8e4a24d15"
DEFAULT_GAME_LABEL = "ניו זילנד vs מצרים (מחזור 2)"

# Simulated live score for midgame mode
DEFAULT_MIDGAME_SCORE = "1:0"


def get_sheet():
    if not SHEET_ID or not os.environ.get("GOOGLE_SHEETS_KEY_JSON"):
        return None
    return sheets.get_sheet(SHEET_ID)


def main():
    mode    = sys.argv[1] if len(sys.argv) > 1 else "kickoff"
    game_id = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_GAME_ID

    if mode == "kickoff":
        bets = []

        sp = get_sheet()
        if sp:
            try:
                bets = sheets.read_bets_for_game(sp, DEFAULT_GAME_LABEL)
                if bets:
                    print(f"Read {len(bets)} bets from sheet (cached)\n")
            except Exception as e:
                print(f"Sheet read failed ({e}), scraping instead...\n")

        if not bets:
            print(f"Scraping bets for {game_id}...")
            bets, _ = scraper.run(game_id, EMAIL, PASSWORD)
            print(f"Got {len(bets)} bets\n")
            if sp:
                sheets.write_bets(sp, bets, DEFAULT_GAME_LABEL)

        label = next((f"{b['team1']} vs {b['team2']}" for b in bets if b.get("team1")), DEFAULT_GAME_LABEL)
        print(whatsapp.format_kickoff_message(bets, label))

    elif mode == "post-game":
        print(f"Scraping bets for {game_id}...")
        bets, leaderboard = scraper.run(game_id, EMAIL, PASSWORD)
        print(f"Got {len(bets)} bets\n")
        label    = next((f"{b['team1']} vs {b['team2']}" for b in bets if b.get("team1")), DEFAULT_GAME_LABEL)
        analysis = analyzer.analyze(bets, leaderboard)
        sp = get_sheet()
        if sp:
            sheets.write_all(analysis, label)
        else:
            print("  (no sheet credentials — skipping Sheets write)")
        print(whatsapp.format_game_summary(analysis, label))

    elif mode == "midgame":
        # Simulate תוצאות as if the game is still in progress
        live_score = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_MIDGAME_SCORE
        print(f"Scraping bets for {game_id} (simulated live score: {live_score})...")
        bets, leaderboard = scraper.run(game_id, EMAIL, PASSWORD)
        print(f"Got {len(bets)} bets\n")

        # Inject the simulated score into each bet so the analyzer sees it
        for b in bets:
            b["actual_result"] = live_score
            # Recalculate points_won based on simulated score
            parsed = live_score.split(":")
            if len(parsed) == 2:
                g1, g2 = int(parsed[0]), int(parsed[1])
                winner = "team1" if g1 > g2 else ("team2" if g2 > g1 else "draw")
                if b.get("score_guess") == live_score:
                    b["points_won"] = b.get("potential_points", 0)
                elif b.get("guess_winner") == winner:
                    b["points_won"] = b.get("potential_points", 0) * 0.5  # rough approximation
                else:
                    b["points_won"] = 0

        label    = next((f"{b['team1']} vs {b['team2']}" for b in bets if b.get("team1")), DEFAULT_GAME_LABEL)
        analysis = analyzer.analyze(bets, leaderboard)
        analysis["summary"]["is_final"] = False  # mark as in-progress

        # What-if
        what_if = analyzer.what_if_analysis(analysis["enriched_bets"], live_score)

        # Position movers from sheet
        position_movers = None
        sp = get_sheet()
        if sp:
            try:
                prev_board = sheets.read_previous_leaderboard_snapshot(sp, label)
                if prev_board:
                    position_movers = analyzer.leaderboard_position_changes(
                        analysis["leaderboard"], prev_board
                    )
                    print(f"Loaded previous leaderboard ({len(prev_board)} players) for comparison\n")
                else:
                    print("No previous leaderboard snapshot found — position movers will be skipped\n")
            except Exception as e:
                print(f"Could not load previous snapshot: {e}\n")

        print("=" * 60)
        print(whatsapp.format_game_summary(
            analysis, label, what_if=what_if, position_movers=position_movers
        ))

    else:
        print(f"Unknown mode: {mode}. Use kickoff, post-game, or midgame.")
        sys.exit(1)

if __name__ == "__main__":
    main()
