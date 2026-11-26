# Study tab: Claude for each of your modules.
# Attach lecture/seminar slides -> Claude writes notes built around your assignments,
# with a gap check. Attach a brief or marking criteria -> Claude saves what a 1st needs.
# Or just ask a question. Everything is saved per module.

from flask import Blueprint, render_template, request, redirect
from markupsafe import Markup, escape
from urllib.parse import quote
from datetime import date, datetime, timedelta
import sqlite3
import base64
import json
import re
import io

import config   # your API key lives in config.py (never on GitHub)

bp = Blueprint("study", __name__)
DB = "dashboard.db"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Same colours as the Uni page, so each module keeps its colour
COLOURS = ["#8b5cf6", "#ec4899", "#22d3ee", "#34d399", "#fb923c", "#facc15", "#60a5fa", "#f87171"]


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


# Creates the study tables the first time the app runs
def setup_study():
    conn = get_db()
    # What each assignment wants (made by Claude from the brief + marking criteria)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            title TEXT NOT NULL,
            data TEXT NOT NULL,
            files TEXT,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # One row per lecture or seminar Claude made notes for
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            title TEXT NOT NULL,
            kind TEXT,
            data TEXT NOT NULL,
            files TEXT,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Knowledge gaps found by the gap check. done = 1 once you've sorted it
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            note_id INTEGER,
            gap TEXT NOT NULL,
            why TEXT,
            fix TEXT,
            done INTEGER DEFAULT 0
        )
    """)
    # The chat for each module
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            files TEXT,
            note_id INTEGER,
            brief_id INTEGER,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


# ---------- Small helpers ----------

def all_modules(conn):
    """Every module from your timetable, deadlines and study notes, each with its colour."""
    names = set()
    for table in ("classes", "deadlines"):
        try:
            names |= {r[0] for r in conn.execute(f"SELECT DISTINCT module FROM {table}") if r[0]}
        except sqlite3.OperationalError:
            pass
    # Same order as the Uni page so colours match
    uni = sorted(names)
    colour_of = {m: COLOURS[i % len(COLOURS)] for i, m in enumerate(uni)}
    for table in ("study_notes", "study_briefs"):
        for r in conn.execute(f"SELECT DISTINCT module FROM {table}"):
            if r[0] not in colour_of:
                colour_of[r[0]] = COLOURS[len(colour_of) % len(COLOURS)]
    return colour_of


def next_class(conn, module, after=None):
    """The next time this module is on your timetable: (datetime, kind) or (None, None)."""
    now = after or datetime.now()
    best = (None, None)
    for c in conn.execute("SELECT * FROM classes WHERE module = ?", (module,)):
        hour, minute = (int(x) for x in c["start"].split(":")[:2])
        days_ahead = (c["day"] - now.weekday()) % 7
        when = datetime.combine(now.date() + timedelta(days=days_ahead), datetime.min.time()).replace(hour=hour, minute=minute)
        if when <= now:
            when += timedelta(days=7)
        if best[0] is None or when < best[0]:
            best = (when, c["kind"] or "Class")
    return best


def nice_when(when):
    if not when:
        return ""
    days = (when.date() - date.today()).days
    day = "Today" if days == 0 else "Tomorrow" if days == 1 else when.strftime("%a %d %b")
    return f"{day} {when.strftime('%H:%M')}"


def module_deadlines(conn, module):
    rows = conn.execute("SELECT * FROM deadlines WHERE module = ? AND done = 0 ORDER BY due",
                        (module,)).fetchall()
    out = []
    for d in rows:
        days = (date.fromisoformat(d["due"]) - date.today()).days
        out.append({"title": d["title"], "kind": d["kind"], "days": days,
                    "nice": date.fromisoformat(d["due"]).strftime("%a %d %b")})
    return out


# Turns Claude's light markdown (**bold**, lists, headings) into safe HTML
def inline_md(text):
    text = str(escape(text))
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
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


def file_to_blocks(f):
    """Turns one uploaded file into something Claude can read."""
    name = f.filename or "file"
    lower = name.lower()
    raw = f.read()
    ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
    header = {"type": "text", "text": f"File: {name}"}
    if ext == ".pdf":
        return [header, {"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.standard_b64encode(raw).decode()}}]
    if ext in IMAGE_TYPES:
        return [header, {"type": "image", "source": {
            "type": "base64", "media_type": IMAGE_TYPES[ext],
            "data": base64.standard_b64encode(raw).decode()}}]
    if ext == ".pptx":
        try:
            from pptx import Presentation
        except ImportError:
            raise ValueError("To read PowerPoint files run: pip install python-pptx "
                             "(or save the slides as PDF).")
        slides = []
        for i, slide in enumerate(Presentation(io.BytesIO(raw)).slides, 1):
            bits = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                bits.append("Speaker notes: " + slide.notes_slide.notes_text_frame.text)
            slides.append(f"--- Slide {i} ---\n" + "\n".join(bits))
        return [{"type": "text", "text": f"File: {name}\n" + "\n".join(slides)}]
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
        return [{"type": "text", "text": f"File: {name}\n" + "\n".join(parts)}]
    if ext in (".txt", ".md", ".csv"):
        return [{"type": "text", "text": f"File: {name}\n" + raw.decode("utf-8", "ignore")}]
    raise ValueError(f"I can't read {name}. Use PDF, PowerPoint, Word, a photo or text.")


# ---------- Claude ----------

def _client():
    import anthropic   # only loaded when needed
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def system_prompt(module):
    return f"""You are Claude, working inside Anil's study dashboard on the module "{module}".
