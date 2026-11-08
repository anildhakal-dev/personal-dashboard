from flask import Flask, render_template, request, redirect
import sqlite3
from datetime import date, datetime, timedelta
import json
from urllib.parse import quote

app = Flask(__name__)
DB = "dashboard.db"


# Opens the database. row_factory lets us write task["title"] instead of task[1]
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


# Creates the tasks table the first time the app runs
def setup_database():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Adds the "due" date column to tasks. If it's already there, SQLite
    # complains, so we just ignore that error
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN due TEXT")
    except sqlite3.OperationalError:
        pass

    # Quotes for the rotating widget
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            author TEXT
        )
    """)
    # Uni timetable: one row per weekly class (day 0 = Monday ... 6 = Sunday)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            kind TEXT,
            day INTEGER NOT NULL,
            start TEXT NOT NULL,
            end TEXT NOT NULL,
            room TEXT
        )
    """)
    # Uni deadlines: assignments and exams
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deadlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            module TEXT,
            kind TEXT DEFAULT 'Assignment',
            due TEXT NOT NULL,
            done INTEGER DEFAULT 0
        )
    """)

    # Gym: which workout is on which day (0 = Monday)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gym_plan (
            day INTEGER PRIMARY KEY,
            workout TEXT NOT NULL
        )
    """)
    # Gym: the exercises in each workout
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workout TEXT NOT NULL,
            name TEXT NOT NULL,
            sets_reps TEXT
        )
    """)
    # Gym: every set you log (weight and reps)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gym_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            exercise TEXT NOT NULL,
            weight REAL,
            reps INTEGER
        )
    """)
    # Protein: foods you eat often, and what you ate each day
    conn.execute("""
        CREATE TABLE IF NOT EXISTS foods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            protein INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS protein_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            food TEXT NOT NULL,
            protein INTEGER NOT NULL
        )
    """)

    # First run only: your starting gym week, exercises and foods
    if conn.execute("SELECT COUNT(*) FROM gym_plan").fetchone()[0] == 0:
        week = ["Push", "Pull", "Legs", "Shoulders & Arms", "Rest", "Boxing", "Rest"]
        conn.executemany("INSERT INTO gym_plan (day, workout) VALUES (?, ?)", list(enumerate(week)))
        conn.executemany("INSERT INTO exercises (workout, name, sets_reps) VALUES (?, ?, ?)", [
            ("Push", "Flat dumbbell press / machine press", "3 × 8-10"),
            ("Push", "Incline machine press", "3 × 8-10"),
            ("Push", "Shoulder press", "3 × 8-10"),
            ("Push", "Lateral raises", "3 × 12-15"),
            ("Push", "Skull crushers", "3 × 10-12"),
            ("Push", "Tricep pushdowns", "3 × 10-12"),
            ("Pull", "Lat pulldown (variation)", "3 × 8-10"),
            ("Pull", "Compound row", "3 × 8-10"),
            ("Pull", "Low-to-high variation", "3 × 10-12"),
            ("Pull", "Preacher curls", "3 × 10-12"),
            ("Pull", "Hammer curls", "3 × 10-12"),
            ("Boxing", "1-1 session", "1 hour"),
        ])
    if conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0] == 0:
        conn.executemany("INSERT INTO foods (name, protein) VALUES (?, ?)", [
            ("Chicken legs (2)", 50),
            ("Chicken breast (200g)", 60),
            ("Protein shake", 25),
            ("Eggs (3)", 18),
            ("Greek yoghurt (200g)", 20),
            ("Tuna (1 can)", 25),
        ])

    # Brain: your "about me" notes, and every plan it makes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            made_on TEXT NOT NULL,
            data TEXT NOT NULL
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO settings (key, value) VALUES ('about_me', ?)
    """, ("Wake up around 8:00, sleep around 00:00.\n"
          "I work on Saturdays (write the hours here).\n"
          "Boxing 1-1 session on Saturdays (write the time here).\n"
          "I trade the New York session in the afternoons (write the hours here).\n"
          "Sparring on Wednesdays is on hold while I'm at uni.\n"
          "I'm learning to code and building my business: 2-3 hours a day.\n"
          "Faith matters to me.",))

    # First run only: fill it with some starter quotes (delete any you don't like)
    count = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    if count == 0:
        starters = [
            ("I can do all things through Christ which strengtheneth me.", "Philippians 4:13"),
            ("Be strong and of a good courage; be not afraid, neither be thou dismayed: for the Lord thy God is with thee whithersoever thou goest.", "Joshua 1:9"),
            ("Trust in the Lord with all thine heart; and lean not unto thine own understanding.", "Proverbs 3:5"),
            ("Whatsoever ye do, do it heartily, as to the Lord, and not unto men.", "Colossians 3:23"),
            ("The hand of the diligent shall bear rule.", "Proverbs 12:24"),
            ("We suffer more often in imagination than in reality.", "Seneca"),
            ("You have power over your mind, not outside events. Realize this, and you will find strength.", "Marcus Aurelius"),
            ("Well done is better than well said.", "Benjamin Franklin"),
        ]
        conn.executemany("INSERT INTO quotes (text, author) VALUES (?, ?)", starters)
    conn.commit()
    conn.close()


# Turns a task's due date into a label and colour for the badge
def due_badge(due_text):
    if not due_text:
        return None, None
    due = date.fromisoformat(due_text)
    today = date.today()
    if due < today:
        return "Overdue", "red"
    if due == today:
        return "Today", "orange"
    if due == today + timedelta(days=1):
        return "Tomorrow", "blue"
    return due.strftime("%a %d %b"), "grey"


# Home page: shows today's date and the to-do list (unfinished tasks first)
@app.route("/")
def home():
    conn = get_db()
    # Unfinished first, then soonest due date first, tasks with no date last
    rows = conn.execute("""
        SELECT * FROM tasks
        ORDER BY done, due IS NULL, due, id DESC
    """).fetchall()
    conn.close()

    # Build a list of tasks with their badge added
    tasks = []
    for row in rows:
        label, colour = due_badge(row["due"])
        tasks.append({
            "id": row["id"],
            "title": row["title"],
            "done": row["done"],
            "badge": label,
            "colour": colour,
        })

    left = sum(1 for t in tasks if t["done"] == 0)
    overdue = sum(1 for t in tasks if t["done"] == 0 and t["badge"] == "Overdue")
    done = len(tasks) - left
    # percent of tasks finished, used for the progress ring
    percent = round(done / len(tasks) * 100) if tasks else 0
    today = date.today().strftime("%A %d %B")

    # Good morning / afternoon / evening depending on the time
    hour = datetime.now().hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 18:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    return render_template("home.html", tasks=tasks, left=left, done=done,
                           overdue=overdue, percent=percent, today=today,
                           greeting=greeting)


# Adds a new task from the box on the home page
@app.route("/add-task", methods=["POST"])
def add_task():
    title = request.form["title"].strip()
    due = request.form.get("due") or None   # empty date box becomes None
    if title:
        conn = get_db()
        conn.execute("INSERT INTO tasks (title, due) VALUES (?, ?)", (title, due))
        conn.commit()
        conn.close()
    return redirect("/")


# Ticks a task as done, or un-ticks it (1 - 0 = 1, 1 - 1 = 0)
@app.route("/toggle/<int:task_id>", methods=["POST"])
def toggle_task(task_id):
    conn = get_db()
    conn.execute("UPDATE tasks SET done = 1 - done WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return redirect("/")


# Deletes a task
@app.route("/delete-task/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return redirect("/")



# ---------- Quotes ----------

# A context processor runs before EVERY page, so the sidebar quote widget
# (which lives in base.html) always has the quotes it needs
@app.context_processor
def inject_quotes():
    conn = get_db()
    rows = conn.execute("SELECT text, author FROM quotes ORDER BY id").fetchall()
    conn.close()
    quotes = [{"text": r["text"], "author": r["author"] or ""} for r in rows]
    # "Quote of the day": changes every day, cycling through your list
    start = date.today().toordinal() % len(quotes) if quotes else 0

    # If the brain planned today, its hand-picked quote shows first
    conn = get_db()
    plan = conn.execute("SELECT data FROM plans WHERE made_on = ? ORDER BY id DESC LIMIT 1",
                        (date.today().isoformat(),)).fetchone()
    conn.close()
    if plan:
        q = json.loads(plan["data"])["quote"]
        quotes.insert(0, {"text": q["text"], "author": q["source"]})
        start = 0
    return {"all_quotes": quotes, "quote_start": start}


# Page to see, add and delete your quotes
@app.route("/quotes")
def quotes_page():
    conn = get_db()
    quotes = conn.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("quotes.html", quotes=quotes)


@app.route("/add-quote", methods=["POST"])
def add_quote():
    text = request.form["text"].strip()
    author = request.form.get("author", "").strip()
    if text:
        conn = get_db()
        conn.execute("INSERT INTO quotes (text, author) VALUES (?, ?)", (text, author))
        conn.commit()
        conn.close()
    return redirect("/quotes")


@app.route("/delete-quote/<int:quote_id>", methods=["POST"])
def delete_quote(quote_id):
    conn = get_db()
    conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
    conn.commit()
    conn.close()
    return redirect("/quotes")



# ---------- Uni ----------

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Each module gets its own colour on the timetable
COLOURS = ["#8b5cf6", "#ec4899", "#22d3ee", "#34d399", "#fb923c", "#facc15", "#60a5fa", "#f87171"]


# Turns a due date into "Today", "in 5 days" etc, plus how urgent it is
def countdown(due_text):
    days = (date.fromisoformat(due_text) - date.today()).days
    if days < 0:
        label = f"{-days} day{'s' if days != -1 else ''} ago"
    elif days == 0:
        label = "Today"
    elif days == 1:
        label = "Tomorrow"
    else:
        label = f"in {days} days"
    if days <= 3:
        urgency = "red"
    elif days <= 7:
        urgency = "orange"
    else:
        urgency = "grey"
    return days, label, urgency


@app.route("/uni")
def uni():
    conn = get_db()
    classes = conn.execute("SELECT * FROM classes ORDER BY day, start").fetchall()
    rows = conn.execute("SELECT * FROM deadlines ORDER BY done, due").fetchall()
    conn.close()

    # Give every module a colour (same module = same colour)
    modules = sorted(set(c["module"] for c in classes) | set(d["module"] for d in rows if d["module"]))
    colour_of = {m: COLOURS[i % len(COLOURS)] for i, m in enumerate(modules)}

    # Put classes into a column for each day. Weekends only show if you have classes then
    week = []
    for i, day_name in enumerate(DAYS):
        day_classes = [c for c in classes if c["day"] == i]
        if i < 5 or day_classes:
            week.append({"name": day_name, "index": i, "classes": day_classes})

    today_index = date.today().weekday()
    today_classes = [c for c in classes if c["day"] == today_index]

    deadlines = []
    for d in rows:
        days, label, urgency = countdown(d["due"])
        deadlines.append({
            "id": d["id"], "title": d["title"], "module": d["module"], "kind": d["kind"],
            "done": d["done"], "days": days, "label": label, "urgency": urgency,
            "nice_date": date.fromisoformat(d["due"]).strftime("%a %d %b"),
        })
    upcoming = [d for d in deadlines if not d["done"] and d["days"] >= 0]
    next_exam = next((d for d in upcoming if d["kind"] == "Exam"), None)

    return render_template("uni.html", week=week, days=DAYS, today_index=today_index,
                           today_classes=today_classes, deadlines=deadlines,
                           upcoming=upcoming, next_exam=next_exam,
                           colour_of=colour_of, modules=modules)


@app.route("/add-class", methods=["POST"])
def add_class():
    conn = get_db()
    conn.execute(
        "INSERT INTO classes (module, kind, day, start, end, room) VALUES (?, ?, ?, ?, ?, ?)",
        (request.form["module"].strip(), request.form["kind"], int(request.form["day"]),
         request.form["start"], request.form["end"], request.form.get("room", "").strip()),
    )
    conn.commit()
    conn.close()
    return redirect("/uni")


@app.route("/delete-class/<int:class_id>", methods=["POST"])
def delete_class(class_id):
    conn = get_db()
    conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))
    conn.commit()
    conn.close()
    return redirect("/uni")


@app.route("/add-deadline", methods=["POST"])
def add_deadline():
    conn = get_db()
    conn.execute(
        "INSERT INTO deadlines (title, module, kind, due) VALUES (?, ?, ?, ?)",
        (request.form["title"].strip(), request.form.get("module", "").strip(),
         request.form["kind"], request.form["due"]),
    )
    conn.commit()
    conn.close()
    return redirect("/uni")


@app.route("/toggle-deadline/<int:deadline_id>", methods=["POST"])
def toggle_deadline(deadline_id):
    conn = get_db()
    conn.execute("UPDATE deadlines SET done = 1 - done WHERE id = ?", (deadline_id,))
    conn.commit()
    conn.close()
    return redirect("/uni")


@app.route("/delete-deadline/<int:deadline_id>", methods=["POST"])
def delete_deadline(deadline_id):
    conn = get_db()
    conn.execute("DELETE FROM deadlines WHERE id = ?", (deadline_id,))
    conn.commit()
    conn.close()
    return redirect("/uni")



# ---------- Gym + protein ----------

PROTEIN_MIN = 150
PROTEIN_MAX = 180
WORKOUT_OPTIONS = ["Push", "Pull", "Legs", "Shoulders & Arms", "Boxing", "Sparring", "Rest"]
WORKOUT_ICONS = {"Push": "💪", "Pull": "🏋️", "Legs": "🦵", "Shoulders & Arms": "🔥",
                 "Boxing": "🥊", "Sparring": "🥊", "Rest": "😴"}


@app.route("/gym")
def gym():
    today = date.today()
    # Which day to show: today, unless you clicked another day (?day=2)
    show_day = request.args.get("day", type=int)
    if show_day is None:
        show_day = today.weekday()

    conn = get_db()
    plan = {r["day"]: r["workout"] for r in conn.execute("SELECT * FROM gym_plan")}
    workout = plan.get(show_day, "Rest")
    exercises = conn.execute("SELECT * FROM exercises WHERE workout = ? ORDER BY id", (workout,)).fetchall()

    # Your last logged set for each exercise, so you know what to beat
    last = {}
    for ex in exercises:
        row = conn.execute(
            "SELECT weight, reps, day FROM gym_log WHERE exercise = ? ORDER BY id DESC LIMIT 1",
            (ex["name"],)).fetchone()
        last[ex["name"]] = row

    todays_sets = conn.execute("SELECT * FROM gym_log WHERE day = ? ORDER BY id DESC",
                               (today.isoformat(),)).fetchall()

    foods = conn.execute("SELECT * FROM foods ORDER BY protein DESC").fetchall()
    eaten = conn.execute("SELECT * FROM protein_log WHERE day = ? ORDER BY id DESC",
                         (today.isoformat(),)).fetchall()
    conn.close()

    protein = sum(e["protein"] for e in eaten)
    # Ring fills up to the 150g minimum
    protein_pct = min(100, round(protein / PROTEIN_MIN * 100))
    if protein >= PROTEIN_MIN:
        protein_msg = "Target hit 🎉"
    else:
        protein_msg = f"{PROTEIN_MIN - protein}g to go"

    week = [{"index": i, "name": DAYS[i][:3], "workout": plan.get(i, "Rest"),
             "icon": WORKOUT_ICONS.get(plan.get(i, "Rest"), "🏃")} for i in range(7)]

    return render_template("gym.html", week=week, show_day=show_day, today_index=today.weekday(),
                           day_name=DAYS[show_day], workout=workout,
                           icon=WORKOUT_ICONS.get(workout, "🏃"), exercises=exercises, last=last,
                           todays_sets=todays_sets, options=WORKOUT_OPTIONS, foods=foods,
                           eaten=eaten, protein=protein, protein_pct=protein_pct,
                           protein_msg=protein_msg, pmin=PROTEIN_MIN, pmax=PROTEIN_MAX)


# Change which workout is on a day
@app.route("/set-workout", methods=["POST"])
def set_workout():
    day = int(request.form["day"])
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO gym_plan (day, workout) VALUES (?, ?)",
                 (day, request.form["workout"]))
    conn.commit()
    conn.close()
    return redirect(f"/gym?day={day}")


@app.route("/add-exercise", methods=["POST"])
def add_exercise():
    conn = get_db()
    conn.execute("INSERT INTO exercises (workout, name, sets_reps) VALUES (?, ?, ?)",
                 (request.form["workout"], request.form["name"].strip(),
                  request.form.get("sets_reps", "").strip()))
    conn.commit()
    conn.close()
    return redirect(f"/gym?day={request.form['day']}")


@app.route("/delete-exercise/<int:ex_id>", methods=["POST"])
def delete_exercise(ex_id):
    conn = get_db()
    conn.execute("DELETE FROM exercises WHERE id = ?", (ex_id,))
    conn.commit()
    conn.close()
    return redirect(f"/gym?day={request.form['day']}")


# Log one set (weight + reps) for an exercise
@app.route("/log-set", methods=["POST"])
def log_set():
    conn = get_db()
    conn.execute("INSERT INTO gym_log (day, exercise, weight, reps) VALUES (?, ?, ?, ?)",
                 (date.today().isoformat(), request.form["exercise"].strip(),
                  request.form.get("weight") or None, request.form.get("reps") or None))
    conn.commit()
    conn.close()
    return redirect(f"/gym?day={request.form['day']}")


# Log food: either a saved food button or a custom amount
@app.route("/eat", methods=["POST"])
def eat():
    conn = get_db()
    conn.execute("INSERT INTO protein_log (day, food, protein) VALUES (?, ?, ?)",
                 (date.today().isoformat(), request.form["food"].strip(), int(request.form["protein"])))
    # Tick "save as a quick food" to get a button for it next time
    if request.form.get("save"):
        conn.execute("INSERT INTO foods (name, protein) VALUES (?, ?)",
                     (request.form["food"].strip(), int(request.form["protein"])))
    conn.commit()
    conn.close()
    return redirect("/gym")


@app.route("/delete-eaten/<int:log_id>", methods=["POST"])
def delete_eaten(log_id):
    conn = get_db()
    conn.execute("DELETE FROM protein_log WHERE id = ?", (log_id,))
    conn.commit()
    conn.close()
    return redirect("/gym")


# Meal prep goes straight onto your to-do list on the Home page
@app.route("/add-prep", methods=["POST"])
def add_prep():
    conn = get_db()
    conn.execute("INSERT INTO tasks (title, due) VALUES (?, ?)",
                 ("🍗 Prep: " + request.form["what"].strip(), request.form.get("due") or None))
    conn.commit()
    conn.close()
    return redirect("/gym?prep=added")



# ---------- Brain ----------

CATEGORY_COLOURS = {"uni": "#8b5cf6", "study": "#ec4899", "gym": "#34d399", "boxing": "#f87171",
                    "food": "#fb923c", "trading": "#facc15", "work": "#60a5fa",
                    "personal": "#22d3ee", "rest": "#8b90b0"}


def latest_plan(conn, kind):
    row = conn.execute("SELECT * FROM plans WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    if not row:
        return None
    plan = json.loads(row["data"])
    plan["made_on"] = row["made_on"]
    # Nice labels for each day, e.g. "Monday 09 Nov"
    for d in plan["days"]:
        try:
            d["label"] = date.fromisoformat(d["date"]).strftime("%A %d %b")
        except ValueError:
            d["label"] = d["date"]
    return plan


@app.route("/brain")
def brain_page():
    conn = get_db()
    about_me = conn.execute("SELECT value FROM settings WHERE key = 'about_me'").fetchone()["value"]
    day_plan = latest_plan(conn, "day")
    week_plan = latest_plan(conn, "week")
    conn.close()
    show = request.args.get("show", "day")
    return render_template("brain.html", about_me=about_me, day_plan=day_plan, week_plan=week_plan,
                           show=show, colours=CATEGORY_COLOURS, error=request.args.get("error"),
                           today=date.today().isoformat(), now=datetime.now().strftime("%H:%M"))


# Press "Plan my day" / "Plan my week"
@app.route("/make-plan", methods=["POST"])
def make_plan_route():
    import brain   # only loaded when needed, so the rest works even without an API key
    kind = request.form["kind"]
    conn = get_db()
    about_me = conn.execute("SELECT value FROM settings WHERE key = 'about_me'").fetchone()["value"]
    try:
        plan = brain.make_plan(conn, kind, about_me)
    except Exception as e:   # show any problem on the page instead of crashing
        conn.close()
        message = quote(f"{type(e).__name__}: {e}"[:300])
        return redirect(f"/brain?show={kind}&error={message}")
    conn.execute("INSERT INTO plans (kind, made_on, data) VALUES (?, ?, ?)",
                 (kind, date.today().isoformat(), json.dumps(plan)))
    conn.commit()
    conn.close()
    return redirect(f"/brain?show={kind}")


@app.route("/about-me", methods=["POST"])
def save_about_me():
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('about_me', ?)",
                 (request.form["about_me"],))
    conn.commit()
    conn.close()
    return redirect("/brain")


setup_database()

if __name__ == "__main__":
    app.run(debug=True, port=5002)
