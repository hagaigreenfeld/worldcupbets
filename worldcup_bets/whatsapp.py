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


def format_game_summary(analysis: dict, game_label: str, what_if: dict = None, position_movers: list = None, bonus_bets: list = None) -> str:
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

    # Bonus players in this match
    active_bonus = bonus_for_teams(bonus_bets or [], team1, team2)
    if active_bonus:
        lines.append("⭐ *בונוס שחקן במשחק הזה:*")
        for b in active_bonus:
            lines.append(f"  {b['nickname']} — {b['player']} ({b['team_he']}) +2 לכל שער")
        lines.append("")

    # What-if scenario
    if what_if:
        block = format_what_if(what_if, team1, team2, is_final=is_final, bonus_bets=active_bonus)
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


def rtl_score(score: str) -> str:
    """Flip 'team1:team2' to 'team2:team1' for correct RTL display in WhatsApp."""
    parts = score.split(":")
    return ":".join(reversed(parts)) if len(parts) == 2 else score


STATUS_EMOJI = {"exact": "🎯", "correct": "✅", "wrong": "❌"}
STATUS_HE    = {"exact": "ניחוש מדויק", "correct": "כיוון נכון", "wrong": "טעות"}


def bonus_for_teams(bonus_bets: list[dict], team1_he: str, team2_he: str) -> list[dict]:
    """Return bonus bets whose player's team matches team1 or team2 (Hebrew names)."""
    result = []
    for b in bonus_bets:
        if not b.get("player") or not b.get("team_he"):
            continue
        if b["team_he"] in (team1_he, team2_he):
            result.append({**b, "side": "team1" if b["team_he"] == team1_he else "team2"})
    return result


def format_what_if(what_if: dict, team1: str, team2: str, is_final: bool = False, bonus_bets: list = None) -> str:
    """Format the what-if next-goal analysis block."""
    if not what_if or "if_team1" not in what_if:
        return ""

    title = "🔮 *מה היה קורה אם... (כמה קרוב היה זה ;)*" if is_final else "🔮 *מה יקרה אם...*"
    lines = ["", title]

    bonus_team1 = [b for b in (bonus_bets or []) if b.get("side") == "team1"]
    bonus_team2 = [b for b in (bonus_bets or []) if b.get("side") == "team2"]

    for side, team_name, bonus_side in [("if_team1", team1, bonus_team1), ("if_team2", team2, bonus_team2)]:
        scenario = what_if.get(side, {})
        score    = scenario.get("score", "")
        changes  = scenario.get("changes", [])

        rtl_score = ":".join(reversed(score.split(":"))) if ":" in score else score
        lines.append(f"  ⚽ *{team_name} תבקיע ({rtl_score}):*")

        if not changes and not bonus_side:
            lines.append("    — אין שינויים בניחושים")
        else:
            exact_winners = [c for c in changes if c["to"] == "exact"]
            dir_gainers   = [c for c in changes if c["to"] == "correct" and c["from"] != "exact"]
            loses_exact   = [c for c in changes if c["from"] == "exact" and c["to"] != "exact"]
            loses_all     = [c for c in changes if c["to"] == "wrong"]

            if exact_winners:
                lines.append(f"    🎯 ניחוש מדויק: {', '.join(nickname(c['player']) for c in exact_winners)}")
            if dir_gainers:
                lines.append(f"    ✅ מרוויחים כיוון: {', '.join(nickname(c['player']) for c in dir_gainers)}")
            if loses_exact:
                lines.append(f"    💔 מאבדים ניחוש מדויק: {', '.join(nickname(c['player']) for c in loses_exact)}")
            if loses_all:
                lines.append(f"    ❌ מפסידים: {', '.join(nickname(c['player']) for c in loses_all)}")
            if bonus_side:
                for b in bonus_side:
                    lines.append(f"    ⭐ +2 בונוס: {b['nickname']} (הימר על {b['player']})")

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


def format_kickoff_message(bets: list[dict], game_label: str, bonus_bets: list = None) -> str:
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

    def fmt_pts(v) -> str:
        f = float(v) if v else 0
        return str(int(f)) if f == int(f) else str(f)

    # Sort clusters by number of pickers desc, then by score string
    sorted_scores = sorted(score_clusters.items(), key=lambda x: (-len(x[1]), x[0]))
    if sorted_scores:
        for score, players in sorted_scores:
            names = ", ".join(p["name"] for p in players)
            pot   = fmt_pts(players[0]["pot"])
            lines.append(f"  *{rtl_score(score)}* — {names} ({pot} נק')")
    else:
        lines.append("  אין הימורי תוצאה")

    if no_bet:
        lines += ["", f"😭 *הידעת ולא הימרת?!* {', '.join(no_bet)}"]

    # Bonus players active in this match
    active_bonus = bonus_for_teams(bonus_bets or [], team1, team2)
    if active_bonus:
        lines.append("")
        lines.append("⭐ *שחקני בונוס במשחק:*")
        for b in active_bonus:
            lines.append(f"  {b['nickname']} מהמר על {b['player']} ({b['team_he']}) — +2 נק' לכל שער")

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


def notify(analysis: dict, game_label: str, what_if: dict = None, position_movers: list = None, bonus_bets: list = None) -> None:
    """Convenience wrapper called from main.py."""
    msg = format_game_summary(analysis, game_label, what_if=what_if, position_movers=position_movers, bonus_bets=bonus_bets)
    log.info("WhatsApp message preview:\n%s", msg)
    push_to_whatsapp(msg)


def notify_kickoff(bets: list[dict], game_label: str, bonus_bets: list = None) -> None:
    """Send pre-game kickoff cluster message."""
    msg = format_kickoff_message(bets, game_label, bonus_bets=bonus_bets)
    log.info("Kickoff WhatsApp message preview:\n%s", msg)
    push_to_whatsapp(msg)