Anil is a final-year university student aiming for a 1st (70%+) in every assignment and exam.
They give you lecture slides, seminar sheets, assignment briefs and marking criteria.

How to answer:
- Be direct and specific, like a sharp classmate who has read everything. No motivational filler,
  no generic study tips, no "you might want to consider exploring". Answer exactly what is asked.
- Base everything on Anil's own material and use the module's own notation. If something isn't in
  the material, say so and give the standard textbook answer, labelled as such.
- Always connect to what the assignments and marking criteria reward, so Anil can complete them
  with full confidence. Point out gaps plainly.
- Write maths in plain text (β0, R², Var(u|x), E[y]) so it reads on a phone.
- UK English. Short paragraphs and bullet points. Use **bold** for key terms."""


def module_context(conn, module):
    """Everything Claude should know about this module, as plain text."""
    lines = [f"Today is {date.today().strftime('%A %d %B %Y')}."]
    classes = conn.execute("SELECT * FROM classes WHERE module = ? ORDER BY day, start", (module,)).fetchall()
    if classes:
        lines.append("Timetable: " + "; ".join(
            f"{DAYS[c['day']]} {c['start']}-{c['end']} {c['kind'] or ''}".strip() for c in classes))
    for d in module_deadlines(conn, module):
        lines.append(f"Deadline: {d['title']} ({d['kind']}) due {d['nice']}, in {d['days']} days.")
    briefs = conn.execute("SELECT * FROM study_briefs WHERE module = ? ORDER BY id", (module,)).fetchall()
    for b in briefs:
        lines.append(f"\nASSIGNMENT: {b['title']}\n{json.dumps(json.loads(b['data']), ensure_ascii=False)}")
    notes = conn.execute("SELECT * FROM study_notes WHERE module = ? ORDER BY id", (module,)).fetchall()
    if notes:
        lines.append("\nNOTES MADE SO FAR (oldest first):")
        for n in notes:
            data = json.loads(n["data"])
            topics = "; ".join(s["heading"] for s in data.get("sections", []))
            lines.append(f"- {n['title']}: {data.get('summary', '')} Topics: {topics}")
    gaps = conn.execute("SELECT * FROM study_gaps WHERE module = ? AND done = 0", (module,)).fetchall()
    if gaps:
        lines.append("\nOPEN GAPS: " + "; ".join(g["gap"] for g in gaps))
    return "\n".join(lines)


def ask_claude(module, context, content, schema=None, history=None, effort="medium"):
    messages = list(history or [])
    messages.append({"role": "user", "content": content})
    # The module context goes first so Claude always knows the briefs and notes
    messages[0] = {"role": "user", "content": (
        [{"type": "text", "text": "ABOUT THIS MODULE\n" + context}] +
        (messages[0]["content"] if isinstance(messages[0]["content"], list)
         else [{"type": "text", "text": messages[0]["content"]}]))}
    output_config = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    response = _client().beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        system=system_prompt(module),
        messages=messages,
        output_config=output_config,
        # If Claude ever declines, the API quietly retries on a backup model
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this one. Try again.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("The answer was too long and got cut off. Try fewer files at once.")
    text = next(block.text for block in response.content if block.type == "text")
    return json.loads(text) if schema else text


def _obj(props, required=None):
    return {"type": "object", "properties": props,
            "required": required or list(props), "additionalProperties": False}


STR = {"type": "string"}
STRS = {"type": "array", "items": STR}

NOTES_SCHEMA = _obj({
    "title": {"type": "string", "description": "e.g. 'Lecture 7: Heteroskedasticity'"},
    "kind": {"type": "string", "enum": ["Lecture", "Seminar", "Workshop", "Other"]},
    "summary": {"type": "string", "description": "1-2 sentences: what this session covers"},
    "sections": {"type": "array", "items": _obj({"heading": STR, "points": STRS})},
    "key_terms": {"type": "array", "items": _obj({"term": STR, "meaning": STR})},
    "formulas": {"type": "array", "items": _obj({"name": STR, "formula": STR, "meaning": STR})},
    "assignment_links": {"type": "array", "items": _obj({"assignment": STR, "how": STR})},
    "gaps": {"type": "array", "items": _obj({"gap": STR, "why": STR, "fix": STR})},
    "reply": {"type": "string", "description": "2-3 sentence chat message: what you made and the most important gap"},
})

NOTES_ASK = """Make my notes for the attached {kind} material.

