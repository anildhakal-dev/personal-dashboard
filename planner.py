# Calendar page: a Google-Calendar-style week view.
# Shows your uni timetable automatically, plus anything you add yourself.
# "Presets" are things you do often (commute, gym, shift...) with how long they take,
# so adding one only needs a start time.

from flask import Blueprint, render_template, request, redirect
import sqlite3
from datetime import date, datetime, timedelta

bp = Blueprint("planner", __name__)
DB = "dashboard.db"

START_HOUR = 6     # top of the calendar (6am)
END_HOUR = 24      # bottom of the calendar (midnight)
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
UNI_COLOUR = "#8b5cf6"


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


# Creates the calendar tables the first time, with some starter presets
def setup_calendar():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            minutes INTEGER NOT NULL,
            colour TEXT DEFAULT '#22d3ee',
            emoji TEXT DEFAULT ''
        )
    """)
    # repeat is 'none' or 'weekly' (weekly = same weekday every week from that date)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            day TEXT NOT NULL,
            start TEXT NOT NULL,
            end TEXT NOT NULL,
            colour TEXT DEFAULT '#22d3ee',
            emoji TEXT DEFAULT '',
            repeat TEXT DEFAULT 'none'
        )
    """)
    # Weeks where a repeating event is skipped (e.g. no gym this Tuesday)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_skips (
            event_id INTEGER NOT NULL,
            day TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_commute', '1')")

    if conn.execute("SELECT COUNT(*) FROM presets").fetchone()[0] == 0:
        conn.executemany("INSERT INTO presets (name, minutes, colour, emoji) VALUES (?, ?, ?, ?)", [
            ("Commute to Lincoln", 50, "#64748b", "🚆"),
            ("Commute home", 50, "#64748b", "🏠"),
            ("Gym", 90, "#34d399", "🏋️"),
            ("Boxing", 60, "#f87171", "🥊"),
            ("Restaurant shift", 480, "#fb923c", "🍛"),
            ("Trading (NY session)", 180, "#22d3ee", "📈"),
            ("Coding / business", 150, "#ec4899", "💻"),
            ("Study", 120, "#a78bfa", "📚"),
            ("Meal prep", 60, "#facc15", "🥗"),
        ])
    conn.commit()
    conn.close()


# "09:30" -> 570 minutes after midnight, and back again
def to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes):
    minutes = max(0, min(minutes, 24 * 60 - 1))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def nice_length(minutes):
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


# Everything happening on each day of the week that starts on `monday`
def week_items(conn, monday):
    sunday = monday + timedelta(days=6)
    days = {i: [] for i in range(7)}

    # 1. Uni classes (they repeat every week)
    for c in conn.execute("SELECT * FROM classes"):
        days[c["day"]].append({
            "kind": "uni", "title": c["module"], "emoji": "🎓",
            "sub": f"{c['kind'] or ''}{' · ' + c['room'] if c['room'] else ''}",
            "start": c["start"], "end": c["end"], "colour": UNI_COLOUR,
        })

    # 2. Your own events: one-offs in this week, and weekly ones that have started
    skips = {(s["event_id"], s["day"]) for s in conn.execute("SELECT * FROM event_skips")}
    for e in conn.execute("SELECT * FROM events WHERE day <= ?", (sunday.isoformat(),)):
        first = date.fromisoformat(e["day"])
        if e["repeat"] == "weekly":
            on = monday + timedelta(days=first.weekday())
            if on < first:
                continue
        else:
            on = first
            if on < monday:
                continue
        if (e["id"], on.isoformat()) in skips:
            continue
        days[on.weekday()].append({
            "kind": "event", "id": e["id"], "title": e["title"], "emoji": e["emoji"] or "",
            "sub": "repeats weekly" if e["repeat"] == "weekly" else "",
            "start": e["start"], "end": e["end"], "colour": e["colour"],
            "repeat": e["repeat"], "date": on.isoformat(), "first_day": e["day"],
        })

    # 3. Automatic commute before your first class and after your last one
    auto = conn.execute("SELECT value FROM settings WHERE key = 'auto_commute'").fetchone()
    if auto and auto["value"] == "1":
        go = conn.execute("SELECT * FROM presets WHERE name = 'Commute to Lincoln'").fetchone()
        back = conn.execute("SELECT * FROM presets WHERE name = 'Commute home'").fetchone()
        for i in range(7):
            classes = [x for x in days[i] if x["kind"] == "uni"]
            if not classes:
                continue
            first = min(to_min(x["start"]) for x in classes)
            last = max(to_min(x["end"]) for x in classes)
            if go:
                days[i].append({"kind": "auto", "title": go["name"], "emoji": go["emoji"],
                                "sub": "automatic", "colour": go["colour"],
                                "start": to_hhmm(first - go["minutes"]), "end": to_hhmm(first)})
            if back:
                days[i].append({"kind": "auto", "title": back["name"], "emoji": back["emoji"],
                                "sub": "automatic", "colour": back["colour"],
                                "start": to_hhmm(last), "end": to_hhmm(last + back["minutes"])})
    return days


