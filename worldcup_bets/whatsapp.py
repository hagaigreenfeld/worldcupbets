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

NICKNAMES = {
    "Nir mish":        "בבה",
    "חיים אבירם":      "חיים",
    "adam aviram":     "אדם הראשון",
    "אלון גזית":       "גזה",
    "asaf gazit":      "גזה ג׳וניור",
    "חגי גרינפלד":     "חגי",
    "אדם אבירם":       "אדם השני",
    "מוטי דקל":        "מוטי",
    "בני אוחיון":      "בני",
    "אדיר":            "אדיר",
    "Eran Gazit":      "אח של גזה",
    "Avishay Shefer":  "אבישי",
    "Reshef Elias":    "רשף",
    "roi piro29":      "פירו",
    "Rom Mishali":     "דוד ג׳וניור",
    "סהר פירו":        "סהר פירו",
    "Yoav Pais":       "יואב",
    "יותם":            "יותם",
    "Eran Sandel":     "סנדל",
    "PIR0":            "פירו ג׳וניור",
}


def nickname(name: str) -> str:
    return NICKNAMES.get(name.strip(), name)


def format_game_summary(analysis: dict, game_label: str, what_if: dict = None, position_movers: list = None) -> str:
    """
    Build a WhatsApp-friendly Hebrew post-game summary.
    what_if: result of analyzer.what_if_analysis()
    position_movers: result of analyzer.leaderboard_position_changes()
    """
    summary = analysis.get("summary", {})
    board   = analysis.get("leaderboard", [])

    game   = summary.get("game", game_label)
    result = summary.get("actual_result", "⏳ טרם הסתיים")
    team1  = summary.get("team1", game.split(" vs ")[0] if " vs " in game else "קבוצה 1")
    team2  = summary.get("team2", game.split(" vs ")[-1] if " vs " in game else "קבוצה 2")

    top_earners = [nickname(n) for n in summary.get("top_earners", [summary.get("top_earner", "—")])]
    top_pts     = summary.get("top_points", 0)
    n_exact     = summary.get("total_exact", 0)
    n_correct   = summary.get("total_correct", 0)
    n_wrong     = summary.get("total_wrong", 0)
    no_bet      = [nickname(n) for n in summary.get("no_bet", [])]

    is_final = summary.get("is_final", True)
    result_label = "תוצאה סופית" if is_final else "תוצאת ביניים"

    # Reverse score for RTL display: "2:0" stored as team1:team2,
    # but in RTL the rightmost number reads first, so flip to show correctly.
    def rtl_score(score: str) -> str:
        parts = score.split(":")
        return ":".join(reversed(parts)) if len(parts) == 2 else score

    lines = [
        f"⚽ *{game}*",
        f"📊 {result_label}: *{rtl_score(result)}*",
        "",
    ]

    # Exact score winners
    exact_by_score = summary.get("exact_by_score", {})
    if exact_by_score:
        lines.append("🎯 *ניחוש מדויק:*")
        for score, players in exact_by_score.items():
            names = ", ".join(nickname(p["name"]) for p in players)
            pts   = players[0]["pts"]
            lines.append(f"  *{rtl_score(score)}* — {names} ({pts} נק')")
    else:
        lines.append("🎯 אף אחד לא ניחש מדויק")

    lines.append("")

    # Correct direction
    correct_by_winner = summary.get("correct_by_winner", {})
    if correct_by_winner:
        lines.append("✅ *כיוון נכון:*")
        winner_label = {"team1": team1, "team2": team2, "draw": "תיקו"}
        for outcome, players in correct_by_winner.items():
            label = winner_label.get(outcome, outcome)
            names = ", ".join(nickname(p["name"]) for p in players)
            pts   = players[0]["pts"]
            lines.append(f"  {label} — {names} ({pts} נק')")

    lines.append("")

    # Wrong bets
    wrong = [nickname(b["player_name"]) for b in analysis.get("enriched_bets", [])
             if b.get("result_status", "").startswith("❌")]
    if wrong:
        lines.append(f"❌ *טעו:* {', '.join(wrong)}")
        lines.append("")

    # No bet
    if no_bet:
        lines.append(f"😭 *הידעת ולא הימרת?!* {', '.join(no_bet)}")
        lines.append("")

    # What-if scenario (mid-game only)
    if what_if:
        block = format_what_if(what_if, team1, team2)
        if block:
            lines.append(block)
            lines.append("")

    # Position changes vs previous game
    if position_movers:
        block = format_position_changes(position_movers)
        if block:
            lines.append(block)
            lines.append("")

    # Leaderboard
    lines.append("📋 *טבלה מעודכנת:*")
    medals = ["🥇", "🥈", "🥉"]
    for r in board[:10]:
        i         = r["rank"] - 1
        medal     = medals[i] if i < 3 else f"{r['rank']}."
        delta     = r.get("rank_delta", "")
        delta_str = f" {delta}" if delta and delta != "—" else ""
        lines.append(f"{medal} {nickname(r['name'])} — {r['points']} נק'{delta_str}")

    if len(board) > 10:
        lines.append(f"...ועוד {len(board) - 10} שחקנים")

    return "\n".join(lines)


