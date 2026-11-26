# Assignment planner: works backwards from each due date and sets mini-deadlines
# (brief + checklist, reading, research/analysis, first draft, edit, final check),
# placed on your library days. Alerts nags you when you fall behind.

from flask import Blueprint, render_template, request, redirect
from datetime import date, timedelta
from urllib.parse import quote
import sqlite3
import json

bp = Blueprint("assignments", __name__)
DB = "dashboard.db"
LIBRARY_DAYS = (0, 1)            # Monday and Tuesday: the days you're in the library
WORK_DAY = 5                     # Saturday: you're at work, so nothing is planned for then
KINDS = ("Assignment", "Coursework", "Presentation", "Essay", "Project", "Report")

# The default plan: (step, share of the time between now and the due date)
DEFAULT_STEPS = [
    ("Read the brief, upload it to Study, check the 1st-class checklist", 0.08),
    ("Reading and sources done (every red checklist item closed)", 0.30),
    ("Research / data / analysis done", 0.50),
    ("First full draft written", 0.72),
    ("Edit against the marking criteria (run Check against my notes)", 0.88),
    ("Final read, references, formatting", 0.97),
]


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def setup_assignments():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assign_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deadline_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            detail TEXT,
            due TEXT NOT NULL,           -- YYYY-MM-DD
            done INTEGER DEFAULT 0,
            sort INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def on_library_day(day, earliest):
    """Moves a date back to the nearest library day (not before `earliest`)."""
    d = day
    while d >= earliest:
        if d.weekday() in LIBRARY_DAYS:
            return d
        d -= timedelta(days=1)
    # no library day in range: keep the date, but never on a work day
    if day.weekday() == WORK_DAY:
        return day - timedelta(days=1) if day - timedelta(days=1) >= earliest else day + timedelta(days=1)
    return day


def place(steps, start, due):
    """Turns (title, detail, share) into real dates between start and the due date."""
    span = max(1, (due - start).days)
    out, last = [], start
    for title, detail, share in steps:
        d = start + timedelta(days=round(share * span))
        d = min(on_library_day(d, max(start, last)), due - timedelta(days=1)) if due > start else due
        d = max(d, last)
        out.append((title, detail, d))
        last = d
    out.append(("Submit 🎯", "Hand it in. Screenshot the receipt.", due))
    return out


def brief_for(conn, deadline):
    """The brief you uploaded in Study for this assignment's module (if any)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'study_sources'").fetchone():
        return None
    rows = conn.execute("SELECT * FROM study_sources WHERE module = ? AND kind = 'brief' ORDER BY id DESC",
                        (deadline["module"] or "",)).fetchall()
    for r in rows:     # prefer the brief with the same due date
        if json.loads(r["guide"] or "{}").get("due") == deadline["due"][:10]:
            return r
    return rows[0] if rows else None


def make_plan(conn, deadline, steps=None):
    conn.execute("DELETE FROM assign_steps WHERE deadline_id = ? AND done = 0", (deadline["id"],))
    done_titles = {r["title"] for r in conn.execute("SELECT title FROM assign_steps WHERE deadline_id = ?", (deadline["id"],))}
    steps = steps or [(t, "", s) for t, s in DEFAULT_STEPS]
    due = date.fromisoformat(deadline["due"][:10])
    for i, (title, detail, day) in enumerate(place(steps, date.today(), due)):
        if title in done_titles:
            continue
        conn.execute("INSERT INTO assign_steps (deadline_id, title, detail, due, sort) VALUES (?, ?, ?, ?, ?)",
                     (deadline["id"], title, detail, day.isoformat(), i))
    conn.commit()


def upcoming(conn):
    """Assignments still to hand in (not exams), soonest first."""
    rows = conn.execute("SELECT * FROM deadlines WHERE done = 0 AND due >= ? ORDER BY due",
                        ((date.today() - timedelta(days=3)).isoformat(),)).fetchall()
    return [r for r in rows if (r["kind"] or "Assignment") != "Exam"]


def nice(d):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    left = (d - date.today()).days
    when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days" if left > 1 else \
           "yesterday" if left == -1 else f"{-left} days ago"
    return d.strftime("%a %d %b"), when, left


# ---------- Pages ----------

@bp.route("/assignments")
def planner_page():
    conn = get_db()
    plans = []
    for d in upcoming(conn):
        steps = conn.execute("SELECT * FROM assign_steps WHERE deadline_id = ? ORDER BY due, sort, id", (d["id"],)).fetchall()
        if not steps:                       # first visit: make the default plan automatically
            make_plan(conn, d)
            steps = conn.execute("SELECT * FROM assign_steps WHERE deadline_id = ? ORDER BY due, sort, id", (d["id"],)).fetchall()
        rows = []
        for s in steps:
            label, when, left = nice(s["due"])
            light = "green" if s["done"] else "red" if left < 0 else "amber" if left <= 6 else "grey"
            rows.append(dict(s, label=label, when=when, light=light))
        done = sum(1 for s in steps if s["done"])
        behind = sum(1 for r in rows if r["light"] == "red")
        due_label, due_when, due_left = nice(d["due"][:10])
        nxt = next((r for r in rows if not r["done"]), None)
        brief = brief_for(conn, d)
        plans.append({"d": d, "steps": rows, "done": done, "total": len(steps), "behind": behind,
                      "due_label": due_label, "due_when": due_when, "next": nxt, "brief": brief})
    conn.close()
    return render_template("assignments.html", plans=plans, error=request.args.get("error"),
                           library=", ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i] for i in LIBRARY_DAYS))


@bp.route("/assignments/<int:deadline_id>/rebuild", methods=["POST"])
def rebuild(deadline_id):
    """Re-plans the unfinished steps from today (keeps the ones you've ticked)."""
    conn = get_db()
    d = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
    if d:
        make_plan(conn, d)
    conn.close()
    return redirect(f"/assignments#a{deadline_id}")


TAILOR_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["steps"],
    "properties": {"steps": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["title", "detail", "share"],
        "properties": {
            "title": {"type": "string", "description": "the milestone, short, specific to this assignment"},
            "detail": {"type": "string", "description": "one line: exactly what 'done' means"},
            "share": {"type": "number", "description": "when it should be done, as a fraction 0-1 of the time from today to the due date; increasing"},
        }}}},
}