# Works out where each block sits on the grid. Blocks that overlap sit side by side
def lay_out(items):
    items = sorted(items, key=lambda x: (to_min(x["start"]), -to_min(x["end"])))
    groups, group, group_end = [], [], -1
    for it in items:
        s, e = to_min(it["start"]), max(to_min(it["end"]), to_min(it["start"]) + 15)
        it["s"], it["e"] = s, e
        if group and s >= group_end:
            groups.append(group)
            group, group_end = [], -1
        group.append(it)
        group_end = max(group_end, e)
    if group:
        groups.append(group)

    for group in groups:
        columns = []   # end time of the last block in each column
        for it in group:
            for col, col_end in enumerate(columns):
                if it["s"] >= col_end:
                    it["col"] = col
                    columns[col] = it["e"]
                    break
            else:
                it["col"] = len(columns)
                columns.append(it["e"])
        for it in group:
            it["cols"] = len(columns)

    top_min = START_HOUR * 60
    for it in items:
        it["top"] = (max(it["s"], top_min) - top_min) / 60       # in hours from the top
        it["height"] = (min(it["e"], END_HOUR * 60) - max(it["s"], top_min)) / 60
        it["length"] = nice_length(it["e"] - it["s"])
    return [it for it in items if it["height"] > 0]


@bp.route("/calendar")
def calendar_page():
    picked = request.args.get("week")
    base = date.fromisoformat(picked) if picked else date.today()
    monday = base - timedelta(days=base.weekday())

    conn = get_db()
    items = week_items(conn, monday)
    presets = conn.execute("SELECT * FROM presets ORDER BY name").fetchall()
    auto = conn.execute("SELECT value FROM settings WHERE key = 'auto_commute'").fetchone()
    conn.close()

    today = date.today()
    week = []
    for i in range(7):
        day = monday + timedelta(days=i)
        blocks = lay_out(items[i])
        busy = sum(b["e"] - b["s"] for b in blocks)
        week.append({
            "index": i, "date": day.isoformat(), "short": DAYS[i][:3], "num": day.day,
            "is_today": day == today, "blocks": blocks, "busy": nice_length(busy) if busy else "",
        })

    now = datetime.now()
    now_top = (now.hour * 60 + now.minute - START_HOUR * 60) / 60

    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        title = f"{monday.day} – {sunday.day} {sunday.strftime('%B %Y')}"
    else:
        title = f"{monday.strftime('%d %b')} – {sunday.strftime('%d %b %Y')}"

    return render_template(
        "calendar.html", week=week, presets=presets, title=title,
        presets_json=[dict(p) for p in presets],
        prev_week=(monday - timedelta(days=7)).isoformat(),
        next_week=(monday + timedelta(days=7)).isoformat(),
        hours=list(range(START_HOUR, END_HOUR)), start_hour=START_HOUR, end_hour=END_HOUR,
        now_top=now_top, auto_commute=(auto and auto["value"] == "1"),
        this_week=monday.isoformat(),
    )


