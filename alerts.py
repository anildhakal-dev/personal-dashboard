# Alerts: everything you need to do right now, on one page, plus notifications on your phone.
#   /alerts            - the list (overdue, today, coming up) and the notification settings
#   /alerts/run?key=.. - cron-job.org calls this every 15 minutes; it decides what to send
#
# Notifications use "web push": the phone app (Add to Home Screen) asks permission once,
# then the dashboard can send to it even when the app is closed.

from flask import Blueprint, render_template, request, redirect, jsonify, send_from_directory
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import sqlite3
import secrets
import random
import json

bp = Blueprint("alerts", __name__)
DB = "dashboard.db"
UK = ZoneInfo("Europe/London")
PROTEIN_TARGET = 150


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def uk_now():
    """The time in the UK (the server runs on UTC, which is an hour out in summer)."""
    return datetime.now(UK).replace(tzinfo=None)


# ---------- Tables ----------

def setup_alerts():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS push_subs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT UNIQUE NOT NULL,
            data TEXT NOT NULL,              -- the phone's subscription (JSON)
            device TEXT,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alert_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS alert_log (     -- what was sent, so nothing is sent twice
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            title TEXT,
            body TEXT,
            sent_at TEXT
        );
    """)
    defaults = {"morning": "08:00", "evening": "20:30", "before_class": "60",
                "cron_key": secrets.token_urlsafe(18)}
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO alert_settings (key, value) VALUES (?, ?)", (k, v))
    # The key pair that proves notifications come from your dashboard (made once)
    if not conn.execute("SELECT 1 FROM alert_settings WHERE key = 'vapid_private'").fetchone():
        from py_vapid import Vapid01
        v = Vapid01()
        v.generate_keys()
        conn.execute("INSERT INTO alert_settings (key, value) VALUES ('vapid_private', ?)",
                     (v.private_pem().decode(),))
    conn.commit()
    conn.close()


def setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM alert_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def public_key(conn):
    """The public half of the key pair, in the format the browser wants."""
    from py_vapid import Vapid01
    from py_vapid.utils import b64urlencode
    from cryptography.hazmat.primitives import serialization
    v = Vapid01.from_pem(setting(conn, "vapid_private").encode())
    raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                    serialization.PublicFormat.UncompressedPoint)
    return b64urlencode(raw)


# ---------- What needs doing ----------

def table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()


def days_word(n):
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


def collect(conn, now=None):
    """Everything that needs your attention, most urgent first.
    level: red (sort it now), amber (today / soon), gold (coming up)."""
    now = now or uk_now()
    today = now.date()
    items = []

    def add(level, emoji, title, body, url, key):
        items.append({"level": level, "emoji": emoji, "title": title, "body": body, "url": url, "key": key})

    # 1. To-dos
    for t in conn.execute("SELECT * FROM tasks WHERE done = 0 AND due IS NOT NULL AND due != ''"):
        try:
            due = date.fromisoformat(t["due"])
        except ValueError:
            continue
        late = (today - due).days
        if late > 0:
            add("red", "🔥", t["title"], f"Overdue by {late} day{'s' if late > 1 else ''}. Sort it out.", "/", f"task{t['id']}")
        elif late == 0:
            add("amber", "📝", t["title"], "Due today.", "/", f"task{t['id']}")
        elif late == -1:
            add("gold", "📝", t["title"], "Due tomorrow.", "/", f"task{t['id']}")

    # 2. Uni deadlines and exams (next 14 days)
    for d in conn.execute("SELECT * FROM deadlines WHERE done = 0"):
        try:
            due = date.fromisoformat(d["due"][:10])
        except ValueError:
            continue
        left = (due - today).days
        what = f"{d['kind'] or 'Deadline'}{' · ' + d['module'] if d['module'] else ''}"
        if left < 0:
            add("red", "⏳", d["title"], f"{what}. It was due {-left} day{'s' if left < -1 else ''} ago.", "/uni", f"dl{d['id']}")
        elif left <= 3:
            add("red", "⏳", d["title"], f"{what}. Due {days_word(left)}.", "/uni", f"dl{d['id']}")
        elif left <= 7:
            add("amber", "⏳", d["title"], f"{what}. Due {days_word(left)}.", "/uni", f"dl{d['id']}")
        elif left <= 14:
            add("gold", "⏳", d["title"], f"{what}. Due {days_word(left)}.", "/uni", f"dl{d['id']}")

    # 3. Flashcard decks not passed yet (deadline within 2 days, or missed)
    if table_exists(conn, "study_decks"):
        for k in conn.execute("SELECT * FROM study_decks WHERE passed_at IS NULL AND deadline IS NOT NULL"):
            try:
                due = datetime.strptime(k["deadline"], "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            hours = (due - now).total_seconds() / 3600
            when = due.strftime("%a %H:%M").replace(" 21:00", " 9pm")
            if hours < 0:
                add("red", "🃏", f"{k['title']} flashcards", f"{k['module']}. Missed the deadline. Do them anyway.", f"/study/deck/{k['id']}", f"deck{k['id']}")
            elif hours <= 24:
                add("red", "🃏", f"{k['title']} flashcards", f"{k['module']}. Due {when}, {k['pass_mark']}% to pass.", f"/study/deck/{k['id']}", f"deck{k['id']}")
            elif hours <= 48:
                add("amber", "🃏", f"{k['title']} flashcards", f"{k['module']}. Due {when}.", f"/study/deck/{k['id']}", f"deck{k['id']}")

    # 4. Today's timetable and calendar (what's still to come)
    for it in today_schedule(conn, today):
        if it["start"] >= now.strftime("%H:%M"):
            add("amber", it["emoji"] or "📅", f"{it['start']} {it['title']}", it["sub"] or "On your calendar today.", "/calendar",
                f"ev{today}{it['start']}{it['title']}")

    # 5. Gym and protein (only once the day is getting on)
    plan = conn.execute("SELECT workout FROM gym_plan WHERE day = ?", (today.weekday(),)).fetchone()
    workout = plan["workout"] if plan else "Rest"
    if workout != "Rest" and now.hour >= 16:
        logged = conn.execute("SELECT COUNT(*) FROM gym_log WHERE day = ?", (today.isoformat(),)).fetchone()[0]
        if not logged:
            add("amber", "🏋️", f"{workout} day", "Nothing logged yet. Get it done.", "/gym", "gym")
    if now.hour >= 18:
        eaten = conn.execute("SELECT COALESCE(SUM(protein), 0) FROM protein_log WHERE day = ?", (today.isoformat(),)).fetchone()[0]
        if eaten < PROTEIN_TARGET:
            add("amber", "🍗", "Protein", f"{eaten}g so far. {PROTEIN_TARGET - eaten}g to go.", "/gym", "protein")

    order = {"red": 0, "amber": 1, "gold": 2}
    items.sort(key=lambda x: order[x["level"]])
    return items


def today_schedule(conn, day):
    """Classes, calendar events and commutes for one day (from the Calendar page)."""
    try:
        import planner
        monday = day - timedelta(days=day.weekday())
        return sorted(planner.week_items(conn, monday)[day.weekday()], key=lambda x: x["start"])
    except Exception:
        return []


# ---------- Harsh wording ----------

MORNING = [
    "Up. You've got shit to do.",
    "Morning. No excuses today.",
    "Get your head down today.",
    "Today's list. Don't waste it.",
    "Nobody's doing this for you.",
]
EVENING = [
    "Before you switch off...",
    "Day's not done yet.",
    "Finish what you started.",
    "Still on the list. Sort it.",
]
NUDGE = ["Move.", "Get going.", "Don't be late.", "Shift."]


def summary(items):
    reds = [i for i in items if i["level"] == "red"]
    ambers = [i for i in items if i["level"] == "amber"]
    lines = [f"{i['emoji']} {i['title']}" for i in (reds + ambers)[:4]]
    extra = len(reds) + len(ambers) - len(lines)
    if extra > 0:
        lines.append(f"+{extra} more")
    head = f"{len(reds)} urgent, {len(ambers)} today" if reds else f"{len(ambers)} things today"
    return head, "\n".join(lines)


# ---------- Sending ----------

def send_push(conn, title, body, url="/alerts", tag=None):
    """Sends one notification to every phone/computer that turned them on.
    Returns (how many it reached, list of errors)."""
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid02     # the modern format; works with iPhone, Android and desktop
    vapid = Vapid02.from_pem(setting(conn, "vapid_private").encode())
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag or title})
    ok, errors = 0, []
    for s in conn.execute("SELECT * FROM push_subs").fetchall():
        try:
            webpush(json.loads(s["data"]), data=payload, vapid_private_key=vapid,
                    vapid_claims={"sub": "mailto:alerts@" + (request.host.split(":")[0] if request else "dashboard.app")},
                    ttl=6 * 3600, timeout=15)
            ok += 1
        except WebPushException as e:
            code = e.response.status_code if e.response is not None else None
            if code in (404, 410):   # that phone turned notifications off: forget it
                conn.execute("DELETE FROM push_subs WHERE id = ?", (s["id"],))
            else:
                errors.append(f"{s['device'] or 'device'}: {code or ''} {str(e)[:160]}")
        except Exception as e:
            errors.append(f"{s['device'] or 'device'}: {str(e)[:160]}")
    conn.commit()
    return ok, errors


def send_once(conn, key, title, body, url="/alerts"):
    """Sends unless this exact alert has already gone out."""
    if conn.execute("SELECT 1 FROM alert_log WHERE key = ?", (key,)).fetchone():
        return False
    ok, errors = send_push(conn, title, body, url, tag=key)
    if not ok:          # nothing reached a phone: try again on the next run
        return False
    conn.execute("INSERT INTO alert_log (key, title, body, sent_at) VALUES (?, ?, ?, ?)",
                 (key, title, body, uk_now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    return True


def run_alerts(conn):
    """Called every 15 minutes. Sends the morning and evening round-ups,
    and a nudge before each class or calendar event."""
    now = uk_now()
    today = now.date().isoformat()
    hhmm = now.strftime("%H:%M")
    sent = []

    def in_window(start):   # true for the first 2 hours after the set time
        try:
            t = datetime.strptime(f"{today} {start}", "%Y-%m-%d %H:%M")
        except ValueError:
            return False
        return t <= now < t + timedelta(hours=2)

    items = collect(conn, now)

    if in_window(setting(conn, "morning", "08:00")) and items:
        head, body = summary(items)
        if send_once(conn, f"morning:{today}", f"{random.choice(MORNING)} {head}", body):
            sent.append("morning")

    evening_items = [i for i in items if i["level"] != "gold" and not i["key"].startswith("ev")]
    if in_window(setting(conn, "evening", "20:30")) and evening_items:
        head, body = summary(evening_items)
        if send_once(conn, f"evening:{today}", f"{random.choice(EVENING)} {head}", body):
            sent.append("evening")

    # Nudge before each class / event (default 60 minutes before), 7am-10pm only
    before = int(setting(conn, "before_class", "60") or 0)
    if before and 7 <= now.hour < 22:
        for it in today_schedule(conn, now.date()):
            if it["kind"] == "auto":
                continue
            start = datetime.strptime(f"{today} {it['start']}", "%Y-%m-%d %H:%M")
            mins = (start - now).total_seconds() / 60
            if 0 < mins <= before:
                key = f"ev:{today}:{it['start']}:{it['title']}"
                body = f"{it['sub'] + '. ' if it['sub'] else ''}{random.choice(NUDGE)}"
                if send_once(conn, key, f"{it['emoji']} {it['title']} at {it['start']}", body, "/calendar"):
                    sent.append(it["title"])

    conn.execute("INSERT OR REPLACE INTO alert_settings (key, value) VALUES ('last_run', ?)", (now.strftime("%Y-%m-%d %H:%M"),))
    # Keep the log short
    conn.execute("DELETE FROM alert_log WHERE sent_at < ?", ((now - timedelta(days=30)).strftime("%Y-%m-%d"),))
    conn.commit()
    return sent


# ---------- Pages ----------

HEADLINES = [
    "Here's what you're avoiding.",
    "This is what needs doing. No excuses.",
    "Your list. Stop staring and start.",
    "Sort these out before anything else.",
    "Less scrolling, more of this.",
]


@bp.route("/alerts")
def alerts_page():
    conn = get_db()
    items = collect(conn)
    groups = [
        ("🔴 Sort it now", [i for i in items if i["level"] == "red"]),
        ("🟠 Today", [i for i in items if i["level"] == "amber"]),
        ("🟡 Coming up", [i for i in items if i["level"] == "gold"]),
    ]
    info = {
        "public_key": public_key(conn),
        "devices": conn.execute("SELECT * FROM push_subs ORDER BY id").fetchall(),
        "morning": setting(conn, "morning"), "evening": setting(conn, "evening"),
        "before_class": setting(conn, "before_class"), "last_run": setting(conn, "last_run"),
        "cron_url": f"{request.host_url.replace('http://', 'https://') if 'pythonanywhere' in request.host else request.host_url}alerts/run?key={setting(conn, 'cron_key')}",
        "sent": conn.execute("SELECT * FROM alert_log ORDER BY id DESC LIMIT 8").fetchall(),
    }
    conn.close()
    return render_template("alerts.html", groups=groups, total=len(items), info=info,
                           headline=random.choice(HEADLINES), saved=request.args.get("saved"))


@bp.route("/sw.js")
def service_worker():
    """The background worker that shows notifications. Served from the top of the site
    so it covers every page (iPhone needs this)."""
    resp = send_from_directory("static", "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.route("/alerts/subscribe", methods=["POST"])
def subscribe():
    sub = request.get_json(silent=True) or {}
    if not sub.get("endpoint"):
        return jsonify(ok=False, error="No subscription"), 400
    device = (sub.pop("device", "") or "")[:60]
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO push_subs (endpoint, data, device) VALUES (?, ?, ?)",
                 (sub["endpoint"], json.dumps(sub), device))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@bp.route("/alerts/device/<int:sub_id>/delete", methods=["POST"])
def delete_device(sub_id):
    conn = get_db()
    conn.execute("DELETE FROM push_subs WHERE id = ?", (sub_id,))
    conn.commit()
    conn.close()
    return redirect("/alerts")


@bp.route("/alerts/test", methods=["POST"])
def test_push():
    conn = get_db()
    items = collect(conn)
    head, body = summary(items) if items else ("Nothing urgent", "Enjoy it while it lasts.")
    ok, errors = send_push(conn, f"Test: {head}", body or "You're clear. For now.", "/alerts", tag="test")
    conn.close()
    return jsonify(ok=ok, errors=errors)


@bp.route("/alerts/settings", methods=["POST"])
def save_settings():
    conn = get_db()
    for k in ("morning", "evening", "before_class"):
        v = request.form.get(k, "").strip()
        if v:
            conn.execute("INSERT OR REPLACE INTO alert_settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()
    return redirect("/alerts?saved=1")


@bp.route("/alerts/run")
def run():
    """cron-job.org opens this every 15 minutes. Needs the secret key from the Alerts page."""
    conn = get_db()
    if request.args.get("key") != setting(conn, "cron_key"):
        conn.close()
        return "Not allowed", 403
    sent = run_alerts(conn)
    conn.close()
    return "OK " + (", ".join(sent) if sent else "nothing to send")


# The red number next to Alerts in the menu, on every page
@bp.app_context_processor
def alert_badge():
    if not request.path or request.path.startswith(("/static", "/login", "/alerts/run")):
        return {}
    try:
        conn = get_db()
        n = sum(1 for i in collect(conn) if i["level"] == "red")
        conn.close()
        return {"alert_count": n}
    except Exception:
        return {"alert_count": 0}


setup_alerts()
