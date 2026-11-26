# Add the Study tab (step 1: notes + briefs + gap check)

## 1. Put the files in (on your Mac)
Unzip **study-update.zip**. Copy into `Documents/personal dashboard`, replacing when asked:
- `study.py` (new, next to app.py)
- `app.py` (only change: 3 lines at the bottom that switch on study.py)
- `templates/study.html`, `templates/study_module.html`, `templates/study_note.html` (new)
- `templates/base.html` (only change: the 📚 Study link in the menu)

## 2. Install the readers for PowerPoint and Word (Terminal, in the project folder)
```
source venv/bin/activate
pip install python-pptx python-docx
```
(PDFs and photos work without these.)

## 3. Push it
```
git add .
git commit -m "Add Study tab"
git push
```

## 4. Update the live site (PythonAnywhere, account anildhakaldev05)
Bash console:
```
cd ~/personal-dashboard
source venv/bin/activate
git pull
pip install python-pptx python-docx
```
Then Web tab → green **Reload**. The new tables create themselves.

## 5. Use it
Study → pick a module.
1. Attach each **assignment brief** (and marking criteria, together if you can) → pick "Assignment brief" → Send.
2. Then attach **lecture slides** → "Lecture slides → make notes" → Send. Takes up to a minute or two.
3. Ask anything in the chat. Tick gaps off as you close them.

Needs Anthropic credit (it ran out on 23 Nov). Top up first or you'll see a "credit balance too low" message.
