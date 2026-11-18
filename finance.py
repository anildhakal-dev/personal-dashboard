# Money helpers: reads your Nationwide CSV, sorts each transaction into a
# category, and asks Claude for a brutally honest monthly review.
# Kept in its own file so app.py stays tidy.

import csv
import io
import json
import re
from datetime import datetime

import config

# ---------- Categories ----------

SPEND_CATEGORIES = ["Groceries", "Eating out", "Transport & fuel", "Rent & bills", "Subscriptions",
                    "Gym & boxing", "Trading (prop fees)", "Shopping", "Fun & going out",
                    "Personal care", "Giving", "Other"]
INCOME_CATEGORIES = ["Wages", "Student loan", "Trading payouts", "Other income"]
# Money moved, not spent: savings and transfers between your own accounts
MOVE_CATEGORIES = ["Savings", "Transfer"]
ALL_CATEGORIES = SPEND_CATEGORIES + INCOME_CATEGORIES + MOVE_CATEGORIES

CATEGORY_COLOURS = {
    "Groceries": "#34d399", "Eating out": "#fb923c", "Transport & fuel": "#60a5fa",
    "Rent & bills": "#f87171", "Subscriptions": "#ec4899", "Gym & boxing": "#22d3ee",
    "Trading (prop fees)": "#facc15", "Shopping": "#a78bfa", "Fun & going out": "#f472b6",
    "Personal care": "#2dd4bf", "Giving": "#c4b5fd", "Other": "#8b90b0",
}

# Shops the dashboard already knows. Checked top to bottom, first match wins
# (so "UBER EATS" is caught as Eating out before "UBER" becomes Transport)
RULES = [
    ("Eating out", ["UBER EATS", "UBEREATS", "DELIVEROO", "JUST EAT", "MCDONALD", "KFC", "NANDO",
                    "GREGGS", "STARBUCKS", "COSTA", "DOMINO", "SUBWAY", "PRET ", "WAGAMAMA",
                    "FIVE GUYS", "TIM HORTONS", "BURGER KING", "PAPA JOHN", "CAFFE NERO"]),
    ("Groceries", ["TESCO", "SAINSBURY", "ASDA", "ALDI", "LIDL", "MORRISON", "CO-OP", "COOP",
                   "WAITROSE", "ICELAND", "M&S FOOD", "MYPROTEIN"]),
    ("Subscriptions", ["NETFLIX", "DISNEY", "SPOTIFY", "PRIME VIDEO", "AMAZON PRIME", "AMZNPRIME",
                       "PRIME MEMBER", "YOUTUBE", "STRAVA", "APPLE.COM", "ICLOUD", "CHATGPT",
                       "OPENAI", "ANTHROPIC", "CLAUDE.AI", "AUDIBLE", "NOW TV", "DAZN"]),
    ("Gym & boxing", ["FOUNDRY", "ULTRAFLEX", "GYM", "BOXING", "PUREGYM", "THE GYM GROUP"]),
    ("Trading (prop fees)", ["TOPSTEP", "APEX TRADER", "APEXTRADER", "FTMO", "TRADEIFY",
                             "MYFUNDEDFUTURES", "TAKE PROFIT", "TAKEPROFIT", "LUCID TRADING",
                             "TRADINGVIEW", "TRADOVATE", "RITHMIC", "NINJATRADER", "FUNDEDNEXT",
                             "ALPHA FUTURES", "BULENOX", "EARN2TRADE"]),
    ("Transport & fuel", ["SHELL", "ESSO", "TEXACO", " BP ", " JET ", "PETROL", "FUEL",
                          "PARKING", "NCP", "RINGGO", "PAYBYPHONE", "JUSTPARK", "TRAINLINE",
                          "UBER", "BOLT", "DVLA", "HALFORDS", "KWIK FIT"]),
    ("Shopping", ["AMAZON", "AMZN", "EBAY", "ASOS", "JD SPORTS", "PRIMARK", "ARGOS", "NIKE",
                  "ZARA", "H&M", "BOOTS", "SUPERDRUG", "CURRYS"]),
    ("Rent & bills", [" RENT", "COUNCIL TAX", "VODAFONE", "GIFFGAFF", "OCTOPUS", "BRITISH GAS",
                      "EDF", "TV LICENCE"]),
]
INCOME_RULES = [
    ("Student loan", ["STUDENT LOAN", "SLC ", "STUDENT LOANS CO"]),
    ("Trading payouts", ["TOPSTEP", "APEX", "RISE", "DEEL", "PAYOUT", "TRADEIFY", "FTMO",
                         "MYFUNDEDFUTURES", "TAKEPROFIT", "LUCID"]),
    ("Wages", ["SALARY", "WAGES", "GURKHA"]),
]


