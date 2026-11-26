from flask import Flask, render_template, request, redirect, session
from werkzeug.security import check_password_hash
import sqlite3
from datetime import date, datetime, timedelta
import json
from urllib.parse import quote

import config   # your secrets: API key, secret key, password hash (never on GitHub)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.permanent_session_lifetime = timedelta(days=30)   # stay logged in on your phone for 30 days
DB = "dashboard.db"


# ---------- Login ----------

# Runs before every page: if you're not logged in, you go to the login page
@app.before_request
def require_login():
    if request.path in ("/login", "/alerts/run", "/sw.js") or request.path.startswith("/static/"):
        return None
    if not session.get("logged_in"):
        return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if check_password_hash(config.PASSWORD_HASH, request.form["password"]):
            session.permanent = True
            session["logged_in"] = True
            return redirect("/")
        error = "Wrong password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


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

    # Trading journal: every trade you take
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            instrument TEXT NOT NULL,
            direction TEXT,
            pnl REAL NOT NULL,
            rr REAL,
            account TEXT,
            setup TEXT,
            followed_plan INTEGER DEFAULT 1,
            notes TEXT
        )
    """)
    # Prop firm money: eval/reset/activation fees you pay, and payouts you get
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prop_money (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            kind TEXT NOT NULL,
            firm TEXT,
            amount REAL NOT NULL,
            note TEXT
        )
    """)

    # Money: every bank transaction (spending is negative, money in is positive)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            description TEXT NOT NULL,
            merchant TEXT,
            amount REAL NOT NULL,
            balance REAL,
            category TEXT,
            UNIQUE (day, description, amount, balance)
        )
    """)
    # Money: shops you've taught it ("ULTRAFLEX" -> Gym & boxing), so it remembers
    conn.execute("""
        CREATE TABLE IF NOT EXISTS merchant_rules (
            merchant TEXT PRIMARY KEY,
            category TEXT NOT NULL
        )
    """)
    # Money: things you're saving for
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            target REAL NOT NULL,
            saved REAL DEFAULT 0,
            by_date TEXT
        )
    """)
    # Money: the brain's monthly reviews
    conn.execute("""
        CREATE TABLE IF NOT EXISTS money_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            made_on TEXT NOT NULL,
            data TEXT NOT NULL
        )
    """)
    # Money: your chat with the coach about each month's review
    conn.execute("""
        CREATE TABLE IF NOT EXISTS money_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            made_on TEXT NOT NULL
        )
    """)
    # Money: lasting facts the coach picked up from your chats (used every month)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS money_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            added_on TEXT NOT NULL
        )
    """)
    # First run only: your goals (edit them on the Money page)
    if conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 0:
        conn.executemany("INSERT INTO goals (name, target, saved, by_date) VALUES (?, ?, 0, ?)", [
            ("🛟 Emergency fund", 3000, "2027-08-31"),
            ("💼 Business software", 3000, "2027-03-31"),
            ("🚗 BMW", 45000, "2028-05-31"),
            ("🏡 House deposit (10% of £350k)", 35000, "2030-11-30"),
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

    conn.execute("""
        INSERT OR IGNORE INTO settings (key, value) VALUES ('money_profile', ?)
    """, ("Income: restaurant wages about £1,000 a month. Student loan £3,350 each semester. "
          "Trading payouts average about £1,000 a month but are not guaranteed.\n"
          "Fixed costs: £734 a month paying off last year's rent until February 2027, then no rent.\n"
          "Subscriptions: Disney+, Amazon Prime, Strava, Spotify, YouTube Premium (maybe more).\n"
          "Two gym memberships: Foundry about £36 a month and Ultraflex about £56 a month.\n"
          "Prop firm fees average about £500 a month, all paid from Nationwide.\n"
          "No debt, no credit cards, no car finance. Only one account (Nationwide).\n"
          "Savings right now: (write how much and where)\n"
          "Untouchable: boxing, £150 for 4 1-1 sessions plus weekly sparring.\n"
          "Goals: a house within 4 years (£300k-£400k), a BMW within 1.5 years (about £45k), "
          "and starting a business that needs a few thousand for software.",))

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
COLOURS = ["#c9951f", "#2f7d74", "#7a5c99", "#b5583a", "#3d6a99", "#6b7a2f", "#a8526a", "#5c6b73"]


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



# ---------- Trading journal ----------

CURRENCY = "$"   # prop firms pay in dollars. Change to "£" if you prefer
FEE_KINDS = ["Eval fee", "Reset fee", "Activation fee", "Other cost"]


def money(x):
    """Turns 125.5 into +$125.50 and -40 into -$40.00"""
    sign = "+" if x > 0 else ("-" if x < 0 else "")
    return f"{sign}{CURRENCY}{abs(x):,.2f}"


@app.route("/trading")
def trading():
    today = date.today()
    # Which month the calendar shows: this month, unless you pressed the arrows (?month=2026-10)
    month_text = request.args.get("month") or today.strftime("%Y-%m")
    first = date.fromisoformat(month_text + "-01")
    prev_month = (first - timedelta(days=1)).strftime("%Y-%m")
    next_month = (first + timedelta(days=32)).strftime("%Y-%m")

    conn = get_db()
    trades = conn.execute("SELECT * FROM trades ORDER BY day DESC, id DESC").fetchall()
    prop = conn.execute("SELECT * FROM prop_money ORDER BY day DESC, id DESC").fetchall()
    conn.close()

    # Add up P&L and trade count for each day
    by_day = {}
    for t in trades:
        d = by_day.setdefault(t["day"], {"pnl": 0, "count": 0})
        d["pnl"] += t["pnl"]
        d["count"] += 1

    def total_between(start, end):
        return sum(v["pnl"] for k, v in by_day.items() if start.isoformat() <= k <= end.isoformat())

    week_start = today - timedelta(days=today.weekday())   # this Monday
    today_pnl = by_day.get(today.isoformat(), {"pnl": 0})["pnl"]
    week_pnl = total_between(week_start, today)
    month_pnl = total_between(today.replace(day=1), today)
    all_pnl = sum(t["pnl"] for t in trades)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    win_rate = round(len(wins) / len(trades) * 100) if trades else 0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    plan_rate = round(sum(t["followed_plan"] for t in trades) / len(trades) * 100) if trades else 0

    # Calendar: rows of Mon-Sun, with the week's total at the end of each row
    weeks = []
    day = first - timedelta(days=first.weekday())   # Monday on or before the 1st
    while day.month == first.month or day < first:
        row = {"days": [], "total": 0, "count": 0}
        for _ in range(7):
            info = by_day.get(day.isoformat())
            row["days"].append({
                "num": day.day,
                "in_month": day.month == first.month,
                "is_today": day == today,
                "pnl": info["pnl"] if info else None,
                "count": info["count"] if info else 0,
            })
            if info:
                row["total"] += info["pnl"]
                row["count"] += info["count"]
            day += timedelta(days=1)
        weeks.append(row)

    # Bar chart: the last 14 days that had trades, oldest first
    recent_days = sorted(by_day.items())[-14:]
    biggest = max([abs(v["pnl"]) for k, v in recent_days] or [1]) or 1
    bars = [{"label": date.fromisoformat(k).strftime("%d/%m"), "pnl": v["pnl"],
             "height": round(abs(v["pnl"]) / biggest * 100)} for k, v in recent_days]

    # Prop firm money: total spent vs total paid out
    spent = sum(p["amount"] for p in prop if p["kind"] != "Payout")
    paid = sum(p["amount"] for p in prop if p["kind"] == "Payout")

    # Names you've used before, so the boxes can suggest them
    instruments = sorted({t["instrument"] for t in trades}) or ["NQ", "MNQ", "ES", "MES", "GC"]
    accounts = sorted({t["account"] for t in trades if t["account"]})
    firms = sorted({p["firm"] for p in prop if p["firm"]})

    return render_template("trading.html", trades=trades[:15], prop=prop[:20], money=money,
                           today_pnl=today_pnl, week_pnl=week_pnl, month_pnl=month_pnl,
                           all_pnl=all_pnl, win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss,
                           plan_rate=plan_rate, trade_count=len(trades), weeks=weeks,
                           month_name=first.strftime("%B %Y"), prev_month=prev_month,
                           next_month=next_month, bars=bars, spent=spent, paid=paid,
                           instruments=instruments, accounts=accounts, firms=firms,
                           fee_kinds=FEE_KINDS, today=today.isoformat(), currency=CURRENCY)


# Log a trade. A loss is typed as a minus number, e.g. -150
@app.route("/add-trade", methods=["POST"])
def add_trade():
    conn = get_db()
    conn.execute("""INSERT INTO trades (day, instrument, direction, pnl, rr, account, setup, followed_plan, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (request.form.get("day") or date.today().isoformat(),
                  request.form["instrument"].strip().upper(),
                  request.form.get("direction"),
                  float(request.form["pnl"]),
                  request.form.get("rr") or None,
                  request.form.get("account", "").strip(),
                  request.form.get("setup", "").strip(),
                  1 if request.form.get("followed_plan") else 0,
                  request.form.get("notes", "").strip()))
    conn.commit()
    conn.close()
    return redirect("/trading")