STATUS_EMOJI = {"exact": "🎯", "correct": "✅", "wrong": "❌"}
STATUS_HE    = {"exact": "ניחוש מדויק", "correct": "כיוון נכון", "wrong": "טעות"}


def format_what_if(what_if: dict, team1: str, team2: str) -> str:
    """Format the what-if next-goal analysis block."""
    if not what_if or "if_team1" not in what_if:
        return ""

    lines = ["", "🔮 *מה יקרה אם...*"]

    for side, team_name in [("if_team1", team1), ("if_team2", team2)]:
        scenario = what_if.get(side, {})
        score    = scenario.get("score", "")
        changes  = scenario.get("changes", [])

        # Reverse score for RTL display
        rtl_score = ":".join(reversed(score.split(":"))) if ":" in score else score
        lines.append(f"  ⚽ *{team_name} תבקיע ({rtl_score}):*")

        if not changes:
            lines.append("    — אין שינויים בניחושים")
        else:
            gains = [c for c in changes if c["to"] in ("exact", "correct") and c["from"] == "wrong"]
            gains += [c for c in changes if c["to"] == "exact" and c["from"] == "correct"]
            loses = [c for c in changes if c["to"] == "wrong"]
            loses += [c for c in changes if c["to"] == "correct" and c["from"] == "exact"]

            if gains:
                names = ", ".join(nickname(c["player"]) for c in gains)
                labels = [f"{STATUS_EMOJI[c['to']]} {STATUS_HE[c['to']]}" for c in gains]
                lines.append(f"    📈 מרוויחים: {names}")
            if loses:
                names = ", ".join(nickname(c["player"]) for c in loses)
                lines.append(f"    📉 מפסידים: {names}")

    return "\n".join(lines)


def format_position_changes(movers: list[dict]) -> str:
    """Format biggest leaderboard position movers block."""
    if not movers:
        return ""

    significant = [m for m in movers if abs(m.get("rank_change", 0)) >= 1]
    if not significant:
        return ""

    lines = ["", "📈 *שינויי מיקום מהמשחק הקודם:*"]
    for m in significant[:5]:
        delta = m["rank_change"]
        arrow = f"⬆️ +{delta}" if delta > 0 else f"⬇️ {delta}"
        pts_delta = m.get("points_change", 0)
        pts_str   = f" (+{pts_delta:.0f} נק')" if pts_delta > 0 else ""
        lines.append(f"  {arrow} {nickname(m['name'])}{pts_str}")

    return "\n".join(lines)


def push_to_whatsapp(message: str, worker_url: Optional[str] = None, secret: Optional[str] = None) -> bool:
    """
    POST the formatted message to the Cloudflare Worker /push endpoint.
    Sends to WHATSAPP_GROUP_ID, and also to SENDER if set and different.
    Returns True on success.
    """
    worker_url = worker_url or os.environ.get("WORKER_URL", "")
    secret     = secret     or os.environ.get("PUSH_SECRET", "")
    sender     = os.environ.get("SENDER", "").strip()

    if not worker_url:
        log.warning("WORKER_URL not set — skipping WhatsApp push")
        return False

    endpoint = worker_url.rstrip("/") + "/push"

    def _send(to: Optional[str] = None) -> bool:
        payload = {"secret": secret, "message": message}
        if to:
            payload["to"] = to
        try:
            resp = requests.post(endpoint, json=payload, timeout=15)
            resp.raise_for_status()
            log.info("WhatsApp push sent ✅  to=%s status=%s", to or "default", resp.status_code)
            return True
        except Exception as exc:
            log.error("WhatsApp push failed (to=%s): %s", to or "default", exc)
            return False

    return _send()  # send to WHATSAPP_GROUP_ID (group members including sender will see it)


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

        name = nickname(name)
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
        f"🎯 *הימורי תוצאות:*",
    ]

    # Sort clusters by number of pickers desc, then by score string
    sorted_scores = sorted(score_clusters.items(), key=lambda x: (-len(x[1]), x[0]))
    if sorted_scores:
        for score, players in sorted_scores:
            names = ", ".join(p["name"] for p in players)
            pot   = players[0]["pot"]
            lines.append(f"  *{score}* — {names} ({pot} נק')")
    else:
        lines.append("  אין הימורי תוצאה")

    if no_bet:
        lines += ["", f"😭 *הידעת ולא הימרת?!* {', '.join(no_bet)}"]

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


def notify(analysis: dict, game_label: str, what_if: dict = None, position_movers: list = None) -> None:
    """Convenience wrapper called from main.py."""
    msg = format_game_summary(analysis, game_label, what_if=what_if, position_movers=position_movers)
    log.info("WhatsApp message preview:\n%s", msg)
    push_to_whatsapp(msg)


def notify_kickoff(bets: list[dict], game_label: str) -> None:
    """Send pre-game kickoff cluster message."""
    msg = format_kickoff_message(bets, game_label)
    log.info("Kickoff WhatsApp message preview:\n%s", msg)
    push_to_whatsapp(msg)
