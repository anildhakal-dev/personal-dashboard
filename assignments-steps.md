# Assignment planner: how to put it live

## 1. On your Mac
1. Unzip **assignment-planner.zip**.
2. Drag **app.py, study.py, alerts.py, scorecard.py and assignments.py** into `Documents/personal dashboard`. Choose **Replace**.
3. The zip's **templates** folder has ALL 22 pages, so it's safe either way. Open it, select all the files inside (Cmd+A) and drag them into `personal dashboard/templates`. Choose **Replace**.
4. In the VS Code terminal:
```bash
git add -A
git commit -m "Assignment planner"
git push
```

## 2. On PythonAnywhere (Bash console)
```bash
cd ~/personal-dashboard
source venv/bin/activate
git pull
```
Then **Web** tab, **Reload**. Nothing new to install.

## How it works
- New **📝 Assignments** item in the menu (also linked from the Uni page). Every assignment on the Uni page gets a plan automatically.
- Steps land on your library days (Mon, Tue), never on Saturdays. Tick them off as you go. ✎ edits a step's name or date, ✕ deletes it, and you can add your own.
- **✨ Make it specific with Claude** rewrites the steps for that assignment using your brief from Study (needs API credit).
- **↻ Re-plan from today** if you fall behind: keeps what you've ticked, re-spreads the rest.
- Alerts tells you when you're behind or a step is due today/tomorrow, and the Sunday scorecard counts mini-deadlines hit.
- To change your library days, edit `LIBRARY_DAYS = (0, 1)` at the top of assignments.py (0 = Monday ... 6 = Sunday).
