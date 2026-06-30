"""
analyzer.py — compute winners, losers, and scenarios from bets rows.
"""

from typing import Optional


WINNER_LABEL = {
    "team1": lambda t1, t2: t1,
    "team2": lambda t1, t2: t2,
    "draw":  lambda t1, t2: "Draw",
    None:    lambda t1, t2: "—",
    "":      lambda t1, t2: "—",
    "N/A":   lambda t1, t2: "—",
}


def enrich_bets(bets: list[dict]) -> list[dict]:
    """
    Add human-readable columns to each bet row:
      - guessed_team_name  : which team the player guessed would win
      - result_status      : Exact / Correct direction / Wrong / Pending
    """
    enriched = []
    for row in bets:
        t1 = row.get("team1", "")
        t2 = row.get("team2", "")
        guess = row.get("guess_winner", "") or ""

        fn = WINNER_LABEL.get(guess, lambda a, b: guess)
        guessed_team = fn(t1, t2)

        actual = row.get("actual_result", "")
        score_guess = row.get("score_guess", "")

        # Classify by DIRECTION vs the actual score — deterministic and
        # consistent. Do NOT use Sport5 gamepoints: they're scraped per-player
        # at different live moments, so identical bets could disagree.
        actual_parsed = _parse_score(actual) if actual else None
        if not actual or actual_parsed is None:
            status = "⏳ Pending"
        elif score_guess and score_guess == actual:
            status = "🎯 Exact!"
        elif guess and guess not in ("N/A", "") and guess == _winner_of(*actual_parsed):
            status = "✅ Correct direction"
        else:
            status = "❌ Wrong"

        enriched.append({
            **row,
            "guessed_team_name": guessed_team,
            "result_status":     status,
        })

    return enriched


def game_summary(bets: list[dict]) -> dict:
    """
    Summarise a single game:
      - game header (team1 vs team2, actual result)
      - how many picked each outcome
      - top points earner
      - bottom points earner
    """
    if not bets:
        return {}

    # Pull game metadata from first valid row
    sample = next((b for b in bets if b.get("team1")), bets[0])
    team1  = sample.get("team1", "Team 1")
    team2  = sample.get("team2", "Team 2")
    actual = sample.get("actual_result", "")

    picks = {"team1": [], "team2": [], "draw": [], "N/A": []}
    for b in bets:
        g = b.get("guess_winner", "N/A") or "N/A"
        bucket = g if g in picks else "N/A"
        picks[bucket].append(b["player_name"])

    pts_sorted = sorted(
        [b for b in bets if b.get("guess_winner", "N/A") != "N/A"],
        key=lambda x: float(x.get("points_won", 0)),
        reverse=True,
    )

    top_pts = pts_sorted[0]["points_won"] if pts_sorted else 0
    top_earners = [b["player_name"] for b in pts_sorted if float(b.get("points_won", 0)) == float(top_pts)]

    # Display points: Sport5 awards gamepoints only once a game is final, so
    # mid-game an exact/correct bet shows 0. Fall back to potential_points then.
    def _disp_pts(b: dict) -> float:
        won = float(b.get("points_won", 0) or 0)
        return won if won > 0 else float(b.get("potential_points", 0) or 0)

    # Group exact scorers with their points
    exact_bets = [b for b in bets if b.get("result_status", "").startswith("🎯")]
    exact_by_score: dict[str, list] = {}
    for b in exact_bets:
        exact_by_score.setdefault(b.get("score_guess", ""), []).append(
            {"name": b["player_name"], "pts": _disp_pts(b)}
        )

    # Group correct-direction bettors with their points
    correct_bets = [b for b in bets if b.get("result_status", "").startswith("✅")]
    correct_by_winner: dict[str, list] = {}
    for b in correct_bets:
        correct_by_winner.setdefault(b.get("guess_winner", ""), []).append(
            {"name": b["player_name"], "pts": _disp_pts(b)}
        )

    return {
        "game":              f"{team1} vs {team2}",
        "team1":             team1,
        "team2":             team2,
        "is_final":          False,  # caller (main.py) sets True when GAME_STATUS=FINISHED
        "actual_result":     actual or "Not yet played",
        "picked_team1":      picks["team1"],
        "picked_team2":      picks["team2"],
        "picked_draw":       picks["draw"],
        "no_bet":            picks["N/A"],
        "top_earners":       top_earners,
        "top_points":        top_pts,
        "top_earner":        top_earners[0] if top_earners else "—",  # kept for backwards compat
        "bottom_earner":     pts_sorted[-1]["player_name"] if pts_sorted else "—",
        "bottom_points":     pts_sorted[-1]["points_won"] if pts_sorted else 0,
        "total_exact":       len(exact_bets),
        "total_correct":     len(correct_bets),
        "total_wrong":       sum(1 for b in bets if b.get("result_status", "").startswith("❌")),
        "exact_by_score":    exact_by_score,
        "correct_by_winner": correct_by_winner,
    }


