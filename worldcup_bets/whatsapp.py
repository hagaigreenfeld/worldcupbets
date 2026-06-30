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


def fmt_pts(v) -> str:
    """Format points: int when whole (10), float otherwise (7.5)."""
    try:
        f = float(v)
    except (ValueError, TypeError):
        return str(v)
    return str(int(f)) if f == int(f) else str(f)


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
            pts   = fmt_pts(players[0]["pts"])
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
            pts   = fmt_pts(players[0]["pts"])
            lines.append(f"  {label} — {names} ({pts} נק')")

    lines.append("")

    # Wrong bets
    wrong = [nickname(b["player_name"]) for b in analysis.get("enriched_bets", [])
             if b.get("result_status", "").startswith("❌")]
    if wrong:
        lines.append(f"❌ *טעו:* {', '.join(wrong)}")
        lines.append("")

    # Funny bets
    if is_final:
        funny = find_funniest_bets(analysis.get("enriched_bets", []), result)
        if funny:
            lines.append("🌀 *אולי ביקום אחר...*")
            for f in funny:
                names_str = ", ".join(f["names"])
                lines.append(f"  🤡 {names_str} — {f['note']}")
            lines.append("")

    # Ruined by last goal
    if is_final:
        ruined = find_ruined_by_last_goal(analysis.get("enriched_bets", []), result)
        if ruined:
            lines.append("💔 *נשבר בריבר:*")
            for r in ruined:
                lines.append(f"  {nickname(r['name'])} ({rtl_score(r['guess'])})")
            lines.append("")

    # No bet
    if no_bet:
        lines.append(f"😭 *הידעת ולא הימרת?!* {', '.join(no_bet)}")
        lines.append("")

    # Bonus players in this match
    active_bonus = bonus_for_teams(bonus_bets or [], team1, team2)
    if active_bonus:
        lines.append("⭐ *בונוס שחקן במשחק הזה:*")
        by_player: dict[str, list] = {}
        for b in active_bonus:
            key = (b["player"], b["team_he"])
            by_player.setdefault(key, []).append(b["nickname"])
        for (player, team_he), nicknames in by_player.items():
            lines.append(f"  {player} ({team_he}) +2 נק' לכל שער: {', '.join(nicknames)}")
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
        lines.append(f"{medal} {nickname(r['name'])} — {fmt_pts(r['points'])} נק'{delta_str}")

    if len(board) > 10:
        lines.append(f"...ועוד {len(board) - 10} שחקנים")

    return "\n".join(lines)


def find_funniest_bets(enriched_bets: list[dict], actual_result: str) -> list[dict]:
    """
    Return zero or more funny-bet entries worth highlighting:
      - Furthest score: highest Manhattan distance |g1-r1|+|g2-r2| among wrong bets
      - Lone underdog: the only person(s) who bet on the losing side
    Each entry: {type, names, guess, note}
    """
    if not actual_result or ":" not in actual_result:
        return []
    try:
        r1, r2 = int(actual_result.split(":")[0]), int(actual_result.split(":")[1])
    except (ValueError, TypeError, IndexError):
        return []

    wrong = [b for b in enriched_bets if b.get("result_status", "").startswith("❌")]
    if not wrong:
        return []

    results = []

    # ── Furthest score ──────────────────────────────────────────────────────
    scored_bets = []
    for b in wrong:
        sg = (b.get("score_guess") or "").strip()
        if not sg or ":" not in sg:
            continue
        try:
            g1, g2 = int(sg.split(":")[0]), int(sg.split(":")[1])
        except (ValueError, TypeError):
            continue
        dist = abs(g1 - r1) + abs(g2 - r2)
        scored_bets.append((dist, b.get("player_name", "?"), sg))

    if scored_bets:
        max_dist = max(d for d, _, _ in scored_bets)
        if max_dist >= 3:  # only funny if meaningfully far
            furthest = [(n, sg) for d, n, sg in scored_bets if d == max_dist]
            names = [nickname(n) for n, _ in furthest]
            guess = furthest[0][1]
            results.append({
                "type":  "far",
                "names": names,
                "guess": guess,
                "note":  f"ניחשו {rtl_score(guess)} 🎲",
            })

    # ── Lone underdog ───────────────────────────────────────────────────────
    # Determine the actual winner side
    if r1 > r2:
        winner_side, loser_side = "team1", "team2"
    elif r2 > r1:
        winner_side, loser_side = "team2", "team1"
    else:
        winner_side, loser_side = "draw", None  # draw can't have a lone underdog

    if loser_side:
        all_bets_with_dir = [b for b in enriched_bets if b.get("guess_winner") not in ("N/A", "", None)]
        loser_bettors = [b for b in all_bets_with_dir if b.get("guess_winner") == loser_side]
        total_bettors = len(all_bets_with_dir)
        if 1 <= len(loser_bettors) <= 2 and total_bettors >= 5:
            names = [nickname(b["player_name"]) for b in loser_bettors]
            guesses = [b.get("score_guess") or b.get("guess_winner", "") for b in loser_bettors]
            results.append({
                "type":  "lone",
                "names": names,
                "guess": guesses[0],
                "note":  f"{'היחיד' if len(names) == 1 else 'היחידים'} שהימרו על הקבוצה שהפסידה 🦁",
            })

    return results