def merchant_key(description):
    """'TESCO STORES 3216 LINCOLN GB' -> 'TESCO STORES LINCOLN' (so every Tesco matches)"""
    text = re.sub(r"[^A-Z& ]", " ", description.upper())
    words = text.split()
    return " ".join(words[:3]) or description.upper()[:30]


def rule_category(description, amount):
    """Category from the built-in rules, or None if it's a shop we don't know"""
    text = " " + description.upper() + " "
    rules = INCOME_RULES if amount > 0 else RULES
    for category, words in rules:
        if any(w in text for w in words):
            return category
    return None


# ---------- Reading the CSV ----------

def to_money(text):
    """'£1,234.56' -> 1234.56, '' -> 0"""
    text = (text or "").replace("£", "").replace(",", "").replace("\xa3", "").strip()
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def to_date(text):
    for fmt in ("%d %b %Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%y", "%d %B %Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def read_statement(raw_bytes):
    """Turns a bank CSV into a list of {day, description, amount, balance}.
    Spending is negative, money in is positive. Works with Nationwide's layout
    (a few account lines at the top, then Date / Description / Paid out / Paid in / Balance)."""
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")   # older Nationwide files use this for the £ sign
    rows = list(csv.reader(io.StringIO(text)))

    # Find the row with the column names (skips Nationwide's account info lines)
    header_at = None
    for i, row in enumerate(rows):
        names = [c.strip().lower() for c in row]
        if "date" in names and any(n in names for n in ("paid out", "amount", "debit", "money out")):
            header_at = i
            break
    if header_at is None:
        raise ValueError("Couldn't find the column names in this file. Is it the CSV export?")

    names = [c.strip().lower() for c in rows[header_at]]

    def col(*options):
        for o in options:
            if o in names:
                return names.index(o)
        return None

    c_date = col("date")
    c_desc = col("description", "transactions", "details", "transaction description", "merchant")
    c_where = col("location")
    c_type = col("transaction type", "type")
    c_out = col("paid out", "debit", "money out")
    c_in = col("paid in", "credit", "money in")
    c_amount = col("amount")
    c_balance = col("balance")

    found = []
    for row in rows[header_at + 1:]:
        if len(row) <= c_date or not row[c_date].strip():
            continue
        day = to_date(row[c_date])
        if not day:
            continue
        parts = [row[c].strip() for c in (c_desc, c_where) if c is not None and c < len(row) and row[c].strip()]
        description = " ".join(parts) or (row[c_type].strip() if c_type is not None else "Unknown")
        if c_amount is not None:
            amount = to_money(row[c_amount])
        else:
            paid_out = to_money(row[c_out]) if c_out is not None and c_out < len(row) else 0
            paid_in = to_money(row[c_in]) if c_in is not None and c_in < len(row) else 0
            amount = paid_in - paid_out
        balance = to_money(row[c_balance]) if c_balance is not None and c_balance < len(row) else None
        if amount != 0:
            found.append({"day": day, "description": description, "amount": round(amount, 2),
                          "balance": balance})
    return found


# ---------- Claude ----------

def _client():
    import anthropic   # only loaded when needed
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _ask_claude(system, content, schema, effort):
    # content can be plain text, or a list of blocks (used for PDFs)
    response = _client().beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        # If Claude ever declines, the API quietly retries on a backup model
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this one. Try again.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("The answer was too long and got cut off. Try again.")
    text = next(block.text for block in response.content if block.type == "text")
    return json.loads(text)


def ai_categorise(merchants):
    """merchants: list of (merchant, 'in' or 'out', example description).
    Returns {merchant: category} for shops the rules didn't know."""
    if not merchants:
        return {}
    lines = [f"- {m} | money {direction} | e.g. \"{example}\"" for m, direction, example in merchants]
    schema = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object",
            "properties": {"merchant": {"type": "string"},
                           "category": {"type": "string", "enum": ALL_CATEGORIES}},
            "required": ["merchant", "category"], "additionalProperties": False}}},
        "required": ["items"], "additionalProperties": False,
    }
    system = ("You sort UK bank transactions into budget categories for a student in Lincoln who "
              "works at a family restaurant (Gurkha Chefs), trades futures with prop firms, goes to "
              "the gym and boxes. Money-out lines must get a spending category, Savings or Transfer. "
              "Money-in lines must get an income category or Transfer. Payments to or from the "
              "person's own accounts or savings pots are Transfer or Savings. If unsure, use Other "
              "(or Other income). Return every merchant exactly as written.")
    result = _ask_claude(system, "Categorise these:\n" + "\n".join(lines), schema, "low")
    return {item["merchant"]: item["category"] for item in result["items"]}


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "description": "A, B, C, D or F for this month"},
        "headline": {"type": "string", "description": "One punchy line summing up the month"},
        "verdict": {"type": "string", "description": "3-5 sentences, brutally honest with a hint of encouragement"},
        "budget": {"type": "array", "items": {
            "type": "object",
            "properties": {"category": {"type": "string"},
                           "spent": {"type": "number"},
                           "limit": {"type": "number", "description": "Suggested monthly limit in pounds"},
                           "note": {"type": "string"}},
            "required": ["category", "spent", "limit", "note"], "additionalProperties": False}},
        "cut_backs": {"type": "array", "items": {
            "type": "object",
            "properties": {"what": {"type": "string"},
                           "save_per_month": {"type": "number"},
                           "how": {"type": "string"}},
            "required": ["what", "save_per_month", "how"], "additionalProperties": False}},
        "save_plan": {"type": "array", "items": {
            "type": "object",
            "properties": {"goal": {"type": "string"},
                           "per_month": {"type": "number"},
                           "note": {"type": "string"}},
            "required": ["goal", "per_month", "note"], "additionalProperties": False}},
        "wins": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "focus": {"type": "string", "description": "The ONE thing to do differently next month"},
    },
    "required": ["grade", "headline", "verdict", "budget", "cut_backs", "save_plan", "wins",
                 "warnings", "focus"],
    "additionalProperties": False,
}

