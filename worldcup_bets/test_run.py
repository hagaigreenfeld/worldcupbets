"""
Local test runner — no WhatsApp, no Google Sheets, no GitHub Actions.
Just scrapes bets and prints the formatted messages.

Usage:
  python3 test_run.py kickoff [game_id]
  python3 test_run.py post-game [game_id]

If game_id is omitted, uses the hardcoded DEFAULT_GAME_ID below.
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


def write_sheets(fn, *args):
    """Call a sheets function only when credentials are available."""
    if not SHEET_ID or not os.environ.get("GOOGLE_SHEETS_KEY_JSON"):
        print("  (no sheet credentials — skipping Sheets write)")
        return
    fn(*args)


def main():
    mode    = sys.argv[1] if len(sys.argv) > 1 else "kickoff"
    game_id = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_GAME_ID

    print(f"Scraping bets for {game_id}...")
    bets, leaderboard = scraper.run(game_id, EMAIL, PASSWORD)
    print(f"Got {len(bets)} bets\n")

    if mode == "kickoff":
        bets = []

        # Try sheet first
        if SHEET_ID and os.environ.get("GOOGLE_SHEETS_KEY_JSON"):
            try:
                sp   = sheets.get_sheet(SHEET_ID)
                bets = sheets.read_bets_for_game(sp, DEFAULT_GAME_LABEL)
                if bets:
                    print(f"Read {len(bets)} bets from sheet (cached)\n")
            except Exception as e:
                print(f"Sheet read failed ({e}), scraping instead...\n")

        # Fall back to Sport5 scrape
        if not bets:
            print(f"Scraping bets for {game_id}...")
            bets, _ = scraper.run(game_id, EMAIL, PASSWORD)
            print(f"Got {len(bets)} bets\n")
            write_sheets(lambda: sheets.write_bets(sheets.get_sheet(SHEET_ID), bets, DEFAULT_GAME_LABEL))

        label = next((f"{b['team1']} vs {b['team2']}" for b in bets if b.get("team1")), DEFAULT_GAME_LABEL)
        print(whatsapp.format_kickoff_message(bets, label))

    elif mode == "post-game":
        print(f"Scraping bets for {game_id}...")
        bets, leaderboard = scraper.run(game_id, EMAIL, PASSWORD)
        print(f"Got {len(bets)} bets\n")
        label    = next((f"{b['team1']} vs {b['team2']}" for b in bets if b.get("team1")), DEFAULT_GAME_LABEL)
        analysis = analyzer.analyze(bets, leaderboard)
        write_sheets(lambda: sheets.write_all(analysis, label))
        print(whatsapp.format_game_summary(analysis, label))

    else:
        print(f"Unknown mode: {mode}. Use kickoff or post-game.")
        sys.exit(1)

if __name__ == "__main__":
    main()
