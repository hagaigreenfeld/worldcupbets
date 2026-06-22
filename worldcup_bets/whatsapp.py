"""
whatsapp.py — format and push game summary to WhatsApp via the Cloudflare Worker.

Called from main.py after analyze() completes.
The worker handles Twilio delivery; we just POST JSON to /push.
"""

import os
import json
import logging
import requests
from typing import Optional

log = logging.getLogger(__name__)


def format_game_summary(analysis: dict, game_label: str) -> str:
    """
    Build a WhatsApp-friendly Hebrew summary message.
    Uses *bold* (Twilio/WhatsApp markdown).
    """
    summary    = analysis.get("summary", {})
    board      = analysis.get("leaderboard", [])
    bets       = analysis.get("enriched_bets", [])

    game       = summary.get("game", game_label)
    result     = summary.get("actual_result", "⏳ טרם הסתיים")
    n_exact    = summary.get("total_exact", 0)
    n_correct  = summary.get("total_correct", 0)
    n_wrong    = summary.get("total_wrong", 0)
    top_name   = summary.get("top_earner", "—")
    top_pts    = summary.get("top_points", 0)

    # Who picked what
    p1  = summary.get("picked_team1", [])
    p2  = summary.get("picked_team2", [])
    pdraw = summary.get("picked_draw", [])

    # Teams
    parts  = game.split(" vs ")
    team1  = parts[0] if len(parts) > 0 else "קבוצה 1"
    team2  = parts[1] if len(parts) > 1 else "קבוצה 2"

    lines = [
        f"⚽ *{game}*",
        f"📊 תוצאה: *{result}*",
        "",
    ]

    if result and result != "⏳ טרם הסתיים":
        lines += [
            f"🎯 ניחוש מדויק: *{n_exact}* שחקנים",
            f"✅ כיוון נכון: *{n_correct}* שחקנים",
            f"❌ טעו: *{n_wrong}* שחקנים",
            "",
        ]
        if top_name != "—":
            lines.append(f"🏆 הרוויח הכי הרבה: *{top_name}* ({top_pts} נק')")
            lines.append("")

    # Mini pick breakdown
    if p1:
        lines.append(f"🔵 בחרו *{team1}* ({len(p1)}): {', '.join(p1)}")
    if p2:
        lines.append(f"🔴 בחרו *{team2}* ({len(p2)}): {', '.join(p2)}")
    if pdraw:
        lines.append(f"⚪ בחרו *תיקו* ({len(pdraw)}): {', '.join(pdraw)}")

    lines += [
        "",
        "📋 *טבלה מעודכנת:*",
    ]

    medals = ["🥇", "🥈", "🥉"]
    for r in board[:10]:
        i     = r["rank"] - 1
        medal = medals[i] if i < 3 else f"{r['rank']}."
        delta = r.get("rank_delta", "")
        delta_str = f" {delta}" if delta and delta != "—" else ""
        lines.append(f"{medal} {r['name']} — {r['points']} נק'{delta_str}")

    if len(board) > 10:
        lines.append(f"...ועוד {len(board) - 10} שחקנים")

    lines += ["", "🤖 שלח *עזרה* לפקודות נוספות"]

    return "\n".join(lines)