def find_ruined_by_last_goal(enriched_bets: list[dict], actual_result: str) -> list[dict]:
    """
    Return dicts {name, guess} whose exact bet matched a score that existed during the game
    but was broken by the final goal.
    Heuristic: bet = final_score minus one goal by either team
    (i.e., the score WAS their bet, then the last goal broke it).
    """
    if not actual_result or ":" not in actual_result:
        return []
    try:
        r1, r2 = int(actual_result.split(":")[0]), int(actual_result.split(":")[1])
    except (ValueError, TypeError, IndexError):
        return []

    ruined = []
    for bet in enriched_bets:
        sg = (bet.get("score_guess") or "").strip()
        if not sg or ":" not in sg:
            continue
        if bet.get("result_status", "").startswith("🎯"):
            continue
        try:
            g1, g2 = int(sg.split(":")[0]), int(sg.split(":")[1])
        except (ValueError, TypeError, IndexError):
            continue
        if g1 == r1 and g2 == r2 - 1 and r2 > 0:
            ruined.append({"name": bet.get("player_name", "?"), "guess": sg})
        elif g2 == r2 and g1 == r1 - 1 and r1 > 0:
            ruined.append({"name": bet.get("player_name", "?"), "guess": sg})
    return ruined


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
            if bonus_side and not is_final:
                all_names = ", ".join(b["nickname"] for b in bonus_side)
                lines.append(f"    ⭐ +2 בונוס: {all_names}")

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

    # Sort clusters by number of pickers desc, then by score string
    sorted_scores = sorted(score_clusters.items(), key=lambda x: (-len(x[1]), x[0]))
    if sorted_scores:
        for score, players in sorted_scores:
            names   = ", ".join(p["name"] for p in players)
            pot_val = max((p["pot"] for p in players), default=0)
            pot     = fmt_pts(pot_val) if pot_val else "?"
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
        by_player: dict[str, list] = {}
        for b in active_bonus:
            key = (b["player"], b["team_he"])
            by_player.setdefault(key, []).append(b["nickname"])
        for (player, team_he), nicknames in by_player.items():
            lines.append(f"  {player} ({team_he}): {', '.join(nicknames)}")

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


_OUTCOME_NAME = {
    "team1": lambda t1, t2: t1,
    "draw":  lambda t1, t2: "תיקו",
    "team2": lambda t1, t2: t2,
}


def format_coming_up_message(game_analyses: list[dict]) -> str:
    """
    'Coming up next' message: next 2-4 games with odds, max points per outcome,
    and who can jump spots if the underdog wins.
    """
    lines = ["🔭 *המשחקים הבאים*", ""]

    for game in game_analyses:
        team1  = game["team1"]
        team2  = game["team2"]
        label  = game["label"]
        r1     = game["ratio1"]
        r2     = game["ratio2"]
        r3     = game["ratio3"]
        p1     = int(game["max_pts_team1"])
        pd     = int(game["max_pts_draw"])
        p2     = int(game["max_pts_team2"])
        upset  = game["upset_outcome"]
        ks     = game.get("kickoff_str", "")

        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(f"⚽ *{label}*")
        if ks:
            lines.append(f"🕐 {ks} UTC")
        lines.append("")

        # Odds table — mark upset with 💥
        def odds_line(outcome, ratio, pts, team_lbl):
            marker = " 💥" if outcome == upset else ""
            return f"  {team_lbl}: ×{ratio:.1f} → עד *{pts} נק'*{marker}"

        lines.append("💰 *תגמול מקסימלי לניחוש מדויק:*")
        lines.append(odds_line("team1", r1, p1, f"נצחון {team1}"))
        lines.append(odds_line("draw",  r2, pd, "תיקו"))
        lines.append(odds_line("team2", r3, p2, f"נצחון {team2}"))

        # Overtake opportunities
        opps = game.get("overtake_opps", [])
        if opps:
            lines.append("")
            lines.append("🚀 *פוטנציאל לשינוי טבלה:*")
            for opp in opps[:6]:
                player      = nickname(opp["player"])
                rank        = opp["current_rank"]
                above       = nickname(opp["above_player"])
                gap         = opp["gap_to_above"]
                new_rank    = opp["would_reach_rank"]
                places      = opp["places_gained"]
                upset_pts   = opp["upset_max_pts"]
                upset_name  = _OUTCOME_NAME[opp["upset_outcome"]](team1, team2)

                # Bet status tag
                if opp["on_upset"]:
                    bet_tag = " ✅ כבר הימר!"
                elif opp["has_bet"]:
                    guess_name = _OUTCOME_NAME.get(opp["their_guess"], lambda a, b: opp["their_guess"])(team1, team2)
                    bet_tag = f" (הימר {guess_name})"
                else:
                    bet_tag = " ⏰ טרם הימר"

                jump_str = f"מקום *{new_rank}*" if places > 1 else f"עולה על *{above}*"
                lines.append(
                    f"  *{player}* (מקום {rank}, פער {gap:.0f} נק')"
                    f" → הפתעת {upset_name} = +{upset_pts:.0f} נק' → {jump_str}! 🔥{bet_tag}"
                )

        lines.append("")

    lines.append("🍿 *בהצלחה לכולם!*")
    return "\n".join(lines)


def notify_coming_up(game_analyses: list[dict]) -> None:
    """Send coming-up-next — one WhatsApp message per game to stay under the 1600-char limit."""
    header = "🔭 *המשחקים הבאים*\n"
    for i, game in enumerate(game_analyses):
        msg = format_coming_up_message([game])
        if i == 0:
            msg = header + "\n" + msg
        log.info("Coming-up message %d/%d preview:\n%s", i + 1, len(game_analyses), msg)
        push_to_whatsapp(msg)
        if i < len(game_analyses) - 1:
            import time as _time
            _time.sleep(1)


def notify_kickoff(bets: list[dict], game_label: str, bonus_bets: list = None) -> None:
    """Send pre-game kickoff cluster message."""
    msg = format_kickoff_message(bets, game_label, bonus_bets=bonus_bets)
    log.info("Kickoff WhatsApp message preview:\n%s", msg)
    push_to_whatsapp(msg)
