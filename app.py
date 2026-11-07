from flask import Flask, render_template, request, redirect
import sqlite3
from datetime import date, datetime, timedelta

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


setup_database()

if __name__ == "__main__":
    app.run(debug=True, port=5002)
