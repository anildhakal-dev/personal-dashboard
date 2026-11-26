# Everything in one go: gold theme + harsh home page + Study v2 + Alerts

This one zip has it all. You don't need any of the earlier zips.

## 1. On your Mac
1. Unzip **dashboard-update.zip** (double-click it in Downloads).
2. Open the unzipped **templates** folder, select **all the files inside** (Cmd+A) and drag them into
   `Documents/personal dashboard/templates`. Choose **Replace**. (Drag the files, not the folder.)
3. Drag **app.py**, **study.py** and **alerts.py** into `Documents/personal dashboard`. Choose **Replace**.
   (app.py is the complete revised version: nothing to edit by hand. Your config.py and dashboard.db aren't touched.)
4. Open the zip's **static** folder and drag **manifest.json** and **sw.js** into `personal dashboard/static`. Replace if asked.
5. Delete `static/himalaya.svg` if you have it (old picture, not used).

6. In the VS Code terminal:
```bash
source venv/bin/activate
pip install pywebpush pypdf
git add -A
git commit -m "Gold theme, study v2 and alerts"
git push
```

## 2. On PythonAnywhere (Bash console)
```bash
cd ~/personal-dashboard
source venv/bin/activate
git pull
pip install pywebpush pypdf python-pptx python-docx
```
Then **Web** tab, green **Reload**. Refresh the site.

## 3. Turn on phone notifications
1. On your iPhone, open the dashboard **from the home screen icon** (not Safari) and go to **Alerts**.
   (If the top bar still looks dark, delete the home screen icon and add it again from Safari first.)
2. Tap **Turn on notifications**, then **Allow**.
3. Tap **Send a test**. If it says "Problem: ...", screenshot that line and send it to me.
4. Tap **Copy** next to the timer link. On **cron-job.org** make a new job with that link, **every 15 minutes**, and save.
   Don't screenshot or share that link: it has the secret key in it.
