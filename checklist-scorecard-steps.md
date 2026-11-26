# 1st-class checklist + weekly scorecard: how to put it live

## 1. On your Mac
1. Unzip **checklist-scorecard.zip**.
2. Drag **app.py, study.py, alerts.py and scorecard.py** into `Documents/personal dashboard`. Choose **Replace**.
3. The zip's **templates** folder has ALL 21 pages in it this time, so it's safe either way. Open it, select all the files inside (Cmd+A) and drag them into `personal dashboard/templates`. Choose **Replace**.
4. In the VS Code terminal:
```bash
git add -A
git commit -m "1st-class checklist and weekly scorecard"
git push
```

## 2. On PythonAnywhere (Bash console)
```bash
cd ~/personal-dashboard
source venv/bin/activate
git pull
```
Then **Web** tab, **Reload**. Nothing new to install.

## Where to find them
- **1st-class checklist:** Study, open a module, Studio panel, top section. Any brief you've uploaded already gets a checklist automatically. Tap it, then **Check against my notes** (uses Claude, so it needs API credit).
- **Scorecard:** new 📊 Scorecard item in the menu. It arrives as a notification every Sunday at 8pm (change the time on the Alerts page).
