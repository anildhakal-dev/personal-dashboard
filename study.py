# Study tab: a NotebookLM-style notebook for each module, powered by Claude.
#   Sources  - your slides, seminar sheets, briefs and readings (tick which ones Claude uses)
#   Chat     - answers only from your sources, with numbered citations you can tap
#   Studio   - flashcards (with deadlines + pass mark), quizzes and mind maps
# Every source also gets its own notes + gap check against your assignments.

from flask import Blueprint, render_template, request, redirect, jsonify
from markupsafe import Markup, escape
from urllib.parse import quote
from datetime import date, datetime, timedelta
import sqlite3
import base64
import random
import json
import re
import io

import config   # your API key lives in config.py (never on GitHub)

bp = Blueprint("study", __name__)
DB = "dashboard.db"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Same colours as the Uni page, so each module keeps its colour
COLOURS = ["#c9951f", "#2f7d74", "#7a5c99", "#b5583a", "#3d6a99", "#6b7a2f", "#a8526a", "#5c6b73"]

PASS_MARK = 85          # % of green cards needed to complete a deck
DEADLINE_HOUR = 21      # decks are due at 9pm the day before the module's next lecture
MODEL = "claude-opus-5-5"
MARKING_MODEL = "claude-haiku-5-5"   # quick + cheap, only for marking typed flashcard answers
KINDS = {"lecture": "📑 Lecture", "seminar": "🗂️ Seminar", "brief": "📋 Brief / criteria",
         "reading": "📖 Reading", "other": "📄 Other"}


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


# Creates the study tables the first time the app runs
def setup_study():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS study_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            title TEXT NOT NULL,
            kind TEXT,
            filename TEXT,
            pages TEXT NOT NULL,          -- the text of each page/slide, as a JSON list
            guide TEXT,                   -- Claude's notes (or brief summary) as JSON
            active INTEGER DEFAULT 1,     -- ticked = Claude uses it
            created TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS study_src_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            source_id INTEGER,
            gap TEXT NOT NULL,
            why TEXT,
            fix TEXT,
            done INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS study_nb_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            cites TEXT,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS study_pins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            cites TEXT,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS study_decks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            title TEXT NOT NULL,
            deadline TEXT,                -- 'YYYY-MM-DD HH:MM'
            pass_mark INTEGER DEFAULT 85,
            best INTEGER,
            passed_at TEXT,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS study_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            type TEXT NOT NULL,           -- flip, mcq or typed
            front TEXT NOT NULL,
            back TEXT NOT NULL,
            options TEXT,
            answer INTEGER,
            last TEXT,                    -- green, amber or red from your last go
            seen INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS study_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            score INTEGER,
            green INTEGER,
            total INTEGER,
            finished TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS study_quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            title TEXT NOT NULL,
            data TEXT NOT NULL,
            best INTEGER,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS study_maps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            title TEXT NOT NULL,
            data TEXT NOT NULL,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


# ---------- Small helpers ----------

def all_modules(conn):
    """Every module from your timetable, deadlines and sources, each with its colour."""
    names = set()
    for table in ("classes", "deadlines"):
        try:
            names |= {r[0] for r in conn.execute(f"SELECT DISTINCT module FROM {table}") if r[0]}
        except sqlite3.OperationalError:
            pass
    colour_of = {m: COLOURS[i % len(COLOURS)] for i, m in enumerate(sorted(names))}   # same as Uni page
    for r in conn.execute("SELECT DISTINCT module FROM study_sources"):
        if r[0] not in colour_of:
            colour_of[r[0]] = COLOURS[len(colour_of) % len(COLOURS)]
    return colour_of


def next_class(conn, module, after=None, lectures_only=False):
    """The next time this module is on your timetable: (datetime, kind) or (None, None)."""
    now = after or datetime.now()
    rows = conn.execute("SELECT * FROM classes WHERE module = ?", (module,)).fetchall()
    if lectures_only:
        lectures = [c for c in rows if "lecture" in (c["kind"] or "").lower()]
        rows = lectures or rows   # no lecture on the timetable? use any class
    best = (None, None)
    for c in rows:
        hour, minute = (int(x) for x in c["start"].split(":")[:2])
        day = now.date() + timedelta(days=(c["day"] - now.weekday()) % 7)
        when = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
        if when <= now:
            when += timedelta(days=7)
        if best[0] is None or when < best[0]:
            best = (when, c["kind"] or "Class")
    return best


def deck_deadline(conn, module):
    """9pm the day before the module's next lecture (or 3 days from now if no timetable)."""
    now = datetime.now()
    lecture, _ = next_class(conn, module, lectures_only=True)
    if not lecture:
        return (now + timedelta(days=3)).replace(hour=DEADLINE_HOUR, minute=0)
    due = (lecture - timedelta(days=1)).replace(hour=DEADLINE_HOUR, minute=0)
    if due <= now:   # too late for that one, so aim for the lecture after
        lecture, _ = next_class(conn, module, after=lecture + timedelta(minutes=1), lectures_only=True)
        due = (lecture - timedelta(days=1)).replace(hour=DEADLINE_HOUR, minute=0)
    return due


def nice_when(when):
    if not when:
        return ""
    days = (when.date() - date.today()).days
    day = "Today" if days == 0 else "Tomorrow" if days == 1 else when.strftime("%a %d %b")
    time = "9pm" if when.strftime("%H:%M") == "21:00" else when.strftime("%H:%M")
    return f"{day} {time}"