- summary: what this session covers.
- sections: the content in order, as tight bullet points. Every definition, model, assumption,
  result and worked example that matters. Keep the slides' notation. No waffle.
- key_terms and formulas: the ones I must know (formula in plain text, then what each part means).
- assignment_links: for each assignment above, exactly which parts of this session feed into which
  requirement or marking criterion. Leave empty only if none apply.
- gaps: GAP CHECK. Compare what the assignments and criteria need against what this session and my
  earlier notes cover. List concrete things I still need (a method not taught yet, a skill the
  criteria reward, a concept the slides only skim, a reading the brief expects). For each: why it
  matters for the mark, and the fastest way to close it. Don't repeat open gaps already listed.
{extra}"""

BRIEF_SCHEMA = _obj({
    "existing_title": {"type": "string", "description": "Exact title of an assignment already listed if these files belong to it, else empty"},
    "title": STR,
    "kind": {"type": "string", "enum": ["Assignment", "Exam", "Coursework", "Presentation", "Other"]},
    "due": {"type": "string", "description": "YYYY-MM-DD, or empty if not stated"},
    "weighting": {"type": "string", "description": "e.g. '50% of module', or empty"},
    "length": {"type": "string", "description": "word count / time limit, or empty"},
    "task": {"type": "string", "description": "2-3 sentences: exactly what must be produced"},
    "requirements": {"type": "array", "items": STR, "description": "every must-do from the brief"},
    "first_class": {"type": "array", "items": STR, "description": "what a 1st (70%+) looks like, concrete, from the criteria"},
    "topics_needed": {"type": "array", "items": STR, "description": "knowledge and skills needed to do it"},
    "reply": {"type": "string", "description": "2-3 sentence chat message summarising the assignment and its biggest demand"},
})

BRIEF_ASK = """These files are an assignment brief and/or marking criteria. Pull out exactly what
the assignment wants and what earns a 1st. If they belong to an assignment already listed above,
put its exact title in existing_title and return the full combined picture (old + new).
Be concrete: quote numbers, word counts, required sections and criteria wording.
{extra}"""


# ---------- Pages ----------

@bp.route("/study")
def study_home():
    conn = get_db()
    colour_of = all_modules(conn)
    modules = []
    for name, colour in colour_of.items():
        when, kind = next_class(conn, name)
        deadlines = module_deadlines(conn, name)
        modules.append({
            "name": name, "colour": colour,
            "next": f"{kind} {nice_when(when)}" if when else "",
            "notes": conn.execute("SELECT COUNT(*) FROM study_notes WHERE module = ?", (name,)).fetchone()[0],
            "briefs": conn.execute("SELECT COUNT(*) FROM study_briefs WHERE module = ?", (name,)).fetchone()[0],
            "gaps": conn.execute("SELECT COUNT(*) FROM study_gaps WHERE module = ? AND done = 0", (name,)).fetchone()[0],
            "deadline": deadlines[0] if deadlines else None,
        })
    conn.close()
    return render_template("study.html", modules=modules)


@bp.route("/study/module")
def module_page():
    name = request.args.get("m", "").strip()
    if not name:
        return redirect("/study")
    conn = get_db()
    colour = all_modules(conn).get(name, COLOURS[0])
    when, kind = next_class(conn, name)
    chat = conn.execute("SELECT * FROM study_chat WHERE module = ? ORDER BY id", (name,)).fetchall()
    notes = conn.execute("SELECT * FROM study_notes WHERE module = ? ORDER BY id DESC", (name,)).fetchall()
    briefs = [dict(b, data=json.loads(b["data"])) for b in
              conn.execute("SELECT * FROM study_briefs WHERE module = ? ORDER BY id", (name,))]
    gaps = conn.execute("SELECT * FROM study_gaps WHERE module = ? AND done = 0 ORDER BY id", (name,)).fetchall()
    deadlines = module_deadlines(conn, name)
    conn.close()
    return render_template("study_module.html", name=name, colour=colour,
                           next=f"{kind} {nice_when(when)}" if when else "",
                           chat=chat, notes=notes, briefs=briefs, gaps=gaps, deadlines=deadlines,
                           error=request.args.get("error"))


@bp.route("/study/note/<int:note_id>")
def note_page(note_id):
    conn = get_db()
    note = conn.execute("SELECT * FROM study_notes WHERE id = ?", (note_id,)).fetchone()
    if not note:
        conn.close()
        return redirect("/study")
    gaps = conn.execute("SELECT * FROM study_gaps WHERE note_id = ? ORDER BY id", (note_id,)).fetchall()
    colour = all_modules(conn).get(note["module"], COLOURS[0])
    conn.close()
    return render_template("study_note.html", note=note, data=json.loads(note["data"]),
                           gaps=gaps, colour=colour)


# The chat box: a question, slides to turn into notes, or a brief
@bp.route("/study/send", methods=["POST"])
def send():
    module = request.form["module"].strip()
    message = request.form.get("message", "").strip()
    kind = request.form.get("kind", "question")
    files = [f for f in request.files.getlist("files") if f and f.filename]
    back = "/study/module?m=" + quote(module)
    if not message and not files:
        return redirect(back)
    if kind in ("lecture", "seminar", "brief") and not files:
        return redirect(back + "&error=" + quote("Attach the file first."))

    conn = get_db()
    try:
        blocks = []
        for f in files:
            blocks += file_to_blocks(f)
        context = module_context(conn, module)
        extra = f"\nMy message: {message}" if message else ""
        note_id = brief_id = None

        if kind in ("lecture", "seminar"):
            label = "lecture" if kind == "lecture" else "seminar"
            data = ask_claude(module, context, blocks + [{"type": "text", "text":
                              NOTES_ASK.format(kind=label, extra=extra)}], NOTES_SCHEMA)
            reply = data.pop("reply")
            cur = conn.execute("INSERT INTO study_notes (module, title, kind, data, files) VALUES (?, ?, ?, ?, ?)",
                               (module, data["title"], data["kind"], json.dumps(data, ensure_ascii=False),
                                ", ".join(f.filename for f in files)))
            note_id = cur.lastrowid
            for g in data.get("gaps", []):
                conn.execute("INSERT INTO study_gaps (module, note_id, gap, why, fix) VALUES (?, ?, ?, ?, ?)",
                             (module, note_id, g["gap"], g["why"], g["fix"]))

        elif kind == "brief":
            data = ask_claude(module, context, blocks + [{"type": "text", "text":
                              BRIEF_ASK.format(extra=extra)}], BRIEF_SCHEMA)
            reply = data.pop("reply")
            existing_title = data.pop("existing_title", "")
            old = conn.execute("SELECT id FROM study_briefs WHERE module = ? AND title = ?",
                               (module, existing_title)).fetchone() if existing_title else None
            if old:
                brief_id = old["id"]
                conn.execute("UPDATE study_briefs SET title = ?, data = ? WHERE id = ?",
                             (data["title"], json.dumps(data, ensure_ascii=False), brief_id))
            else:
                cur = conn.execute("INSERT INTO study_briefs (module, title, data, files) VALUES (?, ?, ?, ?)",
                                   (module, data["title"], json.dumps(data, ensure_ascii=False),
                                    ", ".join(f.filename for f in files)))
                brief_id = cur.lastrowid

        else:   # a normal question, maybe with a file to look at
            past = conn.execute("SELECT role, content FROM study_chat WHERE module = ? "
                                "ORDER BY id DESC LIMIT 16", (module,)).fetchall()[::-1]
            history = []
            for p in past:   # the API needs user/assistant turns to alternate
                if history and history[-1]["role"] == p["role"]:
                    history[-1]["content"] += "\n\n" + p["content"]
                else:
                    history.append({"role": p["role"], "content": p["content"]})
            if history and history[0]["role"] == "assistant":
                history.insert(0, {"role": "user", "content": "(earlier chat)"})
            if history and history[-1]["role"] == "user":
                history.append({"role": "assistant", "content": "(no reply)"})
            reply = ask_claude(module, context, blocks + [{"type": "text", "text": message or "Look at this."}],
                               history=history)
    except Exception as e:   # show the problem on the page instead of crashing
        conn.close()
        return redirect(back + "&error=" + quote(f"{type(e).__name__}: {e}"[:300]))

    label = {"lecture": "📑 Lecture slides", "seminar": "🗂️ Seminar", "brief": "📋 Brief / criteria"}.get(kind)
    shown = message or (f"{label}: make my notes" if kind in ("lecture", "seminar") else
                        f"{label}: what does this want?" if kind == "brief" else "Look at this.")
    conn.execute("INSERT INTO study_chat (module, role, content, files) VALUES (?, 'user', ?, ?)",
                 (module, shown, ", ".join(f.filename for f in files)))
    conn.execute("INSERT INTO study_chat (module, role, content, note_id, brief_id) VALUES (?, 'assistant', ?, ?, ?)",
                 (module, reply, note_id, brief_id))
    conn.commit()
    conn.close()
    return redirect(back + "#bottom")


@bp.route("/study/gap/<int:gap_id>", methods=["POST"])
def toggle_gap(gap_id):
    conn = get_db()
    conn.execute("UPDATE study_gaps SET done = 1 - done WHERE id = ?", (gap_id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or "/study")


@bp.route("/study/note/<int:note_id>/delete", methods=["POST"])
def delete_note(note_id):
    conn = get_db()
    row = conn.execute("SELECT module FROM study_notes WHERE id = ?", (note_id,)).fetchone()
    conn.execute("DELETE FROM study_notes WHERE id = ?", (note_id,))
    conn.execute("DELETE FROM study_gaps WHERE note_id = ?", (note_id,))
    conn.commit()
    conn.close()
    return redirect("/study/module?m=" + quote(row["module"]) if row else "/study")


@bp.route("/study/brief/<int:brief_id>/delete", methods=["POST"])
def delete_brief(brief_id):
    conn = get_db()
    row = conn.execute("SELECT module FROM study_briefs WHERE id = ?", (brief_id,)).fetchone()
    conn.execute("DELETE FROM study_briefs WHERE id = ?", (brief_id,))
    conn.commit()
    conn.close()
    return redirect("/study/module?m=" + quote(row["module"]) if row else "/study")


@bp.route("/study/clear-chat", methods=["POST"])
def clear_chat():
    module = request.form["module"]
    conn = get_db()
    conn.execute("DELETE FROM study_chat WHERE module = ?", (module,))
    conn.commit()
    conn.close()
    return redirect("/study/module?m=" + quote(module))


setup_study()
