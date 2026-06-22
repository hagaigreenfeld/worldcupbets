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
from typing import Optional

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
    for ws in spreadsheet.worksheets():
        if ws.title.strip().lower() == title.strip().lower():
            return ws
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


BETS_HEADERS = [
    "Game", "Round", "Player", "Team 1", "Team 2",
    "Guessed Winner", "Score Guess",
    "Actual Result", "Points Won", "Potential Points",
    "Scraped At",
]


def write_bets(
    spreadsheet: gspread.Spreadsheet,
    bets: list[dict],
    game_label: str,
) -> None:
    """Write bets for a game. Skips silently if rows for this game already exist."""
    ws = ensure_tab(spreadsheet, "All Bets")
    existing = ws.get_all_values()

    # Skip if this game was already written
    if any(row and row[0] == game_label for row in existing[1:]):
        log.info("All Bets: rows for '%s' already exist — skipping write", game_label)
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_rows = []

    if not existing:
        new_rows.append(BETS_HEADERS)

    for b in bets:
        new_rows.append([
            game_label,
            b.get("round_name", ""),
            b.get("player_name", ""),
            b.get("team1", ""),
            b.get("team2", ""),
            b.get("guess_winner", ""),
            b.get("score_guess", ""),
            b.get("actual_result", ""),
            b.get("points_won", ""),
            b.get("potential_points", ""),
            now,
        ])

    if existing:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
    else:
        ws.update(new_rows, value_input_option="USER_ENTERED")

    log.info("All Bets tab: wrote %d rows for %s", len(bets), game_label)


def update_bets_results(
    spreadsheet: gspread.Spreadsheet,
    bets: list[dict],
    game_label: str,
) -> None:
    """After a game finishes, update actual_result and points_won columns in existing rows."""
    ws = ensure_tab(spreadsheet, "All Bets")
    rows = ws.get_all_values()
    if not rows:
        return

    headers = rows[0]
    try:
        game_col   = headers.index("Game")
        player_col = headers.index("Player")
        result_col = headers.index("Actual Result")
        points_col = headers.index("Points Won")
    except ValueError:
        log.warning("All Bets tab headers not found — skipping result update")
        return

    bets_map = {b["player_name"]: b for b in bets}
    updates  = []

    for i, row in enumerate(rows[1:], start=2):  # 1-indexed, skip header
        if not row or row[game_col] != game_label:
            continue
        player = row[player_col] if player_col < len(row) else ""
        bet = bets_map.get(player)
        if not bet:
            continue
        updates.append({"range": f"I{i}", "values": [[bet.get("actual_result", "")]]})
        updates.append({"range": f"J{i}", "values": [[bet.get("points_won", "")]]})

    if updates:
        ws.spreadsheet.values_batch_update({"data": updates, "valueInputOption": "USER_ENTERED"})
        log.info("Updated results for %d rows in All Bets", len(updates) // 2)


def read_bets_for_game(spreadsheet: gspread.Spreadsheet, game_label: str) -> list[dict]:
    """Read bets written at kickoff time for a given game."""
    ws = ensure_tab(spreadsheet, "All Bets")
    rows = ws.get_all_values()
    if not rows:
        return []

    headers = rows[0]
    result  = []
    for row in rows[1:]:
        if not row or row[0] != game_label:
            continue
        entry = dict(zip(headers, row))
        result.append({
            "player_name":      entry.get("Player", ""),
            "round_name":       entry.get("Round", ""),
            "team1":            entry.get("Team 1", ""),
            "team2":            entry.get("Team 2", ""),
            "guess_winner":     entry.get("Guessed Winner", ""),
            "score_guess":      entry.get("Score Guess", ""),
            "actual_result":    entry.get("Actual Result", ""),
            "points_won":       float(entry.get("Points Won", 0) or 0),
            "potential_points": float(entry.get("Potential Points", 0) or 0),
        })
    return result


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
    sheet_id: Optional[str] = None,
) -> None:
    """
    Post-game: update results in existing bet rows, write leaderboard + game summary.
    Bets were already written at kickoff — do NOT write them again here.
    """
    sheet_id = sheet_id or os.environ["GOOGLE_SHEET_ID"]
    spreadsheet = get_sheet(sheet_id)

    update_bets_results(spreadsheet, analysis["enriched_bets"], game_label)
    write_leaderboard(spreadsheet, analysis["leaderboard"])
    write_game_summary(spreadsheet, analysis["summary"], game_label)
    log.info("All Sheets updated ✅")
