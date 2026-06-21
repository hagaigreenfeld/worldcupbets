"""
sheets.py — write bets and leaderboard to Google Sheets.

Sheet structure (tabs):
  1. "Leaderboard"  — live rankings, updated after every game
  2. "All Bets"     — raw bets per game, one row per player per game
  3. "Game Summary" — one row per game with aggregate analysis

Setup:
  - Create a Google Service Account and download the JSON key
  - Share your Google Sheet with the service account email
  - Set env var GOOGLE_SHEETS_KEY_JSON to the raw JSON content of the key
  - Set env var GOOGLE_SHEET_ID to your sheet's ID (from its URL)
"""

import os
import json
import logging
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheet(sheet_id: str) -> gspread.Spreadsheet:
    """Authenticate and return the Spreadsheet object."""
    key_json = os.environ["GOOGLE_SHEETS_KEY_JSON"]
    key_data = json.loads(key_json)
    creds    = Credentials.from_service_account_info(key_data, scopes=SCOPES)
    client   = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


def ensure_tab(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    """Return the worksheet named `title`, creating it if needed."""
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=500, cols=30)
        log.info("Created tab: %s", title)
        return ws


# ── Individual writers ─────────────────────────────────────────────────────────

def write_leaderboard(spreadsheet: gspread.Spreadsheet, leaderboard: list[dict]) -> None:
    ws = ensure_tab(spreadsheet, "Leaderboard")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    headers = ["Rank", "Player", "Points", "Δ Points", "Δ Rank", "Last Updated"]
    rows    = [headers]
    for r in leaderboard:
        rows.append([
            r.get("rank", ""),
            r.get("name", ""),
            r.get("points", ""),
            r.get("points_delta", ""),
            r.get("rank_delta", ""),
            now,
        ])

    ws.clear()
    ws.update(rows, value_input_option="USER_ENTERED")
    log.info("Leaderboard tab updated (%d players)", len(leaderboard))


def write_bets(
    spreadsheet: gspread.Spreadsheet,
    bets: list[dict],
    game_label: str,
) -> None:
    ws = ensure_tab(spreadsheet, "All Bets")

    # Read existing data to append (or write headers if empty)
    existing = ws.get_all_values()
    headers = [
        "Game", "Round", "Player", "Team 1", "Team 2",
        "Guessed Winner", "Score Guess",
        "Actual Result", "Status", "Points Won", "Potential Points",
        "Scraped At",
    ]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_rows = []

    if not existing:
        new_rows.append(headers)

    for b in bets:
        new_rows.append([
            game_label,
            b.get("round_name", ""),
            b.get("player_name", ""),
            b.get("team1", ""),
            b.get("team2", ""),
            b.get("guessed_team_name", b.get("guess_winner", "")),
            b.get("score_guess", ""),
            b.get("actual_result", ""),
            b.get("result_status", ""),
            b.get("points_won", ""),
            b.get("potential_points", ""),
            now,
        ])

    if existing:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
    else:
        ws.update(new_rows, value_input_option="USER_ENTERED")

    log.info("All Bets tab: appended %d rows for %s", len(bets), game_label)


def write_game_summary(
    spreadsheet: gspread.Spreadsheet,
    summary: dict,
    game_label: str,
) -> None:
    ws = ensure_tab(spreadsheet, "Game Summary")
    existing = ws.get_all_values()

    headers = [
        "Game Label", "Teams", "Actual Result",
        "Picked Team 1", "Picked Team 2", "Picked Draw", "No Bet",
        "Exact Scores", "Correct Direction", "Wrong",
        "Top Earner", "Top Points", "Bottom Earner", "Bottom Points",
        "Scraped At",
    ]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    row = [
        game_label,
        summary.get("game", ""),
        summary.get("actual_result", ""),
        ", ".join(summary.get("picked_team1", [])),
        ", ".join(summary.get("picked_team2", [])),
        ", ".join(summary.get("picked_draw", [])),
        ", ".join(summary.get("no_bet", [])),
        summary.get("total_exact", 0),
        summary.get("total_correct", 0),
        summary.get("total_wrong", 0),
        summary.get("top_earner", ""),
        summary.get("top_points", 0),
        summary.get("bottom_earner", ""),
        summary.get("bottom_points", 0),
        now,
    ]

    if not existing:
        ws.update([headers, row], value_input_option="USER_ENTERED")
    else:
        ws.append_rows([row], value_input_option="USER_ENTERED")

    log.info("Game Summary tab updated for %s", game_label)


# ── Main entry ─────────────────────────────────────────────────────────────────

def write_all(
    analysis: dict,
    game_label: str,
    sheet_id: str | None = None,
) -> None:
    """
    Write everything to Google Sheets.
    analysis = output from analyzer.analyze()
    """
    sheet_id = sheet_id or os.environ["GOOGLE_SHEET_ID"]
    spreadsheet = get_sheet(sheet_id)

    write_leaderboard(spreadsheet, analysis["leaderboard"])
    write_bets(spreadsheet, analysis["enriched_bets"], game_label)
    write_game_summary(spreadsheet, analysis["summary"], game_label)
    log.info("All Sheets updated ✅")