REVIEW_SYSTEM = """You are the money coach inside Anil's personal dashboard. Anil is a final-year
UK university student who works at his family's restaurant, trades futures through prop firms,
trains at the gym and boxes. He wants you brutally honest with a hint of encouragement:
say plainly what is going wrong and what it costs him, then show the way forward.

Rules:
- Use only the numbers you are given. Amounts are in pounds. Do the maths carefully.
- Budget from GUARANTEED income (wages, student loan spread over the months it covers).
  Treat trading payouts as irregular: good months go to goals, never to fixed spending.
- Prop firm fees are a business cost. Judge them against payouts (net trading result),
  not as normal spending, but call it out if fees are eating the payouts.
- Never tell him to cut anything listed as untouchable.
- Be specific: name the shop or subscription and the pounds saved per month.
- Check goals against reality. If two goals can't both happen on his income by their dates,
  say so and suggest an order or a change (later date, smaller amount). Car finance and big
  monthly car costs lower how much a mortgage lender will offer: mention it if relevant.
- UK specifics you may use: an emergency fund of 3 months' essential costs comes first;
  a Lifetime ISA (age 18-39) adds a 25% government bonus on up to £4,000 a year for a
  first home up to £450,000 and must be open 12 months before buying; a Cash or Stocks &
  Shares ISA keeps savings tax-free.
- This is general guidance, not regulated financial advice. Don't keep repeating that.
- The budget list covers his spending categories. save_plan covers each goal (and an
  emergency fund if he has none). Keep cut_backs to the 3-6 that matter most."""