# Add a new event, or save changes to one (if an id is sent)
@bp.route("/calendar/save", methods=["POST"])
def save_event():
    f = request.form
    start = f["start"]
    end = f.get("end") or ""
    if not end or to_min(end) <= to_min(start):   # no end time: use the preset's length, or 1 hour
        end = to_hhmm(to_min(start) + int(f.get("minutes") or 60))
    values = (f["title"].strip(), f["day"], start, end, f.get("colour", "#22d3ee"),
              f.get("emoji", ""), "weekly" if f.get("repeat") else "none")
    conn = get_db()
    if f.get("id"):
        conn.execute("UPDATE events SET title=?, day=?, start=?, end=?, colour=?, emoji=?, repeat=? "
                     "WHERE id=?", values + (int(f["id"]),))
    else:
        conn.execute("INSERT INTO events (title, day, start, end, colour, emoji, repeat) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", values)
    conn.commit()
    conn.close()
    return redirect(f"/calendar?week={f['day']}")


@bp.route("/calendar/delete/<int:event_id>", methods=["POST"])
def delete_event(event_id):
    conn = get_db()
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.execute("DELETE FROM event_skips WHERE event_id = ?", (event_id,))
    conn.commit()
    conn.close()
    return redirect(f"/calendar?week={request.form.get('week', '')}")


# Skip one week of a repeating event (the rest stay)
@bp.route("/calendar/skip/<int:event_id>", methods=["POST"])
def skip_event(event_id):
    conn = get_db()
    conn.execute("INSERT INTO event_skips (event_id, day) VALUES (?, ?)", (event_id, request.form["day"]))
    conn.commit()
    conn.close()
    return redirect(f"/calendar?week={request.form['day']}")


@bp.route("/calendar/preset", methods=["POST"])
def add_preset():
    f = request.form
    minutes = int(f.get("hours") or 0) * 60 + int(f.get("mins") or 0)
    conn = get_db()
    conn.execute("INSERT INTO presets (name, minutes, colour, emoji) VALUES (?, ?, ?, ?)",
                 (f["name"].strip(), max(minutes, 5), f.get("colour", "#22d3ee"), f.get("emoji", "").strip()))
    conn.commit()
    conn.close()
    return redirect(f"/calendar?week={f.get('week', '')}")


@bp.route("/calendar/preset/<int:preset_id>/delete", methods=["POST"])
def delete_preset(preset_id):
    conn = get_db()
    conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
    conn.commit()
    conn.close()
    return redirect(f"/calendar?week={request.form.get('week', '')}")


# Change how long a preset takes (e.g. commute is actually 55 mins)
@bp.route("/calendar/preset/<int:preset_id>/time", methods=["POST"])
def preset_time(preset_id):
    f = request.form
    minutes = int(f.get("hours") or 0) * 60 + int(f.get("mins") or 0)
    conn = get_db()
    conn.execute("UPDATE presets SET minutes = ? WHERE id = ?", (max(minutes, 5), preset_id))
    conn.commit()
    conn.close()
    return redirect(f"/calendar?week={f.get('week', '')}")


@bp.route("/calendar/auto-commute", methods=["POST"])
def toggle_auto_commute():
    conn = get_db()
    now = conn.execute("SELECT value FROM settings WHERE key = 'auto_commute'").fetchone()
    new = "0" if now and now["value"] == "1" else "1"
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_commute', ?)", (new,))
    conn.commit()
    conn.close()
    return redirect(f"/calendar?week={request.form.get('week', '')}")


# Text for the AI brain: what's booked over the next 7 days
def brain_lines(conn):
    lines = ["CALENDAR (fixed bookings for the next 7 days, plan around these):"]
    today = date.today()
    for offset in (0, 7):
        monday = today - timedelta(days=today.weekday()) + timedelta(days=offset)
        items = week_items(conn, monday)
        for i in range(7):
            day = monday + timedelta(days=i)
            if not (today <= day < today + timedelta(days=7)):
                continue
            for it in sorted(items[i], key=lambda x: x["start"]):
                lines.append(f"- {day.strftime('%a %d %b')} {it['start']}-{it['end']}: {it['title']}")
    return lines


setup_calendar()
