"""
whatsapp.py — format and push game summary to WhatsApp via the Cloudflare Worker.

Called from main.py after analyze() completes.
The worker handles Twilio delivery; we just POST JSON to /push.
"""

import os
import json
import logging
import requests

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


def push_to_whatsapp(message: str, worker_url: str | None = None, secret: str | None = None) -> bool:
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


def notify(analysis: dict, game_label: str) -> None:
    """Convenience wrapper called from main.py."""
    msg = format_game_summary(analysis, game_label)
    log.info("WhatsApp message preview:\n%s", msg)
    push_to_whatsapp(msg)