def push_to_whatsapp(message: str, worker_url: Optional[str] = None, secret: Optional[str] = None) -> bool:
    """
    POST the formatted message to the Cloudflare Worker /push endpoint.
    Returns True on success.
    """
    worker_url = worker_url or os.environ.get("WORKER_URL", "")
    secret     = secret     or os.environ.get("PUSH_SECRET", "")

    if not worker_url:
        log.warning("WORKER_URL not set — skipping WhatsApp push")
        return False

    endpoint = worker_url.rstrip("/") + "/push"
    payload  = {"secret": secret, "message": message}

    try:
        resp = requests.post(endpoint, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("WhatsApp push sent ✅  status=%s", resp.status_code)
        return True
    except Exception as exc:
        log.error("WhatsApp push failed: %s", exc)
        return False


def format_kickoff_message(bets: list[dict], game_label: str) -> str:
    """
    Pre-game message sent at kickoff.
    Clusters bets by exact score guess, shows potential points per player.
    Also shows direction-only bettors and anyone who didn't bet.
    """
    if not bets:
        return f"⚽ *{game_label}* — לא נמצאו ניחושים"

    sample = next((b for b in bets if b.get("team1")), bets[0])
    team1 = sample.get("team1", "קבוצה 1")
    team2 = sample.get("team2", "קבוצה 2")

    # Cluster by exact score guess
    score_clusters: dict[str, list[dict]] = {}
    direction_only: dict[str, list[dict]] = {}  # winner guess but no score
    no_bet: list[str] = []

    for b in bets:
        name = b.get("player_name", "?")
        score = (b.get("score_guess") or "").strip()
        winner = (b.get("guess_winner") or "").strip()
        pot = b.get("potential_points", 0)

        if not winner or winner in ("N/A", ""):
            no_bet.append(name)
        elif score and score != "N/A":
            score_clusters.setdefault(score, []).append({"name": name, "pot": pot})
        else:
            direction_only.setdefault(winner, []).append({"name": name, "pot": pot})

    lines = [
        f"⚽ *{game_label}*",
        f"🟢 *המשחק מתחיל!*",
        "",
        f"🎯 *ניחושי תוצאה מדויקת:*",
    ]

    # Sort clusters by number of pickers desc, then by score string
    sorted_scores = sorted(score_clusters.items(), key=lambda x: (-len(x[1]), x[0]))
    if sorted_scores:
        for score, players in sorted_scores:
            player_strs = [f"{p['name']} ({p['pot']} נק')" for p in players]
            lines.append(f"  *{score}* — {', '.join(player_strs)}")
    else:
        lines.append("  אין ניחושי תוצאה מדויקת")

    lines += ["", "📈 *ניחושי כיוון בלבד:*"]

    winner_emoji = {"team1": "🔵", "team2": "🔴", "draw": "⚪"}
    winner_label = {"team1": team1, "team2": team2, "draw": "תיקו"}

    for outcome in ["team1", "team2", "draw"]:
        players = direction_only.get(outcome, [])
        if players:
            emoji = winner_emoji[outcome]
            label = winner_label[outcome]
            player_strs = [f"{p['name']} ({p['pot']} נק')" for p in players]
            lines.append(f"  {emoji} *{label}*: {', '.join(player_strs)}")

    if no_bet:
        lines += ["", f"😶 לא ניחשו: {', '.join(no_bet)}"]

    # Quick tension stats
    team1_count = len(score_clusters_for_winner(score_clusters, "team1_side", team1)) + len(direction_only.get("team1", []))
    team2_count = len(score_clusters_for_winner(score_clusters, "team2_side", team2)) + len(direction_only.get("team2", []))
    draw_count  = len(direction_only.get("draw", [])) + sum(
        len(v) for k, v in score_clusters.items() if _score_is_draw(k)
    )

    lines += [
        "",
        f"📊 {team1}: *{team1_count}* | תיקו: *{draw_count}* | {team2}: *{team2_count}*",
        "",
        "🍿 בהצלחה לכולם!",
    ]

    return "\n".join(lines)


def _score_is_draw(score: str) -> bool:
    """Return True if a score string like '1:1' or '0:0' is a draw."""
    try:
        parts = score.replace("-", ":").split(":")
        return int(parts[0]) == int(parts[1])
    except Exception:
        return False


def score_clusters_for_winner(clusters: dict, _side: str, team_name: str) -> list:
    """Count players who bet on a score where team_name wins (higher goals)."""
    result = []
    for score, players in clusters.items():
        try:
            parts = score.replace("-", ":").split(":")
            g1, g2 = int(parts[0]), int(parts[1])
            if _side == "team1_side" and g1 > g2:
                result.extend(players)
            elif _side == "team2_side" and g2 > g1:
                result.extend(players)
        except Exception:
            pass
    return result


def notify(analysis: dict, game_label: str) -> None:
    """Convenience wrapper called from main.py."""
    msg = format_game_summary(analysis, game_label)
    log.info("WhatsApp message preview:\n%s", msg)
    push_to_whatsapp(msg)


def notify_kickoff(bets: list[dict], game_label: str) -> None:
    """Send pre-game kickoff cluster message."""
    msg = format_kickoff_message(bets, game_label)
    log.info("Kickoff WhatsApp message preview:\n%s", msg)
    push_to_whatsapp(msg)
