# One-off script: reads your uni timetable export (timetable.csv) and puts
# every weekly class into the dashboard database.
# Run it with:  python3 import_timetable.py
# Safe to run again next term: it clears the old classes first.

import csv
import sqlite3
from app import setup_database   # makes sure the classes table exists

setup_database()
conn = sqlite3.connect("dashboard.db")
conn.execute("DELETE FROM classes")

added = 0
skipped = []
with open("timetable.csv", newline="") as f:
    for row in csv.DictReader(f):
        if not row["Event Id"]:
            continue   # skip blank lines at the end of the file

        weeks = row["Weeks"]
        # A class that only happens in one week isn't part of your weekly timetable
        if weeks.startswith("Week ") and ";" not in weeks and "-" not in weeks:
            skipped.append(f'{row["Module Ids"]} ({weeks}, {row["Start Time"]})')
            continue

        # Your uni numbers days 1 = Monday, the dashboard uses 0 = Monday
        day = int(row["Day"]) - 1

        # Lectures start in week 1, seminars start in week 2
        kind = "Lecture" if weeks.startswith("Weeks 1-") else "Seminar"

        conn.execute(
            "INSERT INTO classes (module, kind, day, start, end, room) VALUES (?, ?, ?, ?, ?, ?)",
            (row["Module Ids"], kind, day, row["Start Time"], row["End Time"], row["Rooms"]),
        )
        added += 1

conn.commit()
conn.close()

print(f"Added {added} classes to your timetable")
for s in skipped:
    print("Skipped one-off session:", s)