@app.route("/delete-trade/<int:trade_id>", methods=["POST"])
def delete_trade(trade_id):
    conn = get_db()
    conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()
    conn.close()
    return redirect("/trading")


# Log an eval fee, reset, or payout (always typed as a positive number)
@app.route("/add-prop", methods=["POST"])
def add_prop():
    conn = get_db()
    conn.execute("INSERT INTO prop_money (day, kind, firm, amount, note) VALUES (?, ?, ?, ?, ?)",
                 (request.form.get("day") or date.today().isoformat(), request.form["kind"],
                  request.form.get("firm", "").strip(), abs(float(request.form["amount"])),
                  request.form.get("note", "").strip()))
    conn.commit()
    conn.close()
    return redirect("/trading#prop")


@app.route("/delete-prop/<int:prop_id>", methods=["POST"])
def delete_prop(prop_id):
    conn = get_db()
    conn.execute("DELETE FROM prop_money WHERE id = ?", (prop_id,))
    conn.commit()
    conn.close()
    return redirect("/trading#prop")



# ---------- Money ----------

import finance   # CSV reading, categories and the money coach (finance.py)


def month_summary(conn, month):
    """Totals for one month ('2026-11'): spending by category, income by category, etc."""
    rows = conn.execute("SELECT * FROM transactions WHERE substr(day, 1, 7) = ?", (month,)).fetchall()
    spend, income = {}, {}
    saved = 0
    for r in rows:
        cat = r["category"] or "Other"
        if cat == "Transfer":
            continue
        if cat == "Savings":
            saved -= r["amount"]          # money moved into savings counts as saved
        elif r["amount"] < 0:
            spend[cat] = spend.get(cat, 0) + -r["amount"]
        else:
            income[cat] = income.get(cat, 0) + r["amount"]
    return {"rows": rows, "spend": spend, "income": income, "saved": saved,
            "spent": sum(spend.values()), "money_in": sum(income.values())}