def ai_review(context):
    return _ask_claude(REVIEW_SYSTEM, context, REVIEW_SCHEMA, "medium")


# ---------- Reading a PDF statement ----------

PDF_SCHEMA = {
    "type": "object",
    "properties": {"transactions": {"type": "array", "items": {
        "type": "object",
        "properties": {"day": {"type": "string", "description": "YYYY-MM-DD"},
                       "description": {"type": "string"},
                       "amount": {"type": "number", "description": "Negative for money out, positive for money in"},
                       "balance": {"anyOf": [{"type": "number"}, {"type": "null"}]}},
        "required": ["day", "description", "amount", "balance"], "additionalProperties": False}}},
    "required": ["transactions"], "additionalProperties": False,
}


def read_pdf_statement(raw_bytes):
    """Nationwide only gives PDFs on some screens, so Claude reads the PDF and
    hands back the same list read_statement() makes from a CSV."""
    import base64
    content = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                        "data": base64.standard_b64encode(raw_bytes).decode()}},
        {"type": "text", "text": "List every transaction in this bank statement, in order. "
                                 "Copy the description exactly as printed (include the location if shown). "
                                 "Paid out is negative, paid in is positive. Skip balance brought/carried "
                                 "forward lines and totals. Use null if a line has no balance."},
    ]
    result = _ask_claude("You copy bank statements into data with perfect accuracy.",
                         content, PDF_SCHEMA, "low")
    found = []
    for t in result["transactions"]:
        day = to_date(t["day"])
        if day and t["amount"]:
            found.append({"day": day, "description": t["description"].strip(),
                          "amount": round(t["amount"], 2), "balance": t["balance"]})
    if not found:
        raise ValueError("Couldn't find any transactions in that PDF.")
    return found


# ---------- Talking it through with the coach ----------

CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "Your answer to Anil, like a text message"},
        "remember": {"type": "array", "items": {"type": "string"},
                     "description": "New facts that stay true in FUTURE months (not one-offs). Usually empty."},
        "regrade": {"type": "boolean",
                    "description": "True if what Anil said changes the grade, budget or plan enough to redo the review"},
    },
    "required": ["reply", "remember", "regrade"],
    "additionalProperties": False,
}

CHAT_SYSTEM = REVIEW_SYSTEM + """

You are now chatting with Anil about the review you wrote. He may add context the bank
data can't show (a one-off cost, money owed back to him, a cancelled subscription, cash
he saved elsewhere), disagree with you, or ask questions.

- Reply like a sharp friend who knows money: short, direct, specific, in pounds. 2-6 sentences
  unless he asks for detail. No headings.
- Push back when he's making excuses, but accept real context and change your view.
- remember: only facts that will still be true next month (e.g. "Ultraflex cancelled from
  Dec 2026", "£200 a month to mum is a fixed commitment"). Write each as a short note
  with a date if it matters. One-off events for this month go in the chat, not here.
  Never repeat something already in his notes or the remembered list.
- regrade: true when his context means the grade, budget, cut backs or save plan should change.
  If true, say in your reply that you're updating the review."""


def ai_chat(context):
    return _ask_claude(CHAT_SYSTEM, context, CHAT_SCHEMA, "medium")