def deck_status(deck):
    """Traffic light for a deck: done, due or overdue."""
    if deck["passed_at"]:
        return "green", "Done ✓"
    due = datetime.strptime(deck["deadline"], "%Y-%m-%d %H:%M") if deck["deadline"] else None
    if due and due < datetime.now():
        return "red", "Overdue"
    if due and due - datetime.now() < timedelta(hours=36):
        return "amber", "Due " + nice_when(due)
    return "grey", ("Due " + nice_when(due)) if due else "No deadline"


def module_deadlines(conn, module):
    out = []
    for d in conn.execute("SELECT * FROM deadlines WHERE module = ? AND done = 0 ORDER BY due", (module,)):
        days = (date.fromisoformat(d["due"]) - date.today()).days
        out.append({"title": d["title"], "kind": d["kind"], "days": days,
                    "nice": date.fromisoformat(d["due"]).strftime("%a %d %b")})
    return out


# ---------- Turning Claude's text into HTML ----------

def inline_md(text):
    text = str(escape(text))
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)([^*]+?)\*(?!\w)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # citation markers like ⟦3⟧ become little tappable numbers
    text = re.sub(r"⟦(\d+)⟧", r'<button type="button" class="cite" data-n="\1">\1</button>', text)
    return text


@bp.app_template_filter("md")
def md(text):
    html, list_tag = [], None
    for line in (text or "").split("\n"):
        s = line.strip()
        bullet = re.match(r"^[-*•]\s+(.*)", s)
        number = re.match(r"^\d+[.)]\s+(.*)", s)
        tag = "ul" if bullet else "ol" if number else None
        if list_tag and tag != list_tag:
            html.append(f"</{list_tag}>")
            list_tag = None
        if tag:
            if not list_tag:
                html.append(f"<{tag}>")
                list_tag = tag
            html.append(f"<li>{inline_md((bullet or number).group(1))}</li>")
        elif s.startswith("#"):
            html.append(f"<h4>{inline_md(s.lstrip('#').strip())}</h4>")
        elif s:
            html.append(f"<p>{inline_md(s)}</p>")
    if list_tag:
        html.append(f"</{list_tag}>")
    return Markup("".join(html))


@bp.app_template_filter("mdi")
def mdi(text):
    return Markup(inline_md(text or ""))


# ---------- Reading your files ----------

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}