def goal_rows(conn):
    goals = []
    today = date.today()
    for g in conn.execute("SELECT * FROM goals ORDER BY by_date IS NULL, by_date"):
        left = max(0, g["target"] - g["saved"])
        months = None
        per_month = None
        if g["by_date"]:
            end = date.fromisoformat(g["by_date"])
            months = max(1, (end.year - today.year) * 12 + end.month - today.month)
            per_month = left / months
        goals.append({"id": g["id"], "name": g["name"], "target": g["target"], "saved": g["saved"],
                      "by_date": g["by_date"], "left": left, "months": months, "per_month": per_month,
                      "pct": min(100, round(g["saved"] / g["target"] * 100)) if g["target"] else 0,
                      "label": date.fromisoformat(g["by_date"]).strftime("%b %Y") if g["by_date"] else ""})
    return goals


@app.route("/money")
def money_page():
    conn = get_db()
    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(day, 1, 7) FROM transactions ORDER BY 1 DESC")]
    month = request.args.get("month") or (months[0] if months else date.today().strftime("%Y-%m"))
    this = month_summary(conn, month)
    prev_key = (date.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")
    prev = month_summary(conn, prev_key)

    # Donut chart slices, biggest first, with the change vs last month
    slices, start = [], 0
    for cat, amount in sorted(this["spend"].items(), key=lambda kv: -kv[1]):
        pct = amount / this["spent"] * 100 if this["spent"] else 0
        before = prev["spend"].get(cat)
        slices.append({"cat": cat, "amount": amount, "pct": round(pct), "start": start, "end": start + pct,
                       "colour": finance.CATEGORY_COLOURS.get(cat, "#8b90b0"),
                       "change": (amount - before) if before is not None and prev["spent"] else None})
        start += pct

    spending = [r for r in this["rows"] if r["amount"] < 0 and r["category"] not in ("Transfer", "Savings")]
    biggest = sorted(spending, key=lambda r: r["amount"])[:8]
    # Subscriptions: one line per service with what it cost this month
    subs = {}
    for r in spending:
        if r["category"] == "Subscriptions":
            subs[r["merchant"]] = subs.get(r["merchant"], 0) + -r["amount"]
    subs = sorted(subs.items(), key=lambda kv: -kv[1])

    show_cat = request.args.get("cat")
    txns = [r for r in this["rows"] if not show_cat or (r["category"] or "Other") == show_cat]
    txns = sorted(txns, key=lambda r: (r["day"], r["id"]), reverse=True)

    review_row = conn.execute("SELECT * FROM money_reviews WHERE month = ? ORDER BY id DESC LIMIT 1",
                              (month,)).fetchone()
    review = json.loads(review_row["data"]) if review_row else None
    profile = conn.execute("SELECT value FROM settings WHERE key = 'money_profile'").fetchone()["value"]
    goals = goal_rows(conn)
    chat = conn.execute("SELECT * FROM money_chat WHERE month = ? ORDER BY id", (month,)).fetchall()
    facts = conn.execute("SELECT * FROM money_facts ORDER BY id").fetchall()
    conn.close()

    fees = this["spend"].get("Trading (prop fees)", 0)
    payouts = this["income"].get("Trading payouts", 0)
    left_over = this["money_in"] - this["spent"]
    return render_template(
        "money.html", months=months, month=month,
        month_name=date.fromisoformat(month + "-01").strftime("%B %Y"),
        this=this, prev=prev, slices=slices, biggest=biggest, subs=subs, txns=txns,
        show_cat=show_cat, review=review, review_date=review_row["made_on"] if review_row else None,
        profile=profile, goals=goals, chat=chat, facts=facts, fees=fees, payouts=payouts, left_over=left_over,
        save_rate=round(left_over / this["money_in"] * 100) if this["money_in"] else 0,
        categories=finance.ALL_CATEGORIES, colours=finance.CATEGORY_COLOURS,
        added=request.args.get("added"), error=request.args.get("error"),
        regraded=request.args.get("regraded"),
        today=date.today().isoformat())


# Upload a bank statement CSV. Re-uploading the same file won't double count
@app.route("/upload-statement", methods=["POST"])
def upload_statement():
    file = request.files.get("statement")
    if not file or not file.filename:
        return redirect("/money?error=" + quote("Pick a CSV or PDF file first"))
    raw = file.read()
    try:
        if file.filename.lower().endswith(".pdf"):
            found = finance.read_pdf_statement(raw)   # Claude reads the PDF for you
        else:
            found = finance.read_statement(raw)
    except Exception as e:
        return redirect("/money?error=" + quote(f"{type(e).__name__}: {e}"[:300]))

    conn = get_db()
    learned = {r["merchant"]: r["category"] for r in conn.execute("SELECT * FROM merchant_rules")}
    added = []
    for t in found:
        merchant = finance.merchant_key(t["description"])
        category = learned.get(merchant) or finance.rule_category(t["description"], t["amount"])
        cur = conn.execute("""INSERT OR IGNORE INTO transactions (day, description, merchant, amount, balance, category)
                              VALUES (?, ?, ?, ?, ?, ?)""",
                           (t["day"], t["description"], merchant, t["amount"], t["balance"], category))
        if cur.rowcount and not category:
            added.append((cur.lastrowid, merchant, "in" if t["amount"] > 0 else "out", t["description"]))

    conn.commit()   # save now, so the database isn't left waiting while Claude thinks

    # Shops it doesn't know yet: ask Claude once for all of them, then remember the answers
    if added:
        unknown = {}
        for _, merchant, direction, example in added:
            unknown.setdefault((merchant, direction), example)
        try:
            answers = finance.ai_categorise([(m, d, ex) for (m, d), ex in unknown.items()])
        except Exception:
            answers = {}   # no internet / no key: they go to Other and you can fix them by hand
        for row_id, merchant, direction, _ in added:
            category = answers.get(merchant) or ("Other income" if direction == "in" else "Other")
            conn.execute("UPDATE transactions SET category = ? WHERE id = ?", (category, row_id))
            if merchant in answers:
                conn.execute("INSERT OR IGNORE INTO merchant_rules (merchant, category) VALUES (?, ?)",
                             (merchant, category))
    conn.commit()
    conn.close()
    latest = max((t["day"][:7] for t in found), default=date.today().strftime("%Y-%m"))
    return redirect(f"/money?month={latest}&added={len(found)}")


# Fix a category: changes every transaction from that shop and remembers it
@app.route("/set-category/<int:txn_id>", methods=["POST"])
def set_category(txn_id):
    category = request.form["category"]
    conn = get_db()
    row = conn.execute("SELECT merchant, day FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if row:
        conn.execute("UPDATE transactions SET category = ? WHERE merchant = ?", (category, row["merchant"]))
        conn.execute("INSERT OR REPLACE INTO merchant_rules (merchant, category) VALUES (?, ?)",
                     (row["merchant"], category))
    conn.commit()
    conn.close()
    return redirect(f"/money?month={row['day'][:7] if row else ''}#txns")


# Remove a month's transactions (if you uploaded the wrong file)
@app.route("/clear-month", methods=["POST"])
def clear_month():
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE substr(day, 1, 7) = ?", (request.form["month"],))
    conn.commit()
    conn.close()
    return redirect("/money")


@app.route("/add-goal", methods=["POST"])
def add_goal():
    conn = get_db()
    conn.execute("INSERT INTO goals (name, target, saved, by_date) VALUES (?, ?, ?, ?)",
                 (request.form["name"].strip(), float(request.form["target"]),
                  float(request.form.get("saved") or 0), request.form.get("by_date") or None))
    conn.commit()
    conn.close()
    return redirect("/money#goals")


@app.route("/update-goal/<int:goal_id>", methods=["POST"])
def update_goal(goal_id):
    conn = get_db()
    conn.execute("UPDATE goals SET saved = ?, target = ?, by_date = ? WHERE id = ?",
                 (float(request.form.get("saved") or 0), float(request.form["target"]),
                  request.form.get("by_date") or None, goal_id))
    conn.commit()
    conn.close()
    return redirect("/money#goals")


@app.route("/delete-goal/<int:goal_id>", methods=["POST"])
def delete_goal(goal_id):
    conn = get_db()
    conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    conn.commit()
    conn.close()
    return redirect("/money#goals")


@app.route("/money-profile", methods=["POST"])
def save_money_profile():
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('money_profile', ?)",
                 (request.form["profile"],))
    conn.commit()
    conn.close()
    return redirect("/money#about")


def money_context(conn, month):
    """Everything the coach needs to know about one month, as text"""
    this = month_summary(conn, month)
    prev_key = (date.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")
    prev = month_summary(conn, prev_key)
    profile = conn.execute("SELECT value FROM settings WHERE key = 'money_profile'").fetchone()["value"]
    facts = [r["fact"] for r in conn.execute("SELECT fact FROM money_facts ORDER BY id")]
    goals = goal_rows(conn)

    def lines(totals):
        return "\n".join(f"- {k}: £{v:,.2f}" for k, v in sorted(totals.items(), key=lambda kv: -kv[1])) or "- (none)"

    shops = {}
    for r in this["rows"]:
        if r["amount"] < 0 and r["category"] not in ("Transfer", "Savings"):
            key = (r["merchant"], r["category"])
            shops[key] = shops.get(key, [0, 0])
            shops[key][0] += -r["amount"]
            shops[key][1] += 1
    top = sorted(shops.items(), key=lambda kv: -kv[1][0])[:25]

    return f"""Today is {date.today().isoformat()}. The month is {month}.

ABOUT MY MONEY (Anil's own notes):
{profile}

THINGS YOU LEARNED FROM EARLIER CHATS (still true):
""" + ("\n".join(f"- {f}" for f in facts) or "- (nothing yet)") + f"""

THIS MONTH, MONEY IN (£{this['money_in']:,.2f} total):
{lines(this['income'])}

THIS MONTH, SPENDING (£{this['spent']:,.2f} total):
{lines(this['spend'])}

Moved into savings this month: £{this['saved']:,.2f}

LAST MONTH ({prev_key}) SPENDING (£{prev['spent']:,.2f} total):
{lines(prev['spend'])}

WHERE THE MONEY WENT (shop, category, total, number of payments):
""" + "\n".join(f"- {m} [{c}]: £{v[0]:,.2f} ({v[1]}x)" for (m, c), v in top) + """

GOALS:
""" + "\n".join(f"- {g['name']}: target £{g['target']:,.0f}, saved £{g['saved']:,.0f}, "
                f"by {g['by_date'] or 'no date'}"
                + (f", needs £{g['per_month']:,.0f} a month for {g['months']} months" if g['per_month'] else "")
                for g in goals)


def chat_text(conn, month):
    """This month's chat so far, as text"""
    rows = conn.execute("SELECT role, text FROM money_chat WHERE month = ? ORDER BY id", (month,)).fetchall()
    return "\n".join(f"{'Anil' if r['role'] == 'me' else 'Coach'}: {r['text']}" for r in rows)


def run_review(conn, month):
    """Asks the coach for a review (taking this month's chat into account) and saves it"""
    context = money_context(conn, month)
    talk = chat_text(conn, month)
    if talk:
        context += ("\n\nWHAT ANIL TOLD YOU IN CHAT ABOUT THIS MONTH (use it: it is context "
                    "the bank data can't show, and it should change the grade if it matters):\n" + talk)
    review = finance.ai_review(context + "\n\nReview this month.")
    conn.execute("INSERT INTO money_reviews (month, made_on, data) VALUES (?, ?, ?)",
                 (month, date.today().isoformat(), json.dumps(review)))
    conn.commit()


# Press "Review my month": the brain reads the month, your goals, your notes and your chat
@app.route("/money-review", methods=["POST"])
def money_review():
    month = request.form["month"]
    conn = get_db()
    try:
        run_review(conn, month)
    except Exception as e:
        conn.close()
        return redirect(f"/money?month={month}&error=" + quote(f"{type(e).__name__}: {e}"[:300]) + "#review")
    conn.close()
    return redirect(f"/money?month={month}#review")


# Talk it through: you say something, the coach answers, remembers lasting facts,
# and redoes the review if what you said changes the picture
@app.route("/money-chat", methods=["POST"])
def money_chat():
    month = request.form["month"]
    message = request.form["message"].strip()
    if not message:
        return redirect(f"/money?month={month}#coach")
    conn = get_db()
    review = conn.execute("SELECT data FROM money_reviews WHERE month = ? ORDER BY id DESC LIMIT 1",
                          (month,)).fetchone()
    context = money_context(conn, month)
    context += "\n\nYOUR CURRENT REVIEW OF THIS MONTH:\n" + (review["data"] if review else "(no review yet)")
    talk = chat_text(conn, month)
    if talk:
        context += "\n\nOUR CHAT SO FAR:\n" + talk
    context += "\n\nANIL NOW SAYS:\n" + message
    try:
        answer = finance.ai_chat(context)
    except Exception as e:
        conn.close()
        return redirect(f"/money?month={month}&error=" + quote(f"{type(e).__name__}: {e}"[:300]) + "#coach")

    today = date.today().isoformat()
    conn.execute("INSERT INTO money_chat (month, role, text, made_on) VALUES (?, 'me', ?, ?)", (month, message, today))
    conn.execute("INSERT INTO money_chat (month, role, text, made_on) VALUES (?, 'coach', ?, ?)",
                 (month, answer["reply"], today))
    for fact in answer["remember"]:
        if fact.strip():
            conn.execute("INSERT INTO money_facts (fact, added_on) VALUES (?, ?)", (fact.strip(), today))
    conn.commit()

    jump = "#coach"
    if answer["regrade"] and month_summary(conn, month)["rows"]:
        try:
            run_review(conn, month)
            jump = "#review"
        except Exception:
            pass   # the chat reply is saved; press "Review again" to retry
    conn.close()
    return redirect(f"/money?month={month}&regraded={1 if jump == '#review' else ''}{jump}")


# Start this month's chat again
@app.route("/clear-chat", methods=["POST"])
def clear_chat():
    month = request.form["month"]
    conn = get_db()
    conn.execute("DELETE FROM money_chat WHERE month = ?", (month,))
    conn.commit()
    conn.close()
    return redirect(f"/money?month={month}#coach")


# Forget a fact the coach remembered (or fix it by typing it into About my money)
@app.route("/delete-fact/<int:fact_id>", methods=["POST"])
def delete_fact(fact_id):
    conn = get_db()
    conn.execute("DELETE FROM money_facts WHERE id = ?", (fact_id,))
    conn.commit()
    conn.close()
    return redirect(f"/money?month={request.form.get('month', '')}#coach")



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


# ---------- Calendar (planner.py) ----------
import planner
app.register_blueprint(planner.bp)


# ---------- Study (study.py) ----------
import study
app.register_blueprint(study.bp)

# ---------- Alerts (alerts.py) ----------
import alerts
app.register_blueprint(alerts.bp)

# ---------- Weekly scorecard (scorecard.py) ----------
import scorecard
app.register_blueprint(scorecard.bp)


setup_database()

if __name__ == "__main__":
    app.run(debug=True, port=5002)