def leaderboard_delta(before: list[dict], after: list[dict]) -> list[dict]:
    """
    Given leaderboard snapshots before and after a game,
    compute rank changes and point deltas.
    (Pass empty list for `before` on first run.)
    """
    before_map = {r["name"]: r for r in before}
    result = []
    for row in after:
        name   = row["name"]
        prev   = before_map.get(name, {})
        delta_pts  = float(row["points"]) - float(prev.get("points", row["points"]))
        delta_rank = (prev.get("rank", row["rank"]) - row["rank"])  # positive = moved up
        result.append({
            **row,
            "points_delta": f"+{delta_pts}" if delta_pts > 0 else str(delta_pts),
            "rank_delta":   f"↑{delta_rank}" if delta_rank > 0 else (f"↓{abs(delta_rank)}" if delta_rank < 0 else "—"),
        })
    return result


def _parse_score(score: str) -> Optional[tuple[int, int]]:
    try:
        parts = score.replace("-", ":").split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        return None


def _winner_of(g1: int, g2: int) -> str:
    if g1 > g2:
        return "team1"
    if g2 > g1:
        return "team2"
    return "draw"


def _bet_status(bet: dict, score: str) -> str:
    """Return 'exact', 'correct', or 'wrong' for a bet against a hypothetical score."""
    parsed = _parse_score(score)
    if parsed is None:
        return "wrong"
    g1, g2 = parsed
    if bet.get("score_guess") == score:
        return "exact"
    if bet.get("guess_winner") == _winner_of(g1, g2):
        return "correct"
    return "wrong"


def what_if_analysis(bets: list[dict], current_score: str) -> dict:
    """
    Given the current live score, compute what changes if team1 scores next
    vs team2 scores next.

    Returns:
        {
            "current": "2:0",
            "if_team1": { "score": "3:0", "changes": [...] },
            "if_team2": { "score": "2:1", "changes": [...] },
        }
    where each change is { "player", "from", "to" }.
    """
    parsed = _parse_score(current_score)
    if parsed is None:
        return {}
    g1, g2 = parsed

    result = {"current": current_score}
    for side, (ng1, ng2) in [("if_team1", (g1 + 1, g2)), ("if_team2", (g1, g2 + 1))]:
        new_score = f"{ng1}:{ng2}"
        changes = []
        for b in bets:
            if not b.get("guess_winner") or b.get("guess_winner") in ("N/A", ""):
                continue
            old_s = _bet_status(b, current_score)
            new_s = _bet_status(b, new_score)
            if old_s != new_s:
                changes.append({
                    "player": b["player_name"],
                    "bet":    b.get("score_guess", b.get("guess_winner", "")),
                    "from":   old_s,
                    "to":     new_s,
                })
        result[side] = {"score": new_score, "changes": changes}

    return result


def leaderboard_position_changes(
    current: list[dict],
    previous: list[dict],
) -> list[dict]:
    """
    Compare current leaderboard to previous snapshot.
    Returns entries sorted by absolute rank change desc, with delta fields.
    """
    prev_map = {r["name"]: r for r in previous}
    rows = []
    for r in current:
        prev = prev_map.get(r["name"], {})
        prev_rank   = prev.get("rank", r["rank"])
        prev_points = float(prev.get("points", r.get("points", 0)))
        rank_change  = prev_rank - r["rank"]   # positive = moved up
        points_change = float(r.get("points", 0)) - prev_points
        rows.append({
            **r,
            "prev_rank":     prev_rank,
            "rank_change":   rank_change,
            "points_change": points_change,
        })
    return sorted(rows, key=lambda x: abs(x["rank_change"]), reverse=True)


