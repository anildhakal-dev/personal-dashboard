# Weekly scorecard: one honest grade for your week (Monday to Sunday).
# Gym, protein, flashcards, deadlines, to-dos, trading discipline and spending.
# Alerts sends it to your phone on Sunday evening (see alerts.py).

from flask import Blueprint, render_template, request
from datetime import date, datetime, timedelta
import sqlite3

bp = Blueprint("scorecard", __name__)
DB = "dashboard.db"
PROTEIN_TARGET = 150


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()


VERDICTS = {
    "A": "Proper week. Do it again, no slacking because you had one good one.",
    "B": "Decent, not great. You know exactly where you slipped.",
    "C": "Average. Average doesn't get a 1st or a funded account.",
    "D": "Poor week. Stop making excuses and fix the red bits.",
    "F": "That was a write-off. Next week starts Monday. Don't waste it.",
}


def grade(score):
    return "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "F"


def week_card(conn, monday, today=None):
    """Works out the scorecard for the week starting `monday`."""
    today = today or date.today()
    sunday = monday + timedelta(days=6)
    last = min(sunday, today)                       # don't count days that haven't happened
    days_gone = max(1, (last - monday).days + 1)
    m, s = monday.isoformat(), sunday.isoformat()
    parts = []      # each: name, emoji, score (0-100 or None for info only), headline, detail, fix, url

    # Gym: sessions logged vs sessions planned
    plan = {r["day"]: r["workout"] for r in conn.execute("SELECT * FROM gym_plan")}
    planned = sum(1 for d in range(days_gone) if plan.get(d, "Rest") != "Rest")
    done = conn.execute("SELECT COUNT(DISTINCT day) FROM gym_log WHERE day BETWEEN ? AND ?", (m, s)).fetchone()[0]
    if planned:
        parts.append({"name": "Gym", "emoji": "🏋️", "score": min(100, 100 * done / planned),
                      "headline": f"{done} of {planned} sessions", "detail": "Counted from the sets you logged.",
                      "fix": f"Log every session. You hit {done} of {planned}.", "url": "/gym"})

    # Protein: days you hit the target
    eaten = {r["day"]: r["total"] for r in conn.execute(
        "SELECT day, SUM(protein) AS total FROM protein_log WHERE day BETWEEN ? AND ? GROUP BY day", (m, s))}
    hit = sum(1 for v in eaten.values() if v >= PROTEIN_TARGET)
    avg = round(sum(eaten.values()) / days_gone) if eaten else 0
    parts.append({"name": "Protein", "emoji": "🍗", "score": 100 * hit / days_gone,
                  "headline": f"{hit} of {days_gone} days on target", "detail": f"Average {avg}g a day (target {PROTEIN_TARGET}g).",
                  "fix": f"Hit {PROTEIN_TARGET}g every day: you averaged {avg}g. Meal prep on Sunday.", "url": "/gym"})

    # Flashcards: decks due this week that you passed
    if has_table(conn, "study_decks"):
        decks = conn.execute("SELECT * FROM study_decks WHERE substr(deadline, 1, 10) BETWEEN ? AND ?", (m, s)).fetchall()
        due_so_far = [d for d in decks if d["deadline"][:10] <= last.isoformat()]
        if due_so_far:
            passed = sum(1 for d in due_so_far if d["passed_at"])
            missed = [d["title"] for d in due_so_far if not d["passed_at"]]
            parts.append({"name": "Flashcards", "emoji": "🃏", "score": 100 * passed / len(due_so_far),
                          "headline": f"{passed} of {len(due_so_far)} decks passed",
                          "detail": ("Missed: " + ", ".join(missed)) if missed else "Every deck done on time.",
                          "fix": "Do decks the night they're made, not the night they're due.", "url": "/study"})

    # Uni deadlines due this week
    dls = conn.execute("SELECT * FROM deadlines WHERE substr(due, 1, 10) BETWEEN ? AND ?", (m, last.isoformat())).fetchall()
    if dls:
        done_dl = sum(1 for d in dls if d["done"])
        parts.append({"name": "Deadlines", "emoji": "⏳", "score": 100 * done_dl / len(dls),
                      "headline": f"{done_dl} of {len(dls)} handed in", "detail": ", ".join(d["title"] for d in dls),
                      "fix": "Start assignments the week they're set. Last-minute work doesn't get a 1st.", "url": "/uni"})

    # To-dos due this week, and anything overdue right now
    tasks = conn.execute("SELECT * FROM tasks WHERE due BETWEEN ? AND ?", (m, last.isoformat())).fetchall()
    overdue = conn.execute("SELECT COUNT(*) FROM tasks WHERE done = 0 AND due IS NOT NULL AND due != '' AND due < ?",
                           (today.isoformat(),)).fetchone()[0]
    if tasks:
        done_t = sum(1 for t in tasks if t["done"])
        parts.append({"name": "To-dos", "emoji": "📝", "score": 100 * done_t / len(tasks),
                      "headline": f"{done_t} of {len(tasks)} done", "detail": f"{overdue} overdue right now." if overdue else "Nothing overdue.",
                      "fix": f"Clear the {overdue} overdue to-dos before adding new ones." if overdue else "Finish what you put on the list.", "url": "/"})

    # Trading: discipline (followed your plan), P&L shown for info
    trades = conn.execute("SELECT * FROM trades WHERE day BETWEEN ? AND ?", (m, s)).fetchall()
    if trades:
        pnl = sum(t["pnl"] for t in trades)
        followed = sum(1 for t in trades if t["followed_plan"])
        parts.append({"name": "Trading discipline", "emoji": "📈", "score": 100 * followed / len(trades),
                      "headline": f"{followed} of {len(trades)} trades followed the plan",
                      "detail": f"P&L {'+' if pnl >= 0 else '-'}${abs(pnl):,.2f} from {len(trades)} trades.",
                      "fix": "Only take A+ setups. Every off-plan trade is gambling.", "url": "/trading"})

    # Spending (info only: from your uploaded bank statements)
    if has_table(conn, "transactions"):
        try:
            import finance
            spend_cats = finance.SPEND_CATEGORIES
        except Exception:
            spend_cats = None
        rows = conn.execute("SELECT category, amount FROM transactions WHERE day BETWEEN ? AND ? AND amount < 0", (m, s)).fetchall()
        rows = [r for r in rows if spend_cats is None or r["category"] in spend_cats]
        if rows:
            total = -sum(r["amount"] for r in rows)
            by = {}
            for r in rows:
                by[r["category"] or "Other"] = by.get(r["category"] or "Other", 0) - r["amount"]
            top = max(by.items(), key=lambda x: x[1])
            parts.append({"name": "Spending", "emoji": "💷", "score": None, "headline": f"£{total:,.0f} spent",
                          "detail": f"Biggest: {top[0]} £{top[1]:,.0f}.", "fix": "", "url": "/money"})

    # Did you track anything at all this week? (weeks before you started show as blank)
    tracked = bool(done or eaten or trades or tasks or dls) or any(p["name"] == "Flashcards" for p in parts)
    scored = [p for p in parts if p["score"] is not None]
    overall = round(sum(p["score"] for p in scored) / len(scored)) if scored else 0
    g = grade(overall)
    worst = min(scored, key=lambda p: p["score"]) if scored else None
    for p in parts:
        if p["score"] is not None:
            p["score"] = round(p["score"])
            p["light"] = "green" if p["score"] >= 85 else "amber" if p["score"] >= 55 else "red"
        else:
            p["light"] = "grey"
    return {"monday": monday, "sunday": sunday, "parts": parts, "score": overall, "grade": g,
            "verdict": VERDICTS[g], "fix": worst["fix"] if worst and worst["score"] < 85 else "Keep the standard. Raise one thing by 10%.",
            "worst": worst["name"] if worst else None, "current": monday <= today <= sunday, "tracked": tracked}


def push_text(card):
    """Short version for the Sunday notification."""
    bits = [f"{p['emoji']} {p['headline']}" for p in card["parts"] if p["score"] is not None][:4]
    return f"Week scorecard: {card['grade']} ({card['score']}%)", "\n".join(bits + [f"Fix: {card['fix']}"])


@bp.route("/scorecard")
def scorecard_page():
    back = max(0, request.args.get("w", 0, type=int))     # 0 = this week, 1 = last week...
    today = date.today()
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=back)
    conn = get_db()
    card = week_card(conn, monday, today)
    history = []
    for i in range(1, 5):
        mon = today - timedelta(days=today.weekday()) - timedelta(weeks=i)
        c = week_card(conn, mon, today)
        history.append({"w": i, "label": mon.strftime("%d %b"), "grade": c["grade"] if c["tracked"] else "–",
                        "score": f"{c['score']}%" if c["tracked"] else "nothing logged"})
    conn.close()
    return render_template("scorecard.html", card=card, back=back, history=history)