def read_file(name, raw):
    """Returns (pages, blocks): the text of each page if we can read it here,
    and what to show Claude so it can see the original (slides, diagrams, photos)."""
    ext = "." + name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext == ".pdf":
        pages = []
        try:
            from pypdf import PdfReader
            pages = [(p.extract_text() or "").strip() for p in PdfReader(io.BytesIO(raw)).pages]
        except ImportError:
            pass   # no pypdf: Claude will copy the text out instead
        except Exception:
            pages = []
        if sum(len(p) for p in pages) < 40 * max(1, len(pages)) // 4:
            pages = []   # scanned/picture PDF: let Claude read it
        return pages, [{"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.standard_b64encode(raw).decode()}}]
    if ext in IMAGE_TYPES:
        return [], [{"type": "image", "source": {
            "type": "base64", "media_type": IMAGE_TYPES[ext],
            "data": base64.standard_b64encode(raw).decode()}}]
    if ext == ".pptx":
        try:
            from pptx import Presentation
        except ImportError:
            raise ValueError("To read PowerPoint files run: pip install python-pptx (or save the slides as PDF).")
        pages = []
        for slide in Presentation(io.BytesIO(raw)).slides:
            bits = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                bits.append("Speaker notes: " + slide.notes_slide.notes_text_frame.text)
            pages.append("\n".join(bits))
        return pages, []
    if ext == ".docx":
        try:
            from docx import Document
        except ImportError:
            raise ValueError("To read Word files run: pip install python-docx (or save it as PDF).")
        doc = Document(io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        # Word files have no pages, so split into chunks of about 40 lines
        return ["\n".join(parts[i:i + 40]) for i in range(0, len(parts), 40)] or [""], []
    if ext in (".txt", ".md", ".csv"):
        lines = raw.decode("utf-8", "ignore").split("\n")
        return ["\n".join(lines[i:i + 60]) for i in range(0, len(lines), 60)], []
    raise ValueError(f"I can't read {name}. Use PDF, PowerPoint, Word, a photo or text.")


def source_document(src, cite=True):
    """A source as a document Claude can quote from: one block per page, so a citation tells us the page."""
    pages = json.loads(src["pages"])
    doc = {"type": "document",
           "source": {"type": "content", "content": [
               {"type": "text", "text": f"[Page {i}] {p}" if p.strip() else f"[Page {i}] (blank)"}
               for i, p in enumerate(pages, 1)]},
           "title": src["title"]}
    if cite:
        doc["citations"] = {"enabled": True}
    return doc


# ---------- Claude ----------

def _client():
    import anthropic   # only loaded when needed
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def system_prompt(module):
    return f"""You are Claude, working inside Anil's study notebook for the module "{module}".
Anil is a final-year university student aiming for a 1st (70%+) in every assignment and exam.
Their sources (lecture slides, seminar sheets, assignment briefs, marking criteria, readings) are attached.

How to answer:
- Be direct and specific, like a sharp classmate who has read everything. No motivational filler,
  no generic study tips. Answer exactly what is asked.
- Ground everything in the sources and use the module's own notation. If the sources don't cover
  something, say so plainly, then give the standard textbook answer labelled "(not in your sources)".
- Connect to what the assignments and marking criteria reward, and point out gaps.
- Write maths in plain text (β0, R², Var(u|x), E[y]) so it reads on a phone.
- UK English. Short paragraphs and bullet points. **Bold** key terms."""


def module_context(conn, module):
    """Plain-text facts about the module (timetable, deadlines, notes made, open gaps)."""
    lines = [f"Today is {date.today().strftime('%A %d %B %Y')}."]
    classes = conn.execute("SELECT * FROM classes WHERE module = ? ORDER BY day, start", (module,)).fetchall()
    if classes:
        lines.append("Timetable: " + "; ".join(
            f"{DAYS[c['day']]} {c['start']}-{c['end']} {c['kind'] or ''}".strip() for c in classes))
    for d in module_deadlines(conn, module):
        lines.append(f"Deadline: {d['title']} ({d['kind']}) due {d['nice']}, in {d['days']} days.")
    for s in conn.execute("SELECT * FROM study_sources WHERE module = ? AND kind = 'brief' AND guide IS NOT NULL", (module,)):
        lines.append(f"\nASSIGNMENT SUMMARY ({s['title']}): {s['guide']}")
    notes = conn.execute("SELECT title, guide FROM study_sources WHERE module = ? AND kind != 'brief' ORDER BY id", (module,)).fetchall()
    if notes:
        lines.append("\nSOURCES SO FAR (oldest first):")
        for n in notes:
            g = json.loads(n["guide"] or "{}")
            lines.append(f"- {n['title']}: {g.get('summary', '')}")
    gaps = conn.execute("SELECT gap FROM study_src_gaps WHERE module = ? AND done = 0", (module,)).fetchall()
    if gaps:
        lines.append("\nOPEN GAPS: " + "; ".join(g["gap"] for g in gaps))
    return "\n".join(lines)


def _create(**kwargs):
    response = _client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        # If Claude ever declines, the API quietly retries on a backup model
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        **kwargs,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this one. Try again.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("The answer was too long and got cut off. Try with fewer sources ticked.")
    return response


def ask_json(module, content, schema, effort="medium"):
    response = _create(system=system_prompt(module),
                       messages=[{"role": "user", "content": content}],
                       output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def _obj(props, required=None):
    return {"type": "object", "properties": props,
            "required": required or list(props), "additionalProperties": False}


STR = {"type": "string"}
STRS = {"type": "array", "items": STR}

NOTES_SCHEMA = _obj({
    "title": {"type": "string", "description": "short, e.g. 'Lecture 7: Heteroskedasticity'"},
    "summary": {"type": "string", "description": "1-2 sentences: what this covers"},
    "pages": {"type": "array", "items": STR, "description": "ONLY if asked to copy out the text: one entry per page/slide, else []"},
    "sections": {"type": "array", "items": _obj({"heading": STR, "points": STRS})},
    "key_terms": {"type": "array", "items": _obj({"term": STR, "meaning": STR})},
    "formulas": {"type": "array", "items": _obj({"name": STR, "formula": STR, "meaning": STR})},
    "assignment_links": {"type": "array", "items": _obj({"assignment": STR, "how": STR})},
    "gaps": {"type": "array", "items": _obj({"gap": STR, "why": STR, "fix": STR})},
})

BRIEF_SCHEMA = _obj({
    "title": STR,
    "summary": {"type": "string", "description": "1-2 sentences: the assignment and its biggest demand"},
    "pages": {"type": "array", "items": STR, "description": "ONLY if asked to copy out the text: one entry per page, else []"},
    "kind": {"type": "string", "enum": ["Assignment", "Exam", "Coursework", "Presentation", "Other"]},
    "due": {"type": "string", "description": "YYYY-MM-DD, or empty if not stated"},
    "weighting": {"type": "string", "description": "e.g. '50% of module', or empty"},
    "length": {"type": "string", "description": "word count / time limit, or empty"},
    "task": {"type": "string", "description": "2-3 sentences: exactly what must be produced"},
    "requirements": {"type": "array", "items": STR, "description": "every must-do from the brief"},
    "first_class": {"type": "array", "items": STR, "description": "what a 1st (70%+) looks like, concrete, from the criteria"},
    "topics_needed": {"type": "array", "items": STR, "description": "knowledge and skills needed to do it"},
})

COPY_TEXT = ("\nIMPORTANT: I couldn't extract the text from this file, so fill \"pages\" with the full "
             "text of every page/slide in order (formulas in plain text, describe charts in one line).")

NOTES_ASK = """This new source is {kind} material for my module. Make my notes for it.
- summary: what it covers.
- sections: the content in order, as tight bullet points. Every definition, model, assumption,
  result and worked example that matters. Keep the slides' notation. No waffle.
- key_terms and formulas: the ones I must know (formula in plain text, then what each part means).
- assignment_links: for each assignment in the context, exactly which parts feed into which
  requirement or marking criterion. Empty only if none apply.
- gaps: GAP CHECK. Compare what the assignments and criteria need against what this and my earlier
  sources cover. Concrete things I still need (a method not taught yet, a skill the criteria reward,
  a concept only skimmed, a reading the brief expects). For each: why it matters for the mark, and the
  fastest way to close it. Don't repeat open gaps already listed.
- pages: [] unless told otherwise below.{copy}"""

BRIEF_ASK = """This new source is an assignment brief and/or marking criteria. Pull out exactly what the
assignment wants and what earns a 1st. Be concrete: quote numbers, word counts, required sections and
criteria wording. pages: [] unless told otherwise below.{copy}"""


@bp.route("/study/source", methods=["POST"])
def add_source():
    """Upload ONE file (the page sends several one after another)."""
    module = request.form["module"].strip()
    kind = request.form.get("kind", "lecture")
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="No file"), 400
    conn = get_db()
    try:
        pages, blocks = read_file(f.filename, f.read())
        need_copy = not pages
        if need_copy and not blocks:
            raise ValueError("That file looks empty.")
        show = blocks or [{"type": "text", "text": "\n\n".join(f"[Page {i}] {p}" for i, p in enumerate(pages, 1))}]
        ask = (BRIEF_ASK if kind == "brief" else NOTES_ASK).format(
            kind=kind, copy=COPY_TEXT if need_copy else "")
        content = ([{"type": "text", "text": "ABOUT THIS MODULE\n" + module_context(conn, module)},
                    {"type": "text", "text": f"NEW SOURCE: {f.filename}"}] + show +
                   [{"type": "text", "text": ask}])
        guide = ask_json(module, content, BRIEF_SCHEMA if kind == "brief" else NOTES_SCHEMA)
        copied = guide.pop("pages", [])
        if need_copy:
            pages = copied or ["(Claude couldn't copy any text from this file)"]
        gaps = guide.pop("gaps", [])
        cur = conn.execute("INSERT INTO study_sources (module, title, kind, filename, pages, guide) VALUES (?, ?, ?, ?, ?, ?)",
                           (module, guide["title"], kind, f.filename, json.dumps(pages, ensure_ascii=False),
                            json.dumps(guide, ensure_ascii=False)))
        for g in gaps:
            conn.execute("INSERT INTO study_src_gaps (module, source_id, gap, why, fix) VALUES (?, ?, ?, ?, ?)",
                         (module, cur.lastrowid, g["gap"], g["why"], g["fix"]))
        conn.commit()
        return jsonify(ok=True, id=cur.lastrowid, title=guide["title"])
    except Exception as e:   # tell the page what went wrong instead of crashing
        return jsonify(error=f"{f.filename}: {type(e).__name__}: {e}"[:400]), 500
    finally:
        conn.close()


# ---------- Pages ----------

@bp.route("/study")
def study_home():
    conn = get_db()
    modules = []
    for name, colour in all_modules(conn).items():
        when, kind = next_class(conn, name)
        decks = conn.execute("SELECT * FROM study_decks WHERE module = ? AND passed_at IS NULL ORDER BY deadline", (name,)).fetchall()
        deck = None
        if decks:
            light, label = deck_status(decks[0])
            deck = {"title": decks[0]["title"], "light": light, "label": label}
        deadlines = module_deadlines(conn, name)
        modules.append({
            "name": name, "colour": colour,
            "next": f"{kind} {nice_when(when)}" if when else "",
            "sources": conn.execute("SELECT COUNT(*) FROM study_sources WHERE module = ?", (name,)).fetchone()[0],
            "gaps": conn.execute("SELECT COUNT(*) FROM study_src_gaps WHERE module = ? AND done = 0", (name,)).fetchone()[0],
            "decks_due": len(decks), "deck": deck,
            "deadline": deadlines[0] if deadlines else None,
        })
    conn.close()
    return render_template("study.html", modules=modules)


@bp.route("/study/module")
def notebook():
    name = request.args.get("m", "").strip()
    if not name:
        return redirect("/study")
    conn = get_db()
    colour = all_modules(conn).get(name, COLOURS[0])
    when, kind = next_class(conn, name)
    sources = conn.execute("SELECT id, title, kind, filename, active FROM study_sources WHERE module = ? ORDER BY id", (name,)).fetchall()
    chat = [dict(m, cites=json.loads(m["cites"] or "[]")) for m in
            conn.execute("SELECT * FROM study_nb_chat WHERE module = ? ORDER BY id", (name,))]
    pins = conn.execute("SELECT id, title FROM study_pins WHERE module = ? ORDER BY id DESC", (name,)).fetchall()
    decks = []
    for d in conn.execute("SELECT * FROM study_decks WHERE module = ? ORDER BY id DESC", (name,)):
        light, label = deck_status(d)
        count = conn.execute("SELECT COUNT(*) FROM study_cards WHERE deck_id = ?", (d["id"],)).fetchone()[0]
        decks.append({"id": d["id"], "title": d["title"], "light": light, "label": label, "best": d["best"], "count": count})
    quizzes = conn.execute("SELECT id, title, best FROM study_quizzes WHERE module = ? ORDER BY id DESC", (name,)).fetchall()
    maps = conn.execute("SELECT id, title FROM study_maps WHERE module = ? ORDER BY id DESC", (name,)).fetchall()
    gaps = conn.execute("SELECT * FROM study_src_gaps WHERE module = ? AND done = 0 ORDER BY id", (name,)).fetchall()
    deadlines = module_deadlines(conn, name)
    due = deck_deadline(conn, name)
    conn.close()
    return render_template("study_module.html", name=name, colour=colour, kinds=KINDS,
                           next=f"{kind} {nice_when(when)}" if when else "",
                           sources=sources, chat=chat, pins=pins, decks=decks, quizzes=quizzes,
                           maps=maps, gaps=gaps, deadlines=deadlines, next_due=nice_when(due),
                           prefill=request.args.get("q", ""), error=request.args.get("error"))


# The notes + gap check for one source (shown in a pop-up)
@bp.route("/study/source/<int:source_id>")
def source_guide(source_id):
    conn = get_db()
    src = conn.execute("SELECT * FROM study_sources WHERE id = ?", (source_id,)).fetchone()
    if not src:
        conn.close()
        return "Not found", 404
    gaps = conn.execute("SELECT * FROM study_src_gaps WHERE source_id = ? ORDER BY id", (source_id,)).fetchall()
    conn.close()
    return render_template("study_source.html", src=src, g=json.loads(src["guide"] or "{}"),
                           pages=json.loads(src["pages"]), gaps=gaps, kinds=KINDS,
                           page=request.args.get("page", type=int))


@bp.route("/study/source/<int:source_id>/toggle", methods=["POST"])
def toggle_source(source_id):
    conn = get_db()
    conn.execute("UPDATE study_sources SET active = 1 - active WHERE id = ?", (source_id,))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@bp.route("/study/sources/all", methods=["POST"])
def toggle_all_sources():
    conn = get_db()
    conn.execute("UPDATE study_sources SET active = ? WHERE module = ?",
                 (1 if request.form.get("on") == "1" else 0, request.form["module"]))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@bp.route("/study/source/<int:source_id>/delete", methods=["POST"])
def delete_source(source_id):
    conn = get_db()
    row = conn.execute("SELECT module FROM study_sources WHERE id = ?", (source_id,)).fetchone()
    conn.execute("DELETE FROM study_sources WHERE id = ?", (source_id,))
    conn.execute("DELETE FROM study_src_gaps WHERE source_id = ?", (source_id,))
    conn.commit()
    conn.close()
    return redirect("/study/module?m=" + quote(row["module"]) if row else "/study")


@bp.route("/study/gap/<int:gap_id>", methods=["POST"])
def toggle_gap(gap_id):
    conn = get_db()
    conn.execute("UPDATE study_src_gaps SET done = 1 - done WHERE id = ?", (gap_id,))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------- Chat with citations ----------

def citation_answer(response, sources):
    """Turns Claude's reply into text with ⟦n⟧ markers + a list of what each number points to."""
    text, cites, seen = "", [], {}
    for block in response.content:
        if block.type != "text":
            continue
        text += block.text
        marks = []
        for c in (getattr(block, "citations", None) or []):
            idx = getattr(c, "document_index", None)
            if idx is None or idx >= len(sources):
                continue
            src = sources[idx]
            page = (getattr(c, "start_block_index", 0) or 0) + 1
            key = (src["id"], page)
            if key not in seen:
                quote_text = re.sub(r"^\[Page \d+\]\s*", "", (getattr(c, "cited_text", "") or "").strip())
                cites.append({"n": len(cites) + 1, "source_id": src["id"], "title": src["title"],
                              "page": page, "quote": quote_text[:600]})
                seen[key] = len(cites)
            if seen[key] not in marks:
                marks.append(seen[key])
        text += "".join(f"⟦{n}⟧" for n in marks)
    return text.strip(), cites


@bp.route("/study/ask", methods=["POST"])
def ask():
    data = request.get_json()
    module, message = data["module"].strip(), data["message"].strip()
    if not message:
        return jsonify(error="Type a question first."), 400
    conn = get_db()
    try:
        sources = conn.execute("SELECT * FROM study_sources WHERE module = ? AND active = 1 ORDER BY id", (module,)).fetchall()
        docs = [source_document(s) for s in sources]
        if docs:   # cache the sources, so follow-up questions are much cheaper
            docs[-1]["cache_control"] = {"type": "ephemeral"}
        first = docs + [{"type": "text", "text": "ABOUT THIS MODULE\n" + module_context(conn, module)}]
        if not sources:
            first.append({"type": "text", "text": "(No sources are ticked, so answer from general knowledge and say so.)"})
        # Earlier chat (text only), so follow-ups make sense
        past = conn.execute("SELECT role, content FROM study_nb_chat WHERE module = ? ORDER BY id DESC LIMIT 12",
                            (module,)).fetchall()[::-1]
        history = []
        for p in past:
            clean = re.sub(r"⟦\d+⟧", "", p["content"])
            if history and history[-1]["role"] == p["role"]:
                history[-1]["content"] += "\n\n" + clean
            else:
                history.append({"role": p["role"], "content": clean})
        if history and history[0]["role"] == "assistant":
            history.pop(0)
        if history and history[-1]["role"] == "user":
            history.append({"role": "assistant", "content": "(no reply)"})
        if history:
            history[0] = {"role": "user", "content": first + [{"type": "text", "text": history[0]["content"]}]}
            messages = history + [{"role": "user", "content": message}]
        else:
            messages = [{"role": "user", "content": first + [{"type": "text", "text": message}]}]
        response = _create(system=system_prompt(module), messages=messages, output_config={"effort": "medium"})
        answer, cites = citation_answer(response, sources)
    except Exception as e:
        conn.close()
        return jsonify(error=f"{type(e).__name__}: {e}"[:400]), 500

    conn.execute("INSERT INTO study_nb_chat (module, role, content) VALUES (?, 'user', ?)", (module, message))
    cur = conn.execute("INSERT INTO study_nb_chat (module, role, content, cites) VALUES (?, 'assistant', ?, ?)",
                       (module, answer, json.dumps(cites, ensure_ascii=False)))
    conn.commit()
    msgs = [{"id": None, "role": "user", "content": message, "cites": []},
            {"id": cur.lastrowid, "role": "assistant", "content": answer, "cites": cites}]
    conn.close()
    return jsonify(html=render_template("_study_msgs.html", chat=msgs))


@bp.route("/study/clear-chat", methods=["POST"])
def clear_chat():
    module = request.form["module"]
    conn = get_db()
    conn.execute("DELETE FROM study_nb_chat WHERE module = ?", (module,))
    conn.commit()
    conn.close()
    return redirect("/study/module?m=" + quote(module))


# ---------- Saved notes (pinned answers) ----------

@bp.route("/study/pin", methods=["POST"])
def pin():
    msg_id = request.get_json()["id"]
    conn = get_db()
    m = conn.execute("SELECT * FROM study_nb_chat WHERE id = ?", (msg_id,)).fetchone()
    question = conn.execute("SELECT content FROM study_nb_chat WHERE module = ? AND id < ? AND role = 'user' "
                            "ORDER BY id DESC LIMIT 1", (m["module"], msg_id)).fetchone()
    title = (question["content"] if question else re.sub(r"[*⟦⟧\d]", "", m["content"]))[:70]
    cur = conn.execute("INSERT INTO study_pins (module, title, content, cites) VALUES (?, ?, ?, ?)",
                       (m["module"], title, m["content"], m["cites"]))
    conn.commit()
    conn.close()
    return jsonify(ok=True, id=cur.lastrowid, title=title)


@bp.route("/study/pin/<int:pin_id>")
def show_pin(pin_id):
    conn = get_db()
    p = conn.execute("SELECT * FROM study_pins WHERE id = ?", (pin_id,)).fetchone()
    conn.close()
    if not p:
        return "Not found", 404
    return render_template("_study_pin.html", p=p, cites=json.loads(p["cites"] or "[]"))


@bp.route("/study/pin/<int:pin_id>/delete", methods=["POST"])
def delete_pin(pin_id):
    conn = get_db()
    row = conn.execute("SELECT module FROM study_pins WHERE id = ?", (pin_id,)).fetchone()
    conn.execute("DELETE FROM study_pins WHERE id = ?", (pin_id,))
    conn.commit()
    conn.close()
    return redirect("/study/module?m=" + quote(row["module"]) if row else "/study")


# ---------- Studio: flashcards, quiz, mind map ----------

CARDS_SCHEMA = _obj({
    "title": {"type": "string", "description": "short deck name, e.g. 'Heteroskedasticity'"},
    "cards": {"type": "array", "items": _obj({
        "type": {"type": "string", "enum": ["flip", "mcq", "typed"]},
        "front": STR,
        "back": {"type": "string", "description": "the full answer (for mcq: the correct option + one-line why)"},
        "options": {"type": "array", "items": STR, "description": "mcq: exactly 4 options; otherwise []"},
        "answer": {"type": "integer", "description": "mcq: index 0-3 of the correct option; otherwise -1"},
    })},
})

QUIZ_SCHEMA = _obj({
    "title": STR,
    "questions": {"type": "array", "items": _obj({
        "question": STR,
        "options": {"type": "array", "items": STR, "description": "exactly 4"},
        "answer": {"type": "integer", "description": "index 0-3"},
        "explanation": {"type": "string", "description": "why it's right, and why the tempting wrong one is wrong"},
        "source": {"type": "string", "description": "source title + page it comes from"},
    })},
})

LEAF = _obj({"title": STR, "note": STR})
MAP_SCHEMA = _obj({
    "title": STR,
    "branches": {"type": "array", "items": _obj({
        "title": STR, "note": STR,
        "children": {"type": "array", "items": _obj({
            "title": STR, "note": STR,
            "children": {"type": "array", "items": LEAF}})}})},
})

ASKS = {
    "flashcards": """Make a flashcard deck from these sources (about 20 cards, more if there's lots to learn).
Mix the types: about 40% "flip" (I think, turn over, mark myself), 30% "mcq", 30% "typed".
- Cover what the assignments and exams reward, plus every open gap. Definitions, assumptions, formulas,
  interpretations, when to use which method, common traps.
- typed: the answer must be short (a term, a symbol, a number, a formula) so it can be typed on a phone.
- mcq: 4 plausible options, one clearly right.
- Fronts are questions, not statements. Use the sources' notation in plain text.""",
    "quiz": """Make a 10-question multiple-choice quiz from these sources, at the level of a final-year exam.
Test understanding and application, not just recall: interpretations, which method fits a scenario,
spotting a wrong statement, what follows if an assumption fails. Every option plausible.""",
    "mindmap": """Make a mind map of these sources: the main topics as branches, then subtopics, then key details.
4-8 branches, each with 2-5 children; add a third level only where it helps. Titles 1-5 words.
"note" is one short line explaining that node (formulas in plain text).""",
}


@bp.route("/study/make", methods=["POST"])
def make():
    module = request.form["module"].strip()
    what = request.form["what"]
    focus = request.form.get("focus", "").strip()
    back = "/study/module?m=" + quote(module)
    conn = get_db()
    sources = conn.execute("SELECT * FROM study_sources WHERE module = ? AND active = 1 ORDER BY id", (module,)).fetchall()
    if not sources:
        conn.close()
        return redirect(back + "&error=" + quote("Tick at least one source first."))
    try:
        content = [source_document(s, cite=False) for s in sources]
        content += [{"type": "text", "text": "ABOUT THIS MODULE\n" + module_context(conn, module)},
                    {"type": "text", "text": ASKS[what] + (f"\nFocus on: {focus}" if focus else "")}]
        schema = {"flashcards": CARDS_SCHEMA, "quiz": QUIZ_SCHEMA, "mindmap": MAP_SCHEMA}[what]
        data = ask_json(module, content, schema)
    except Exception as e:
        conn.close()
        return redirect(back + "&error=" + quote(f"{type(e).__name__}: {e}"[:300]))

    if what == "flashcards":
        due = deck_deadline(conn, module).strftime("%Y-%m-%d %H:%M")
        cur = conn.execute("INSERT INTO study_decks (module, title, deadline, pass_mark) VALUES (?, ?, ?, ?)",
                           (module, data["title"], due, PASS_MARK))
        for c in data["cards"]:
            mcq = c["type"] == "mcq" and len(c["options"]) >= 2 and 0 <= c["answer"] < len(c["options"])
            conn.execute("INSERT INTO study_cards (deck_id, type, front, back, options, answer) VALUES (?, ?, ?, ?, ?, ?)",
                         (cur.lastrowid, c["type"] if c["type"] != "mcq" or mcq else "flip", c["front"], c["back"],
                          json.dumps(c["options"] if mcq else [], ensure_ascii=False), c["answer"] if mcq else -1))
        url = f"/study/deck/{cur.lastrowid}"
    elif what == "quiz":
        cur = conn.execute("INSERT INTO study_quizzes (module, title, data) VALUES (?, ?, ?)",
                           (module, data["title"], json.dumps(data, ensure_ascii=False)))
        url = f"/study/quiz/{cur.lastrowid}"
    else:
        cur = conn.execute("INSERT INTO study_maps (module, title, data) VALUES (?, ?, ?)",
                           (module, data["title"], json.dumps(data, ensure_ascii=False)))
        url = f"/study/map/{cur.lastrowid}"
    conn.commit()
    conn.close()
    return redirect(url)


# --- Flashcards ---

@bp.route("/study/deck/<int:deck_id>")
def deck_page(deck_id):
    conn = get_db()
    deck = conn.execute("SELECT * FROM study_decks WHERE id = ?", (deck_id,)).fetchone()
    if not deck:
        conn.close()
        return redirect("/study")
    cards = [dict(c, options=json.loads(c["options"] or "[]")) for c in
             conn.execute("SELECT * FROM study_cards WHERE deck_id = ?", (deck_id,))]
    runs = conn.execute("SELECT * FROM study_runs WHERE deck_id = ? ORDER BY id DESC LIMIT 5", (deck_id,)).fetchall()
    colour = all_modules(conn).get(deck["module"], COLOURS[0])
    conn.close()
    light, label = deck_status(deck)
    due = deck["deadline"] or ""
    return render_template("study_deck.html", deck=deck, cards=cards, runs=runs, colour=colour,
                           light=light, label=label, due_date=due[:10], due_time=due[11:16])


@bp.route("/study/deck/<int:deck_id>/deadline", methods=["POST"])
def set_deadline(deck_id):
    conn = get_db()
    conn.execute("UPDATE study_decks SET deadline = ? WHERE id = ?",
                 (f"{request.form['date']} {request.form.get('time') or '21:00'}", deck_id))
    conn.commit()
    conn.close()
    return redirect(f"/study/deck/{deck_id}")


@bp.route("/study/card/<int:card_id>", methods=["POST"])
def card_result(card_id):
    result = request.get_json()["result"]
    if result not in ("green", "amber", "red"):
        return jsonify(error="bad result"), 400
    conn = get_db()
    conn.execute("UPDATE study_cards SET last = ?, seen = seen + 1 WHERE id = ?", (result, card_id))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "θ": "theta", "λ": "lambda",
         "μ": "mu", "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi", "χ": "chi", "ω": "omega",
         "²": "2", "³": "3", "₀": "0", "₁": "1", "₂": "2", "₃": "3", "ˆ": "hat", "^": ""}


def normalise(text):
    t = text.lower().strip()
    for k, v in GREEK.items():
        t = t.replace(k, v)
    return re.sub(r"[^a-z0-9]", "", t)


MARK_SCHEMA = _obj({
    "verdict": {"type": "string", "enum": ["green", "amber", "red"]},
    "feedback": {"type": "string", "description": "one short sentence"},
})


@bp.route("/study/mark", methods=["POST"])
def mark_typed():
    data = request.get_json()
    conn = get_db()
    card = conn.execute("SELECT * FROM study_cards WHERE id = ?", (data["card_id"],)).fetchone()
    conn.close()
    typed = (data.get("answer") or "").strip()
    if not typed:
        return jsonify(verdict="red", feedback="No answer given.")
    if normalise(typed) == normalise(card["back"]):
        return jsonify(verdict="green", feedback="Spot on.")
    try:
        response = _client().beta.messages.create(
            model=MARKING_MODEL, max_tokens=2000,
            system=("You mark a university student's typed flashcard answer by MEANING, not spelling. "
                    "They type on a phone without special characters, so B0 / b0 / beta0 / beta 0 all mean β0, "
                    "R2 means R², sigma^2 means σ², E(u|x) means E[u|x]. Ignore typos and extra words. "
                    "green = correct, amber = partly right or missing a key part, red = wrong."),
            messages=[{"role": "user", "content":
                       f"Question: {card['front']}\nModel answer: {card['back']}\nStudent typed: {typed}"}],
            output_config={"format": {"type": "json_schema", "schema": MARK_SCHEMA}},
        )
        return jsonify(json.loads(next(b.text for b in response.content if b.type == "text")))
    except Exception as e:
        return jsonify(verdict=None, feedback=f"Couldn't mark it ({type(e).__name__}). Mark yourself."), 200


@bp.route("/study/deck/<int:deck_id>/finish", methods=["POST"])
def finish_deck(deck_id):
    data = request.get_json()
    green, total = int(data["green"]), int(data["total"])
    score = round(100 * green / total) if total else 0
    conn = get_db()
    deck = conn.execute("SELECT * FROM study_decks WHERE id = ?", (deck_id,)).fetchone()
    conn.execute("INSERT INTO study_runs (deck_id, score, green, total) VALUES (?, ?, ?, ?)", (deck_id, score, green, total))
    passed = score >= (deck["pass_mark"] or PASS_MARK) and data.get("full", True)
    conn.execute("UPDATE study_decks SET best = MAX(COALESCE(best, 0), ?) WHERE id = ?", (score, deck_id))
    if passed and not deck["passed_at"]:
        conn.execute("UPDATE study_decks SET passed_at = ? WHERE id = ?",
                     (datetime.now().strftime("%Y-%m-%d %H:%M"), deck_id))
    conn.commit()
    conn.close()
    return jsonify(score=score, passed=bool(passed), pass_mark=deck["pass_mark"] or PASS_MARK)


@bp.route("/study/deck/<int:deck_id>/delete", methods=["POST"])
def delete_deck(deck_id):
    conn = get_db()
    row = conn.execute("SELECT module FROM study_decks WHERE id = ?", (deck_id,)).fetchone()
    conn.execute("DELETE FROM study_decks WHERE id = ?", (deck_id,))
    conn.execute("DELETE FROM study_cards WHERE deck_id = ?", (deck_id,))
    conn.execute("DELETE FROM study_runs WHERE deck_id = ?", (deck_id,))
    conn.commit()
    conn.close()
    return redirect("/study/module?m=" + quote(row["module"]) if row else "/study")


# --- Quiz ---

@bp.route("/study/quiz/<int:quiz_id>")
def quiz_page(quiz_id):
    conn = get_db()
    q = conn.execute("SELECT * FROM study_quizzes WHERE id = ?", (quiz_id,)).fetchone()
    if not q:
        conn.close()
        return redirect("/study")
    colour = all_modules(conn).get(q["module"], COLOURS[0])
    conn.close()
    return render_template("study_quiz.html", quiz=q, data=json.loads(q["data"]), colour=colour)


@bp.route("/study/quiz/<int:quiz_id>/finish", methods=["POST"])
def finish_quiz(quiz_id):
    score = int(request.get_json()["score"])
    conn = get_db()
    conn.execute("UPDATE study_quizzes SET best = MAX(COALESCE(best, 0), ?) WHERE id = ?", (score, quiz_id))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@bp.route("/study/quiz/<int:quiz_id>/delete", methods=["POST"])
def delete_quiz(quiz_id):
    conn = get_db()
    row = conn.execute("SELECT module FROM study_quizzes WHERE id = ?", (quiz_id,)).fetchone()
    conn.execute("DELETE FROM study_quizzes WHERE id = ?", (quiz_id,))
    conn.commit()
    conn.close()
    return redirect("/study/module?m=" + quote(row["module"]) if row else "/study")


# --- Mind map ---

@bp.route("/study/map/<int:map_id>")
def map_page(map_id):
    conn = get_db()
    m = conn.execute("SELECT * FROM study_maps WHERE id = ?", (map_id,)).fetchone()
    if not m:
        conn.close()
        return redirect("/study")
    colour = all_modules(conn).get(m["module"], COLOURS[0])
    conn.close()
    return render_template("study_map.html", m=m, data=json.loads(m["data"]), colour=colour,
                           colours=COLOURS)


@bp.route("/study/map/<int:map_id>/delete", methods=["POST"])
def delete_map(map_id):
    conn = get_db()
    row = conn.execute("SELECT module FROM study_maps WHERE id = ?", (map_id,)).fetchone()
    conn.execute("DELETE FROM study_maps WHERE id = ?", (map_id,))
    conn.commit()
    conn.close()
    return redirect("/study/module?m=" + quote(row["module"]) if row else "/study")


setup_study()