def coming_up_analysis(
    upcoming_games: list[dict],
    bets_per_game: dict[str, list[dict]],
    leaderboard: list[dict],
) -> list[dict]:
    """
    For each of the next few games, compute:
      - Max points per outcome (win team1 / draw / win team2)
      - Which outcome is the "upset" (highest ratio, least likely, most rewarding)
      - Who can overtake someone above them if the upset happens
      - Whether they've already bet on the upset or not yet

    upcoming_games : from scraper.get_upcoming_games_with_odds()
    bets_per_game  : { gid: [bet_rows] } from scraper.scrape_all_players_upcoming()
    leaderboard    : [{ rank, name, points }]
    """
    lb_sorted = sorted(leaderboard, key=lambda r: r["rank"])
    lb_map    = {r["name"]: r for r in lb_sorted}

    results = []

    for game in upcoming_games:
        ratio1 = float(game.get("ratio1", 0) or 0)
        ratio2 = float(game.get("ratio2", 0) or 0)
        ratio3 = float(game.get("ratio3", 0) or 0)
        p1     = float(game.get("max_pts_team1", 0) or 0)
        pd     = float(game.get("max_pts_draw", 0) or 0)
        p2     = float(game.get("max_pts_team2", 0) or 0)

        team1 = game.get("team1", "")
        team2 = game.get("team2", "")

        outcome_pts = {"team1": p1, "draw": pd, "team2": p2}
        upset_outcome    = max(outcome_pts, key=outcome_pts.get)
        favorite_outcome = min(outcome_pts, key=outcome_pts.get)
        upset_max_pts    = outcome_pts[upset_outcome]

        gid  = game.get("gid", "")
        bets = bets_per_game.get(gid, [])

        # Map player name → their bet for this game
        bets_map = {b["player_name"]: b for b in bets if b.get("player_name")}

        overtake_opps = []

        for lb_entry in lb_sorted:
            name   = lb_entry["name"]
            rank   = lb_entry["rank"]
            points = float(lb_entry.get("points", 0))

            if rank == 1:
                continue

            # Person directly above
            above = next((r for r in lb_sorted if r["rank"] == rank - 1), None)
            if not above:
                continue

            gap_to_above = float(above.get("points", 0)) - points

            # How far could they jump with upset_max_pts?
            new_pts       = points + upset_max_pts
            would_reach_rank = sum(1 for r in lb_sorted if float(r.get("points", 0)) > new_pts) + 1
            places_gained = rank - would_reach_rank

            if places_gained <= 0:
                continue  # upset doesn't help this player

            # What has the player actually bet?
            bet         = bets_map.get(name, {})
            their_guess = (bet.get("guess_winner") or "").strip()
            their_score = (bet.get("score_guess") or "").strip()
            has_bet     = bool(their_guess and their_guess not in ("N/A", ""))
            on_upset    = has_bet and (their_guess == upset_outcome)

            # Direction points (no exact bonus): ratio × mult (for context)
            their_pot   = float(bet.get("potential_points", 0) or 0)

            overtake_opps.append({
                "player":           name,
                "current_rank":     rank,
                "current_points":   points,
                "above_player":     above["name"],
                "above_rank":       above["rank"],
                "above_points":     float(above.get("points", 0)),
                "gap_to_above":     gap_to_above,
                "has_bet":          has_bet,
                "their_guess":      their_guess,
                "their_score":      their_score,
                "their_max_pts":    their_pot,
                "on_upset":         on_upset,
                "upset_outcome":    upset_outcome,
                "upset_max_pts":    upset_max_pts,
                "would_reach_rank": would_reach_rank,
                "places_gained":    places_gained,
            })

        # Sort by most dramatic jump (most places gained first)
        overtake_opps.sort(key=lambda x: (-x["places_gained"], x["current_rank"]))

        round_name = game.get("round_name", "")
        label      = f"{team1} vs {team2}"
        if round_name:
            label += f" ({round_name})"

        kickoff_ts = game.get("kickoff_ts", 0)
        try:
            from datetime import datetime, timezone
            kickoff_str = (
                datetime.fromtimestamp(int(kickoff_ts) / 1000, tz=timezone.utc).strftime("%d/%m %H:%M")
                if kickoff_ts else ""
            )
        except Exception:
            kickoff_str = ""

        results.append({
            "gid":              gid,
            "label":            label,
            "team1":            team1,
            "team2":            team2,
            "round_name":       round_name,
            "ratio1":           ratio1,
            "ratio2":           ratio2,
            "ratio3":           ratio3,
            "max_pts_team1":    p1,
            "max_pts_draw":     pd,
            "max_pts_team2":    p2,
            "upset_outcome":    upset_outcome,
            "upset_max_pts":    upset_max_pts,
            "favorite_outcome": favorite_outcome,
            "kickoff_ts":       kickoff_ts,
            "kickoff_str":      kickoff_str,
            "overtake_opps":    overtake_opps,
        })

    return results


def analyze(bets: list[dict], leaderboard: list[dict]) -> dict:
    """
    Full analysis bundle returned to sheets writer.
    Returns {enriched_bets, summary, leaderboard}.
    """
    enriched  = enrich_bets(bets)
    summary   = game_summary(enriched)
    return {
        "enriched_bets": enriched,
        "summary":       summary,
        "leaderboard":   leaderboard,
    }
