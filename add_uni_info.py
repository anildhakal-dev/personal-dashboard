# One-off script: gives your modules proper names and adds every deadline
# from your module handbooks. Run it with:  python3 add_uni_info.py
# Safe to run twice: it won't add the same deadline again.

import sqlite3

conn = sqlite3.connect("dashboard.db")

# 1. Swap module codes for names on the timetable
NAMES = {
    "ECO3203": "Panel Econometrics",
    "ECO3204": "Time Series Econometrics",
    "MGT3290": "Dissertation",
}
for code, name in NAMES.items():
    conn.execute("UPDATE classes SET module = ? WHERE module = ?", (name, code))

# 2. Deadlines from the handbooks: (title, module, kind, due date)
DEADLINES = [
    ("Dissertation: literature review + methodology (formative)", "Dissertation", "Assignment", "2027-01-15"),
    ("Time Series project (100%) - 12 noon", "Time Series Econometrics", "Assignment", "2027-01-18"),
    ("Panel Data project (100%) - 12 noon", "Panel Econometrics", "Assignment", "2027-01-21"),
    ("Dissertation: ethics (LEAS) approval", "Dissertation", "Assignment", "2027-01-29"),
    ("Dissertation thesis, 7000 words (100%) - 12 noon", "Dissertation", "Assignment", "2027-05-06"),
]
added = 0
for title, module, kind, due in DEADLINES:
    already = conn.execute("SELECT id FROM deadlines WHERE title = ?", (title,)).fetchone()
    if not already:
        conn.execute("INSERT INTO deadlines (title, module, kind, due) VALUES (?, ?, ?, ?)",
                     (title, module, kind, due))
        added += 1

conn.commit()
conn.close()
print(f"Renamed modules and added {added} deadlines")
