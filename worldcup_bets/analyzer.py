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
        pts = row.get("points_won", 0)

        if not actual:
            status = "⏳ Pending"
        elif score_guess and actual and score_guess == actual:
            status = "🎯 Exact!"
        elif pts and float(pts) > 0:
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

    return {
        "game":           f"{team1} vs {team2}",
        "actual_result":  actual or "Not yet played",
        "picked_team1":   picks["team1"],
        "picked_team2":   picks["team2"],
        "picked_draw":    picks["draw"],
        "no_bet":         picks["N/A"],
        "top_earner":     pts_sorted[0]["player_name"] if pts_sorted else "—",
        "top_points":     pts_sorted[0]["points_won"]  if pts_sorted else 0,
        "bottom_earner":  pts_sorted[-1]["player_name"] if pts_sorted else "—",
        "bottom_points":  pts_sorted[-1]["points_won"] if pts_sorted else 0,
        "total_exact":    sum(1 for b in bets if b.get("result_status", "").startswith("🎯")),
        "total_correct":  sum(1 for b in bets if b.get("result_status", "").startswith("✅")),
        "total_wrong":    sum(1 for b in bets if b.get("result_status", "").startswith("❌")),
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