@bp.route("/assignments/<int:deadline_id>/tailor", methods=["POST"])
def tailor(deadline_id):
    """Asks Claude to make the steps specific to this assignment (uses your brief from Study)."""
    conn = get_db()
    d = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
    try:
        import study
        brief = brief_for(conn, d)
        left = (date.fromisoformat(d["due"][:10]) - date.today()).days
        about = study.module_context(conn, d["module"]) if d["module"] else ""
        ask = f"""Plan my assignment "{d['title']}" ({d['kind']}), due {d['due'][:10]}, {left} days from today.
I only do focused work in the library on Mondays and Tuesdays (about 8 hours each), plus odd evenings.
Give 5-7 milestones in order, ending with a final check (the hand-in itself is added automatically).
Make them specific to this assignment (name the actual analyses, sections and criteria), aimed at a 1st.
Front-load anything the checklist shows as not covered. Leave at least 15% of the time for editing.
share = when each milestone should be finished, as a fraction of the time from today to the due date.

{('THE BRIEF: ' + brief['guide']) if brief else '(No brief uploaded in Study for this one: keep the steps general but sensible.)'}

{about}"""
        result = study.ask_json(d["module"] or "General", [{"type": "text", "text": ask}], TAILOR_SCHEMA)
        steps = sorted(((s["title"], s["detail"], min(max(float(s["share"]), 0.0), 0.99)) for s in result["steps"]), key=lambda x: x[2])
        if not steps:
            raise ValueError("Claude didn't return any steps")
        make_plan(conn, d, steps)
        return redirect(f"/assignments#a{deadline_id}")
    except Exception as e:
        return redirect("/assignments?error=" + quote(f"{type(e).__name__}: {e}"[:300]))
    finally:
        conn.close()


@bp.route("/assignments/step/<int:step_id>/tick", methods=["POST"])
def tick(step_id):
    conn = get_db()
    conn.execute("UPDATE assign_steps SET done = 1 - done WHERE id = ?", (step_id,))
    row = conn.execute("SELECT deadline_id FROM assign_steps WHERE id = ?", (step_id,)).fetchone()
    conn.commit()
    conn.close()
    return redirect(f"/assignments#a{row['deadline_id']}" if row else "/assignments")


@bp.route("/assignments/step/<int:step_id>/edit", methods=["POST"])
def edit_step(step_id):
    conn = get_db()
    title = request.form.get("title", "").strip()
    due = request.form.get("due", "").strip()
    if title:
        conn.execute("UPDATE assign_steps SET title = ? WHERE id = ?", (title, step_id))
    if due:
        conn.execute("UPDATE assign_steps SET due = ? WHERE id = ?", (due, step_id))
    row = conn.execute("SELECT deadline_id FROM assign_steps WHERE id = ?", (step_id,)).fetchone()
    conn.commit()
    conn.close()
    return redirect(f"/assignments#a{row['deadline_id']}" if row else "/assignments")


@bp.route("/assignments/step/<int:step_id>/delete", methods=["POST"])
def delete_step(step_id):
    conn = get_db()
    row = conn.execute("SELECT deadline_id FROM assign_steps WHERE id = ?", (step_id,)).fetchone()
    conn.execute("DELETE FROM assign_steps WHERE id = ?", (step_id,))
    conn.commit()
    conn.close()
    return redirect(f"/assignments#a{row['deadline_id']}" if row else "/assignments")


@bp.route("/assignments/<int:deadline_id>/add", methods=["POST"])
def add_step(deadline_id):
    title = request.form.get("title", "").strip()
    due = request.form.get("due", "").strip()
    if title and due:
        conn = get_db()
        conn.execute("INSERT INTO assign_steps (deadline_id, title, due, sort) VALUES (?, ?, ?, 50)", (deadline_id, title, due))
        conn.commit()
        conn.close()
    return redirect(f"/assignments#a{deadline_id}")


# ---------- Used by Alerts and the Scorecard ----------

def step_alerts(conn, today):
    """(level, title, body, url, key) for steps you're behind on or that are due very soon."""
    out = []
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'assign_steps'").fetchone():
        return out
    for s in conn.execute("""SELECT s.*, d.title AS assignment, d.due AS final FROM assign_steps s
                             JOIN deadlines d ON d.id = s.deadline_id
                             WHERE s.done = 0 AND d.done = 0 AND s.title NOT LIKE 'Submit%'"""):
        left = (date.fromisoformat(s["due"]) - today).days
        if left < 0:
            out.append(("red", s["title"], f"{s['assignment']}: should have been done {-left} day{'s' if left < -1 else ''} ago. You're behind.",
                        f"/assignments#a{s['deadline_id']}", f"step{s['id']}"))
        elif left <= 1:
            out.append(("amber", s["title"], f"{s['assignment']}: due {'today' if left == 0 else 'tomorrow'}.",
                        f"/assignments#a{s['deadline_id']}", f"step{s['id']}"))
    return out


setup_assignments()
