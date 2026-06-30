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
    """Return the worksheet named `title`, creating it if needed.
    Extends the grid to 2000 rows if the existing sheet is smaller."""
    for ws in spreadsheet.worksheets():
        if ws.title.strip().lower() == title.strip().lower():
            if ws.row_count < 2000:
                ws.resize(rows=2000)
            return ws
    ws = spreadsheet.add_worksheet(title=title, rows=2000, cols=30)
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


def _bet_row(game_label: str, b: dict, now: str) -> list:
    return [
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
    ]


def write_bets(
    spreadsheet: gspread.Spreadsheet,
    bets: list[dict],
    game_label: str,
) -> None:
    """Write bets for a game. Overwrites any existing rows for this game (in case
    they were written with stale/empty potential_points by an earlier run)."""
    overwrite_game_bets(spreadsheet, bets, game_label)


def overwrite_game_bets(
    spreadsheet: gspread.Spreadsheet,
    bets: list[dict],
    game_label: str,
) -> None:
    """Delete all existing rows for game_label in All Bets and rewrite with fresh data.
    Also ensures the header row matches BETS_HEADERS (fixes old-format sheets)."""
    ws      = ensure_tab(spreadsheet, "All Bets")
    rows    = ws.get_all_values()
    now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not rows:
        # Empty sheet: write header + data in one shot
        ws.update([BETS_HEADERS] + [_bet_row(game_label, b, now) for b in bets],
                  value_input_option="USER_ENTERED")
        log.info("All Bets tab: wrote header + %d rows for %s", len(bets), game_label)
        return

    # Identify header row: first row that contains "Game" (col 0) and "Player" (col 2)
    # Old code had a "Status" column that was later removed — rewrite the header if stale.
    header_row_idx = None  # 0-indexed in `rows`
    for idx, row in enumerate(rows):
        if row and row[0] == "Game" and len(row) >= 3 and row[2] == "Player":
            header_row_idx = idx
            break

    # Collect 1-indexed sheet row numbers for this game (skip header row)
    game_rows_sheet = []  # 1-indexed
    for idx, row in enumerate(rows):
        if idx == header_row_idx:
            continue
        if row and row[0] == game_label:
            game_rows_sheet.append(idx + 1)  # sheets are 1-indexed

    # Delete stale rows in reverse order (so indices don't shift)
    for sheet_row in reversed(game_rows_sheet):
        ws.delete_rows(sheet_row)
    if game_rows_sheet:
        log.info("Deleted %d stale rows for %s", len(game_rows_sheet), game_label)

    # Fix header row if it's stale (wrong columns from old code)
    if header_row_idx is not None and rows[header_row_idx] != BETS_HEADERS:
        ws.update([BETS_HEADERS], range_name=f"A{header_row_idx + 1}",
                  value_input_option="USER_ENTERED")
        log.info("Rewrote All Bets header row (old format detected)")

    # If no header row found, prepend one before the data
    if header_row_idx is None:
        ws.insert_row(BETS_HEADERS, index=1, value_input_option="USER_ENTERED")
        log.info("Inserted missing header row in All Bets")

    # Append fresh rows for this game
    new_rows = [_bet_row(game_label, b, now) for b in bets]
    ws.append_rows(new_rows, value_input_option="USER_ENTERED")
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
        game_col      = headers.index("Game")
        player_col    = headers.index("Player")
        result_col    = headers.index("Actual Result")
        points_col    = headers.index("Points Won")
    except ValueError:
        log.warning("All Bets tab headers not found — skipping result update")
        return

    result_letter = chr(ord("A") + result_col)
    points_letter = chr(ord("A") + points_col)
    bets_map = {b["player_name"]: b for b in bets}
    updates  = []

    for i, row in enumerate(rows[1:], start=2):  # 1-indexed, skip header
        if not row or row[game_col] != game_label:
            continue
        player = row[player_col] if player_col < len(row) else ""
        bet = bets_map.get(player)
        if not bet:
            continue
        updates.append({"range": f"'All Bets'!{result_letter}{i}", "values": [[bet.get("actual_result", "")]]})
        updates.append({"range": f"'All Bets'!{points_letter}{i}", "values": [[bet.get("points_won", "")]]})

    if updates:
        ws.spreadsheet.values_batch_update({"data": updates, "valueInputOption": "USER_ENTERED"})
        log.info("Updated results for %d rows in All Bets", len(updates) // 2)


def update_bets_potential_points(
    spreadsheet: gspread.Spreadsheet,
    bets: list[dict],
    game_label: str,
) -> None:
    """Update the Potential Points column for existing rows (used after a re-scrape).
    Handles sheets written by old code that had a different column layout."""
    ws = ensure_tab(spreadsheet, "All Bets")
    rows = ws.get_all_values()
    if not rows:
        return

    # Find the header row (may not be row 0 on malformed sheets)
    header_row = None
    header_sheet_idx = None  # 1-indexed
    for idx, row in enumerate(rows):
        if row and "Game" in row and "Player" in row and "Potential Points" in row:
            header_row = row
            header_sheet_idx = idx + 1
            break

    if not header_row:
        log.warning("All Bets: could not find header row with 'Potential Points' — "
                    "overwriting game rows instead")
        overwrite_game_bets(spreadsheet, bets, game_label)
        return

    try:
        game_col    = header_row.index("Game")
        player_col  = header_row.index("Player")
        pot_col_idx = header_row.index("Potential Points")
    except ValueError:
        log.warning("All Bets tab headers incomplete — overwriting game rows instead")
        overwrite_game_bets(spreadsheet, bets, game_label)
        return

    pot_col_letter = chr(ord("A") + pot_col_idx)
    bets_map = {b["player_name"]: b for b in bets}
    updates  = []

    for i, row in enumerate(rows, start=1):
        if i == header_sheet_idx:
            continue
        if not row or row[game_col] != game_label:
            continue
        player = row[player_col] if player_col < len(row) else ""
        bet = bets_map.get(player)
        if not bet:
            continue
        pot = bet.get("potential_points", "")
        if pot:
            updates.append({"range": f"'All Bets'!{pot_col_letter}{i}", "values": [[pot]]})

    if updates:
        ws.spreadsheet.values_batch_update({"data": updates, "valueInputOption": "USER_ENTERED"})
        log.info("Updated potential_points for %d rows in All Bets (%s)", len(updates), game_label)
    else:
        log.warning("No rows found to update potential_points for %s — overwriting", game_label)
        overwrite_game_bets(spreadsheet, bets, game_label)


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


BONUS_BETS_TAB = "Nicknames"

# English team name → Hebrew (Sport5 uses Hebrew; bonus sheet uses English)
TEAM_EN_TO_HE: dict[str, str] = {
    "France": "צרפת", "Brazil": "ברזיל", "Argentina": "ארגנטינה",
    "Germany": "גרמניה", "Spain": "ספרד", "England": "אנגליה",
    "Portugal": "פורטוגל", "Netherlands": "הולנד", "Belgium": "בלגיה",
    "Italy": "איטליה", "Uruguay": "אורוגוואי", "Mexico": "מקסיקו",
    "United States": "ארה\"ב", "USA": "ארה\"ב",
    "Japan": "יפן", "Australia": "אוסטרליה", "Morocco": "מרוקו",
    "Senegal": "סנגל", "Croatia": "קרואטיה", "Poland": "פולין",
    "Switzerland": "שוויץ", "Denmark": "דנמרק", "Serbia": "סרביה",
    "Ecuador": "אקוודור", "Cameroon": "קמרון", "Ghana": "גאנה",
    "South Korea": "קוריאה", "Iran": "איראן", "Saudi Arabia": "ערב הסעודית",
    "Canada": "קנדה", "Colombia": "קולומביה", "Venezuela": "ונצואלה",
    "Austria": "אוסטריה", "Hungary": "הונגריה", "Turkey": "טורקיה",
    "Ukraine": "אוקראינה", "New Zealand": "ניו זילנד", "Egypt": "מצרים",
    "Iraq": "עיראק", "Nigeria": "ניגריה", "Ivory Coast": "חוף השנהב",
    "Algeria": "אלג'יריה", "Tunisia": "תוניסיה", "Slovakia": "סלובקיה",
    "Czechia": "צ'כיה", "Czech Republic": "צ'כיה", "Romania": "רומניה",
    "Scotland": "סקוטלנד", "Slovenia": "סלובניה", "Albania": "אלבניה",
    "Georgia": "גאורגיה", "Paraguay": "פרגוואי", "Peru": "פרו",
    "Honduras": "הונדורס", "Costa Rica": "קוסטה ריקה", "Panama": "פנמה",
    "Bahrain": "בחריין", "Uzbekistan": "אוזבקיסטן", "Indonesia": "אינדונזיה",
    "China": "סין", "Chile": "צ'ילה", "Bolivia": "בוליביה",
    "Norway": "נורווגיה", "Sweden": "שבדיה", "Greece": "יוון",
    "Wales": "ויילס", "Republic of Ireland": "אירלנד", "Ireland": "אירלנד",
    "Finland": "פינלנד", "Iceland": "איסלנד", "Russia": "רוסיה",
    "Jordan": "ירדן", "Qatar": "קטאר", "United Arab Emirates": "איחוד האמירויות",
    "South Africa": "דרום אפריקה", "Mali": "מאלי", "Jamaica": "ג'מייקה",
    "Cape Verde": "כף ורדה", "DR Congo": "קונגו", "Burkina Faso": "בורקינה פאסו",
    "Bosnia and Herzegovina": "בוסניה", "North Macedonia": "מקדוניה",
    "Israel": "ישראל", "Kuwait": "כווית", "Oman": "עומאן",
}


def team_en_to_he(english: str) -> str:
    return TEAM_EN_TO_HE.get(english.strip(), english.strip())


def read_bonus_bets(spreadsheet: gspread.Spreadsheet) -> list[dict]:
    """
    Read member bonus picks from the Nicknames tab.
    Returns list of dicts: {board_name, nickname, player, team_en, team_he, winner_team_he}
    """
    try:
        ws = ensure_tab(spreadsheet, BONUS_BETS_TAB)
        rows = ws.get_all_values()
    except Exception as exc:
        log.warning("Could not read bonus bets tab: %s", exc)
        return []

    if len(rows) < 2:
        return []

    headers = [h.strip().lower() for h in rows[0]]
    result  = []
    for row in rows[1:]:
        if not any(row):
            continue
        entry    = dict(zip(headers, row))
        team_en  = entry.get("players team", "").strip()
        winner   = entry.get("winner team", "").strip()
        player_en = entry.get("player", "").strip()
        player_he = entry.get("player hebrew", "").strip() or entry.get("player_hebrew", "").strip() or player_en
        result.append({
            "board_name":     entry.get("booard name", "").strip(),
            "nickname":       entry.get("nickname", "").strip(),
            "player":         player_he,   # Hebrew if available, else English
            "player_en":      player_en,
            "team_en":        team_en,
            "team_he":        team_en_to_he(team_en),
            "winner_team_en": winner,
            "winner_team_he": team_en_to_he(winner),
        })

    log.info("Loaded %d bonus bets from Nicknames tab", len(result))
    return result


LEADERBOARD_HISTORY_HEADERS = ["Game", "Rank", "Player", "Points", "Saved At"]


def save_leaderboard_snapshot(
    spreadsheet: gspread.Spreadsheet,
    leaderboard: list[dict],
    game_label: str,
) -> None:
    """Append a leaderboard snapshot after each game for future comparison."""
    ws = ensure_tab(spreadsheet, "Leaderboard History")
    existing = ws.get_all_values()

    # Skip if this game snapshot already saved
    if any(row and row[0] == game_label for row in existing[1:]):
        log.info("Leaderboard History: snapshot for '%s' already exists — skipping", game_label)
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []
    if not existing:
        rows.append(LEADERBOARD_HISTORY_HEADERS)
    for r in leaderboard:
        rows.append([game_label, r.get("rank", ""), r.get("name", ""), r.get("points", ""), now])

    if existing:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    else:
        ws.update(rows, value_input_option="USER_ENTERED")
    log.info("Leaderboard History: saved %d rows for %s", len(leaderboard), game_label)


def read_previous_leaderboard_snapshot(
    spreadsheet: gspread.Spreadsheet,
    current_game_label: str,
) -> list[dict]:
    """
    Return the leaderboard snapshot for the game BEFORE current_game_label.
    If none found, returns empty list.
    """
    try:
        ws = ensure_tab(spreadsheet, "Leaderboard History")
        rows = ws.get_all_values()
    except Exception:
        return []
    if len(rows) < 2:
        return []

    headers = rows[0]
    # Collect unique game labels in order of appearance
    seen_games: list[str] = []
    for row in rows[1:]:
        if row and row[0] and row[0] not in seen_games:
            seen_games.append(row[0])

    # Find the game before current
    try:
        idx = seen_games.index(current_game_label)
        prev_game = seen_games[idx - 1] if idx > 0 else None
    except ValueError:
        # current game not yet in history — use the last saved game
        prev_game = seen_games[-1] if seen_games else None

    if not prev_game:
        return []

    result = []
    for row in rows[1:]:
        if not row or row[0] != prev_game:
            continue
        entry = dict(zip(headers, row))
        result.append({
            "name":   entry.get("Player", ""),
            "rank":   int(entry.get("Rank", 0) or 0),
            "points": float(entry.get("Points", 0) or 0),
        })
    return result


def read_leaderboard(spreadsheet: gspread.Spreadsheet) -> list[dict]:
    """Read current leaderboard from the Leaderboard tab."""
    try:
        ws   = ensure_tab(spreadsheet, "Leaderboard")
        rows = ws.get_all_values()
    except Exception:
        return []
    if len(rows) < 2:
        return []
    headers = rows[0]
    result  = []
    for i, row in enumerate(rows[1:]):
        entry = dict(zip(headers, row))
        result.append({
            "rank":   int(entry.get("Rank", i + 1) or i + 1),
            "name":   entry.get("Player", ""),
            "points": float(entry.get("Points", 0) or 0),
        })
    return result


def write_game_summary(
    spreadsheet: gspread.Spreadsheet,
    summary: dict,
    game_label: str,
) -> None:
    ws = ensure_tab(spreadsheet, "Game Summary")
    existing = ws.get_all_values()

    # Skip if this game was already written
    if any(row and row[0] == game_label for row in existing[1:]):
        log.info("Game Summary: row for '%s' already exists — skipping", game_label)
        return

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


UPCOMING_GAMES_HEADERS = [
    "Game Label", "Round", "Team 1", "Team 2",
    "Win Team1 Max Pts", "Draw Max Pts", "Win Team2 Max Pts",
    "Ratio Team1", "Ratio Draw", "Ratio Team2",
    "Kickoff (UTC)", "Updated At",
]


def write_upcoming_games(spreadsheet: gspread.Spreadsheet, games: list[dict]) -> None:
    """Overwrite the Upcoming Games tab with the full remaining schedule + odds."""
    ws  = ensure_tab(spreadsheet, "Upcoming Games")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows = [UPCOMING_GAMES_HEADERS]
    for g in games:
        ts = g.get("kickoff_ts", 0)
        try:
            kickoff_str = (
                datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%d/%m %H:%M")
                if ts else ""
            )
        except Exception:
            kickoff_str = str(ts)

        label = f"{g['team1']} vs {g['team2']}"
        if g.get("round_name"):
            label += f" ({g['round_name']})"

        rows.append([
            label,
            g.get("round_name", ""),
            g.get("team1", ""),
            g.get("team2", ""),
            g.get("max_pts_team1", ""),
            g.get("max_pts_draw", ""),
            g.get("max_pts_team2", ""),
            g.get("ratio1", ""),
            g.get("ratio2", ""),
            g.get("ratio3", ""),
            kickoff_str,
            now,
        ])

    ws.clear()
    ws.update(rows, value_input_option="USER_ENTERED")
    log.info("Upcoming Games tab updated (%d games)", len(games))


UPCOMING_BETS_HEADERS = [
    "Game", "Round", "Kickoff (UTC)", "Team 1", "Team 2",
    "Player", "Score Guess", "Direction",
]


def write_upcoming_bets(
    spreadsheet: gspread.Spreadsheet,
    games: list[dict],
    bets_per_game: dict[str, list[dict]],
) -> None:
    """Overwrite the Upcoming Bets tab with all players' guesses for all upcoming games."""
    ws  = ensure_tab(spreadsheet, "Upcoming Bets")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows = [UPCOMING_BETS_HEADERS]
    for g in games:
        gid  = g.get("gid", "")
        bets = bets_per_game.get(gid, [])
        if not bets:
            continue

        ts = g.get("kickoff_ts", 0)
        try:
            kickoff_str = (
                datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%d/%m %H:%M")
                if ts else ""
            )
        except Exception:
            kickoff_str = str(ts)

        label  = f"{g['team1']} vs {g['team2']}"
        round_ = g.get("round_name", "")
        t1     = g.get("team1", "")
        t2     = g.get("team2", "")

        for b in bets:
            direction = b.get("guess_winner", "")
            dir_label = {"team1": t1, "team2": t2, "draw": "תיקו"}.get(direction, direction)
            rows.append([
                label,
                round_,
                kickoff_str,
                t1,
                t2,
                b.get("player_name", ""),
                b.get("score_guess", ""),
                dir_label,
            ])

    ws.clear()
    ws.update(rows, value_input_option="USER_ENTERED")
    log.info("Upcoming Bets tab updated (%d rows across %d games)", len(rows) - 1, len(games))


# ── Main entry ─────────────────────────────────────────────────────────────────

def write_all(
    analysis: dict,
    game_label: str,
    sheet_id: Optional[str] = None,
    spreadsheet: Optional[gspread.Spreadsheet] = None,
) -> None:
    """
    Post-game: update results in existing bet rows, write leaderboard + game summary.
    Bets were already written at kickoff — do NOT write them again here.
    """
    if spreadsheet is None:
        sheet_id = sheet_id or os.environ["GOOGLE_SHEET_ID"]
        spreadsheet = get_sheet(sheet_id)

    update_bets_results(spreadsheet, analysis["enriched_bets"], game_label)
    write_leaderboard(spreadsheet, analysis["leaderboard"])
    save_leaderboard_snapshot(spreadsheet, analysis["leaderboard"], game_label)
    write_game_summary(spreadsheet, analysis["summary"], game_label)
    log.info("All Sheets updated ✅")
