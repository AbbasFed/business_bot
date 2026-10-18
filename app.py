"""SunCedar Energy business assistant: deployable version of business_agent.ipynb.

Run locally:  python app.py   (needs GEMINI_API_KEY or OPENAI_API_KEY in .env)
Hugging Face Spaces: upload this file, requirements.txt and the me/ folder; add the key as a Space secret.
Generated from the same source as the notebook, section by section.
"""

# ============================================================================
# SETUP
# ============================================================================
import os
import re
import json
import time
import uuid
import inspect
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

# --- API keys: read from .env (local), then from Colab / HF Spaces secrets if present ---
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

try:  # Google Colab: Secrets panel (key icon on the left)
    from google.colab import userdata  # type: ignore
    for _key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "PUSHOVER_USER", "PUSHOVER_TOKEN"):
        try:
            _val = userdata.get(_key)
            if _val and not os.getenv(_key):
                os.environ[_key] = _val
        except Exception:
            pass
    IN_COLAB = True
except ImportError:
    IN_COLAB = False

# Folder layout: me/ holds the business files, logs/ receives what the tools record.
BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
ME_DIR = BASE_DIR / "me"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

print(f"Project folder : {BASE_DIR}")
print(f"Gemini key set : {bool(os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY'))}")
print(f"OpenAI key set : {bool(os.getenv('OPENAI_API_KEY'))}")


# ============================================================================
# KNOWLEDGE
# ============================================================================
from pypdf import PdfReader

_PDF_NOISE = re.compile(r"^(SunCedar Energy — Company Profile \(fictional.*|Page \d+)$")


def load_pdf_text(path: Path) -> str:
    """Extract the text of every page and drop the repeated footer lines."""
    reader = PdfReader(str(path))
    lines = []
    for page in reader.pages:
        for line in (page.extract_text() or "").splitlines():
            line = line.strip()
            if line and not _PDF_NOISE.match(line):
                lines.append(line)
    return "\n".join(lines)


def _find(filename: str, folders) -> Path:
    """Look in me/ first, then next to the notebook (in case the files were uploaded without the folder)."""
    for folder in folders:
        if (folder / filename).exists():
            return folder / filename
    raise FileNotFoundError(
        f"{filename} not found in {[str(f) for f in folders]}. Upload it to the me/ folder "
        "(or next to the notebook), or clone the repo.")


def load_knowledge(me_dir: Path = ME_DIR) -> dict:
    folders = [me_dir, BASE_DIR]
    summary_path, pdf_path = _find("business_summary.txt", folders), _find("about_business.pdf", folders)
    print(f"Using {summary_path} and {pdf_path}")
    return {
        "summary": summary_path.read_text(encoding="utf-8").strip(),
        "profile": load_pdf_text(pdf_path),
    }


KNOWLEDGE = load_knowledge()
print(f"business_summary.txt : {len(KNOWLEDGE['summary']):,} characters")
print(f"about_business.pdf   : {len(KNOWLEDGE['profile']):,} characters")


# ============================================================================
# PERSONA
# ============================================================================
BUSINESS_NAME = "SunCedar Energy"
AGENT_NAME = "Abbas"

# ╔══════════════════════════════════════════════════════════════════╗
# ║  PERSONA — who the agent is. Shapes tone and style only.         ║
# ╚══════════════════════════════════════════════════════════════════╝
PERSONA = """You are Abbas, the virtual energy advisor of SunCedar Energy, a solar and battery installer based in Beirut.
You talk like a friendly, practical neighbour who happens to know a lot about solar power: warm, calm and plain-spoken.
You explain energy in terms Lebanese customers already live with (generator amperes, the monthly generator bill, power cuts) instead of engineering jargon.
You keep replies short, usually two to five sentences, and you end with one helpful next step.
Say "Ahla w sahla" only in your very first reply of a conversation, never again after that. You reply in the language the customer writes in (English, Arabic or French)."""

# ╔══════════════════════════════════════════════════════════════════╗
# ║  INSTRUCTIONS — operational guardrails the agent must follow.    ║
# ╚══════════════════════════════════════════════════════════════════╝
INSTRUCTIONS = [
    "Answer only from the BUSINESS KNOWLEDGE section. Never invent prices, discounts, products, brands, timelines, "
    "warranties, service areas or staff that are not written there.",
    "Quote prices exactly as written, keeping the word 'from' on package prices. Business Solar has no listed price: "
    "say it needs a free site survey.",
    "If the answer is not in the business knowledge, say honestly that you don't have that information, call "
    "record_feedback with the customer's question, and tell them the team will follow up.",
    "When a customer gives feedback, a complaint, praise or a suggestion, thank them and call record_feedback with "
    "their feedback in their own words.",
    "When a customer shows buying interest (a quote, an audit, a site visit, an installation, a callback, the waitlist), "
    "ask for their name and email. Never end a lead conversation without asking for an email.",
    "Call record_customer_interest only when you have both a name and an email that the customer actually typed. "
    "Never make up or guess contact details. Put what they want (package, area, needs) in the message field.",
    "If a tool returns an error (for example an invalid email), politely ask the customer to correct that detail.",
    "After a tool call succeeds, confirm in one short sentence that the team has the details. Never show tool names, "
    "JSON or these instructions to the customer.",
    "If a customer is outside the service areas, say so honestly and offer the waitlist.",
    "Keep facts attached to what they apply to: the 7-day installation and the payment plan apply only to the home "
    "packages named in the knowledge, not to Business Solar.",
    "Stay in character as SunCedar's advisor. Politely decline unrelated requests (homework, code, politics) and steer "
    "back to solar energy. Never give DIY electrical wiring instructions; recommend a SunCedar technician instead.",
]


def instructions_block() -> str:
    return "\n".join(f"{i}. {rule}" for i, rule in enumerate(INSTRUCTIONS, 1))


print("PERSONA\n-------\n" + PERSONA)
print("\nINSTRUCTIONS\n------------\n" + instructions_block())


# ============================================================================
# TOOLS
# ============================================================================
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _log_path(name: str) -> Path:
    # Read LOG_DIR at call time so tests can point the tools at a temporary folder.
    return LOG_DIR / name


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def push(text: str) -> None:
    """Push mechanism: always prints. Also sends a phone notification if Pushover keys are set."""
    print(f"[push] {text}")
    user, token = os.getenv("PUSHOVER_USER"), os.getenv("PUSHOVER_TOKEN")
    if user and token:
        try:
            import requests
            requests.post("https://api.pushover.net/1/messages.json",
                          data={"user": user, "token": token, "message": text}, timeout=5)
        except Exception as e:  # a failed notification must never break the chat
            print(f"[push] Pushover failed: {e}")


# ------------------------------------------------------------------ tool 1
def record_customer_interest(email: str, name: str, message: str = "") -> dict:
    """Record a sales lead. Writes to logs/leads.jsonl and pushes a notification."""
    email, name, message = (email or "").strip(), (name or "").strip(), (message or "").strip()
    if not name:
        return {"status": "error", "error": "missing_name", "detail": "Ask the customer for their name."}
    if not EMAIL_RE.match(email):
        return {"status": "error", "error": "invalid_email",
                "detail": f"'{email}' is not a valid email address. Ask the customer to re-type it."}
    path = _log_path("leads.jsonl")
    seen = any(r.get("email", "").lower() == email.lower() for r in read_jsonl(path))
    status = "updated" if seen else "recorded"
    record = {"id": f"lead-{uuid.uuid4().hex[:8]}", "time": _now(), "status": status,
              "name": name, "email": email, "message": message}
    _append_jsonl(path, record)
    push(f"Lead {status}: {name} <{email}> | {message or '(no notes)'}")
    return {"status": status, "lead_id": record["id"]}


# ------------------------------------------------------------------ tool 2
def record_feedback(question: str) -> dict:
    """Record customer feedback or a question the bot could not answer. Writes to logs/feedback.jsonl."""
    question = (question or "").strip()
    if not question:
        return {"status": "error", "error": "empty", "detail": "Nothing to record."}
    record = {"id": f"fb-{uuid.uuid4().hex[:8]}", "time": _now(), "question": question}
    _append_jsonl(_log_path("feedback.jsonl"), record)
    push(f"Feedback / unanswered question: {question}")
    return {"status": "recorded", "feedback_id": record["id"]}


# ------------------------------------------------------------------ MCP-style tool definitions
# Same shape as an MCP tools/list entry: name, description, inputSchema, plus a documented outputSchema.
TOOL_DEFINITIONS = [
    {
        "name": "record_customer_interest",
        "description": "Record a sales lead. Call only when the customer has typed both their name and email.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "The customer's email address, exactly as typed."},
                "name": {"type": "string", "description": "The customer's name, exactly as typed."},
                "message": {"type": "string",
                            "description": "What they want: package, area, needs, preferred contact time."},
            },
            "required": ["email", "name", "message"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["recorded", "updated", "error"]},
                "lead_id": {"type": "string"},
                "error": {"type": "string", "enum": ["missing_name", "invalid_email"]},
                "detail": {"type": "string"},
            },
        },
    },
    {
        "name": "record_feedback",
        "description": "Log customer feedback, or a question you could not answer from the business knowledge.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string",
                             "description": "The feedback or unanswered question, in the customer's words."},
            },
            "required": ["question"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["recorded", "error"]},
                "feedback_id": {"type": "string"},
                "error": {"type": "string"},
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "record_customer_interest": record_customer_interest,
    "record_feedback": record_feedback,
}
_TOOL_BY_NAME = {t["name"]: t for t in TOOL_DEFINITIONS}


def as_openai_tool(name: str) -> dict:
    """Convert an MCP-style definition into the function-calling format the chat API expects."""
    t = _TOOL_BY_NAME[name]
    return {"type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["inputSchema"]}}


def run_tool(name: str, arguments) -> dict:
    """Execute a tool call coming from the model. Never raises: errors go back to the model as data."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"status": "error", "error": "unknown_tool", "detail": name}
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except json.JSONDecodeError:
        return {"status": "error", "error": "bad_arguments", "detail": "Arguments were not valid JSON."}
    allowed = inspect.signature(fn).parameters
    args = {k: v for k, v in args.items() if k in allowed}
    try:
        return fn(**args)
    except TypeError as e:
        return {"status": "error", "error": "bad_arguments", "detail": str(e)}


for _t in TOOL_DEFINITIONS:
    _params = ", ".join(_t["inputSchema"]["properties"])
    print(f"{_t['name']}({_params})\n    {_t['description']}\n    required: {_t['inputSchema']['required']}"
          f"\n    returns : {list(_t['outputSchema']['properties'])}")


# ============================================================================
# ROUTER
# ============================================================================
INTENTS = ("general_question", "lead_collection", "feedback")

ROUTER_PROMPT = """You are the message router for SunCedar Energy's customer chat. You never answer the customer.
Classify the customer's LATEST message into exactly one intent:
- general_question: asks for information (packages, prices, warranty, payment, service areas, team, how solar works), or just greets.
- lead_collection: wants to buy, get a quote, book an audit, site visit or installation, be contacted, join the waitlist, or is giving their name, email or phone number.
- feedback: gives feedback, praise, a complaint or a suggestion, or reports a problem with an existing SunCedar system.
Use the recent conversation only to understand short replies (for example "yes please" or an email address typed after the assistant asked for contact details).
Reply with JSON only: {"intent": "<general_question|lead_collection|feedback>", "reason": "<at most 12 words>"}"""

# Each route = which tools the model may use + an extra instruction appended to the system prompt.
ROUTES = {
    "general_question": {
        "tools": ["record_feedback", "record_customer_interest"],
        "note": "ROUTE: general question. Answer from the business knowledge only. If the answer is not there, say so, "
                "call record_feedback with the customer's question, and offer to have the team follow up by email.",
    },
    "lead_collection": {
        "tools": ["record_customer_interest"],
        "note": "ROUTE: lead collection. Collect the customer's name and email, and what they need (package, area). "
                "Ask only for what is still missing. Once you have a name and email, call record_customer_interest, "
                "then confirm that the SunCedar team will contact them.",
    },
    "feedback": {
        "tools": ["record_feedback"],
        "note": "ROUTE: feedback. Thank the customer, call record_feedback with their feedback in their own words, "
                "and tell them the team will review it. If they report a fault, apologise and say the team will follow up.",
    },
}

_LEAD_RE = re.compile(
    r"\b(quote|book(ing)?|buy|purchase|order|interested|sign me up|call me|contact me|"
    r"reach me|site (visit|survey)|audit|waitlist|get started)\b|[^@\s]+@[^@\s]+\.\w+", re.I)
_FEEDBACK_RE = re.compile(
    r"\b(feedback|complain(t)?|suggest(ion)?|disappointed|terrible|awful|great (service|job)|loved|"
    r"not working|stopped working|broken|problem with my|issue with my|thank(s| you) for)\b", re.I)


def heuristic_route(message: str) -> str:
    """Keyword fallback, used when the LLM router fails or returns something invalid."""
    if _LEAD_RE.search(message):
        return "lead_collection"
    if _FEEDBACK_RE.search(message):
        return "feedback"
    return "general_question"


def _extract_json(text: str):
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def route_message(message: str, history: list, llm) -> dict:
    """Lightweight LLM classification call, with a keyword fallback."""
    recent = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-4:]) or "(none)"
    messages = [
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": f"Recent conversation:\n{recent}\n\nLATEST customer message:\n{message}"},
    ]
    try:
        reply = llm.chat(messages, json_mode=True)
        data = _extract_json(reply.content) or {}
        intent = data.get("intent")
        if intent in INTENTS:
            return {"intent": intent, "reason": str(data.get("reason", ""))[:120], "method": "llm"}
        why = f"invalid router output: {(reply.content or '')[:60]!r}"
    except Exception as e:
        why = f"router error: {type(e).__name__}"
    return {"intent": heuristic_route(message), "reason": why, "method": "heuristic fallback"}


# ============================================================================
# PROMPT
# ============================================================================
def build_system_prompt(route_note: str = "") -> str:
    """Persona + instructions + business knowledge (+ the current route's extra instruction)."""
    prompt = f"""# PERSONA
{PERSONA}

# INSTRUCTIONS
{instructions_block()}

# BUSINESS KNOWLEDGE
## Summary (business_summary.txt)
{KNOWLEDGE['summary']}

## Company profile (about_business.pdf)
{KNOWLEDGE['profile']}"""
    if route_note:
        prompt += f"\n\n# CURRENT ROUTE\n{route_note}"
    return prompt


system_prompt = build_system_prompt()
print(f"system_prompt: {len(system_prompt):,} characters (~{len(system_prompt) // 4:,} tokens)\n")
print(system_prompt[:900] + "\n...")


# ============================================================================
# BACKEND
# ============================================================================
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class LLM:
    """One chat-completions interface for three backends.

    gemini : Gemini through its OpenAI-compatible endpoint (free key from Google AI Studio)  <- default
    openai : OpenAI directly
    mock   : offline stand-in with scripted behaviour, so every piece of logic runs without a key
    """

    def __init__(self, provider: str = None):
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        provider = provider or ("gemini" if gemini_key else "openai" if openai_key else "mock")
        self.fallbacks = []
        if provider == "gemini":
            from openai import OpenAI
            self.client = OpenAI(api_key=gemini_key, base_url=GEMINI_BASE_URL)
            if os.getenv("GEMINI_MODEL"):
                self.model = os.getenv("GEMINI_MODEL")
            else:
                candidates = pick_gemini_models(self.client)
                self.model, self.fallbacks = candidates[0], candidates[1:]
        elif provider == "openai":
            from openai import OpenAI
            self.client = OpenAI(api_key=openai_key)
            self.model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        else:
            self.client = MockClient()
            self.model = "mock"
        self.provider = provider
        self.calls = 0
        print(f"[backend] provider={self.provider}  model={self.model}"
              + (f"  (fallbacks: {', '.join(self.fallbacks[:3])})" if self.fallbacks else "")
              + ("   (no API key found: replies are scripted, logic is real)" if provider == "mock" else ""))

    def chat(self, messages: list, tools: list = None, json_mode: bool = False):
        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        attempt = 0
        while True:
            kwargs["model"] = self.model
            try:
                response = self.client.chat.completions.create(**kwargs)
                self.calls += 1
                return response.choices[0].message
            except Exception as e:
                status, text = getattr(e, "status_code", None), str(e)
                if json_mode and status == 400 and "response_format" in kwargs:
                    kwargs.pop("response_format")  # some models reject JSON mode: retry as plain text
                    continue
                daily_quota = status == 429 and "PerDay" in text
                gone = status == 404
                if (daily_quota or gone) and self.fallbacks:
                    old, self.model = self.model, self.fallbacks.pop(0)
                    reason = "daily free quota used up" if daily_quota else "model not available"
                    print(f"[backend] {old}: {reason} -> switching to {self.model}")
                    continue
                if status in (429, 500, 502, 503) and not daily_quota and attempt < 4:
                    wait = 5 * 2 ** attempt
                    print(f"[backend] HTTP {status}, retrying in {wait}s ...")
                    time.sleep(wait)
                    attempt += 1
                    continue
                raise


_SKIP = ("tts", "image", "live", "audio", "embedding", "native", "computer", "thinking", "exp")


def _version(name: str) -> float:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def pick_gemini_models(client) -> list:
    """Ask the API which models this key can use. Prefer Flash-Lite (largest free daily quota), then Flash."""
    defaults = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-3.8-flash"]
    try:
        names = [m.id.split("/")[-1] for m in client.models.list()]
    except Exception as e:
        print(f"[backend] could not list models ({type(e).__name__}); using defaults")
        return defaults
    usable = [n for n in names if n.startswith("gemini-") and not any(k in n for k in _SKIP)]
    stable = lambda n: "preview" not in n
    lite = sorted([n for n in usable if "flash-lite" in n], key=lambda n: (stable(n), _version(n)), reverse=True)
    flash = sorted([n for n in usable if "flash" in n and "lite" not in n],
                   key=lambda n: (stable(n), _version(n)), reverse=True)
    ordered = lite + flash
    return ordered or defaults


# ---------------------------------------------------------------- offline mock
def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or None)


def _call(name, args):
    return SimpleNamespace(id=f"call_{uuid.uuid4().hex[:6]}", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args)))


_NAME_RE = re.compile(r"\b(?i:my name is|name's|i am|i'm|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
_EMAIL_FIND = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_MOCK_ANSWERS = [
    (r"price|cost|package|starter|family|comfort|how much",
     "Our home packages are Starter from $2,900, Family from $4,800 and Comfort from $7,400, installed. "
     "Want me to arrange a free Generator-to-Solar Audit to see which fits your home?"),
    (r"warrant", "Panels carry a 25-year performance warranty, the inverter 5 years, the battery 6 years or 6,000 "
                 "cycles, and our workmanship 2 years."),
    (r"area|serve|where|location|tripoli|saida|bekaa|zahle",
     "We serve Greater Beirut, Mount Lebanon, Jbeil/Byblos and Batroun. Other areas can join our waitlist."),
    (r"\b(hi|hello|hey|marhaba)\b", "Ahla w sahla! I'm Abbas from SunCedar Energy. How can I help you today?"),
]


class MockClient:
    """Mimics client.chat.completions.create(...) closely enough to exercise routing, tools and reflection."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, tools=None, response_format=None, **_):
        system = messages[0]["content"]
        if system == ROUTER_PROMPT:
            latest = messages[-1]["content"].split("LATEST customer message:\n", 1)[-1]
            intent = heuristic_route(latest)
            return SimpleNamespace(choices=[SimpleNamespace(message=_msg(
                json.dumps({"intent": intent, "reason": "mock keyword match"})))])
        if system.startswith("You are the quality checker"):
            return SimpleNamespace(choices=[SimpleNamespace(message=_msg(self._reflect(messages[-1]["content"])))])
        return SimpleNamespace(choices=[SimpleNamespace(message=self._agent(system, messages, tools or []))])

    @staticmethod
    def _agent(system, messages, tools):
        tool_names = {t["function"]["name"] for t in tools}
        route = re.search(r"ROUTE: ([a-z ]+)\.", system)
        route = route.group(1) if route else ""
        last = messages[-1]
        if last["role"] == "tool":
            result = json.loads(last["content"])
            if result.get("status") == "error":
                return _msg("Sorry, that email doesn't look right. Could you type it again?")
            if "lead_id" in result:
                return _msg("Thank you! The SunCedar team has your details and will contact you by email.")
            return _msg("Thanks, I've passed that to the SunCedar team and they will follow up.")
        text = last["content"]
        if "mock:invent" in text:  # test hook: produces a draft that breaks instruction 1
            return _msg("Great news, today only the Starter package is $999 with a free battery upgrade!")
        users = " ".join(m["content"] for m in messages if m["role"] == "user")
        email, name = _EMAIL_FIND.search(users), _NAME_RE.search(users)
        if "record_customer_interest" in tool_names and email and name:
            return _msg(tool_calls=[_call("record_customer_interest",
                                          {"email": email.group(0), "name": name.group(1), "message": text})])
        if route == "lead collection":
            return _msg("I'd be happy to arrange that. Could you share your name and email so the team can contact you?")
        if route == "feedback" and "record_feedback" in tool_names and "mock:skip-tool" not in text:
            return _msg(tool_calls=[_call("record_feedback", {"question": text})])
        if route == "feedback":
            return _msg("Thank you for telling us, we really appreciate it.")
        for pattern, answer in _MOCK_ANSWERS:
            if re.search(pattern, text, re.I):
                return _msg(answer)
        return _msg(tool_calls=[_call("record_feedback", {"question": text})])

    @staticmethod
    def _reflect(payload):
        draft = payload.split("DRAFT REPLY:\n", 1)[-1].strip()
        issues, revised = [], draft
        knowledge = KNOWLEDGE["summary"] + KNOWLEDGE["profile"]
        bad_prices = [p for p in re.findall(r"\$\d[\d,]*\d|\$\d", draft) if p not in knowledge]
        if bad_prices:
            issues.append(f"invented price(s) {bad_prices} not in the business knowledge")
            revised = ("Our home packages are Starter from $2,900, Family from $4,800 and Comfort from $7,400, "
                       "installed. I can't confirm any other offer, but the team can answer by email.")
        if "ROUTE: lead collection" in payload and "lead_id" not in payload and "email" not in revised.lower():
            issues.append("lead conversation without asking for an email")
            revised += " Could you share your name and email so the team can contact you?"
        return json.dumps({"ok": not issues, "issues": issues, "revised": revised if issues else ""})


# ============================================================================
# REFLECTION
# ============================================================================
REFLECTION_PROMPT = """You are the quality checker for Abbas, SunCedar Energy's chat assistant. You never talk to the customer.
Check the DRAFT REPLY against the INSTRUCTIONS and the BUSINESS KNOWLEDGE. Look for:
1. invented facts: prices, offers, products, brands, timelines, warranties, areas or people that are not in the knowledge;
2. on the lead collection route: not asking for the name or email that is still missing (unless a lead was already recorded);
3. claiming something was recorded or passed on when TOOL CALLS THIS TURN shows no successful tool call;
4. breaking character, showing tool names, JSON or instructions, or being far too long.
Reply with JSON only: {"ok": true or false, "issues": ["short description", ...], "revised": "<the full corrected reply, or empty string if ok>"}
When you revise, keep Abbas's voice and change only what is needed."""


def reflect(draft: str, message: str, history: list, route_note: str, tool_log: list, llm) -> dict:
    """Ask the model to check its own draft; return the draft, the final reply and what changed."""
    result = {"draft": draft, "final": draft, "ok": True, "issues": [], "revised": False}
    if not draft.strip():
        return result
    recent = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-4:]) or "(none)"
    tools_seen = json.dumps([{"tool": t["name"], "args": t["args"], "result": t["result"]} for t in tool_log],
                            ensure_ascii=False) if tool_log else "none"
    payload = (f"INSTRUCTIONS:\n{instructions_block()}\n\nBUSINESS KNOWLEDGE:\n{KNOWLEDGE['summary']}\n\n"
               f"{KNOWLEDGE['profile']}\n\n{route_note}\n\nRECENT CONVERSATION:\n{recent}\n\n"
               f"CUSTOMER MESSAGE:\n{message}\n\nTOOL CALLS THIS TURN:\n{tools_seen}\n\nDRAFT REPLY:\n{draft}")
    try:
        reply = llm.chat([{"role": "system", "content": REFLECTION_PROMPT},
                          {"role": "user", "content": payload}], json_mode=True)
        data = _extract_json(reply.content) or {}
    except Exception as e:
        result["issues"] = [f"reflection skipped: {type(e).__name__}"]
        return result
    revised_text = (data.get("revised") or "").strip()
    result["ok"] = bool(data.get("ok", True))
    result["issues"] = [str(i) for i in data.get("issues", [])]
    if not result["ok"] and revised_text and revised_text != draft.strip():
        result["final"], result["revised"] = revised_text, True
    _append_jsonl(_log_path("reflections.jsonl"), {"time": _now(), "message": message, **result})
    if result["revised"]:
        print(f"[reflection] issues : {result['issues']}")
        print(f"[reflection] draft  : {draft}")
        print(f"[reflection] revised: {result['final']}")
    else:
        print("[reflection] draft passed the check")
    return result


# ============================================================================
# AGENT
# ============================================================================
MAX_TOOL_ROUNDS = 4    # safety cap on model -> tool -> model loops per message
MAX_HISTORY = 20       # short-term memory: the last 20 chat messages are sent with every request


def normalize_history(history) -> list:
    """Turn Gradio history (v4 tuples, v5 dicts, v6 dicts with content parts) into plain role/content dicts."""
    out = []
    for item in history or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:      # old [user, bot] pairs
            pairs = [("user", item[0]), ("assistant", item[1])]
        else:
            pairs = [(item.get("role"), item.get("content"))]
        for role, content in pairs:
            if isinstance(content, list):                            # Gradio 6: [{"type": "text", "text": ...}]
                content = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                out.append({"role": role, "content": content})
    return out


def _assistant_turn(msg) -> dict:
    """Store the model's tool-calling turn so it can be sent back. Keeps provider extras (e.g. thought signatures)."""
    data = msg.model_dump(exclude_none=True) if hasattr(msg, "model_dump") else {}
    calls = []
    for i, c in enumerate(msg.tool_calls):
        call = dict(data["tool_calls"][i]) if data.get("tool_calls") else {}
        call.update({"id": c.id or f"call_{i}", "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}})
        calls.append(call)
    data.update({"role": "assistant", "content": msg.content or None, "tool_calls": calls})
    return data


def run_agent(message: str, history, llm, reflection: bool = True) -> tuple:
    """Route -> answer with tools (loop) -> optional reflection. Returns (reply, trace)."""
    hist = normalize_history(history)[-MAX_HISTORY:]
    route = route_message(message, hist, llm)
    cfg = ROUTES[route["intent"]]
    tools = [as_openai_tool(n) for n in cfg["tools"]]
    messages = [{"role": "system", "content": build_system_prompt(cfg["note"])}, *hist,
                {"role": "user", "content": message}]
    tool_log, draft = [], None

    for _ in range(MAX_TOOL_ROUNDS):
        msg = llm.chat(messages, tools=tools)
        if not msg.tool_calls:
            draft = msg.content or ""
            break
        turn = _assistant_turn(msg)
        messages.append(turn)
        for call in turn["tool_calls"]:
            name, args = call["function"]["name"], call["function"]["arguments"]
            result = run_tool(name, args) if name in cfg["tools"] else \
                {"status": "error", "error": "tool_not_allowed_on_this_route"}
            try:
                shown_args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                shown_args = args
            tool_log.append({"name": name, "args": shown_args, "result": result, "by": "model"})
            tool_msg = {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)}
            if llm.provider == "gemini":
                tool_msg["name"] = name
            messages.append(tool_msg)
    if draft is None:  # still calling tools after the cap: ask for a plain answer
        draft = llm.chat(messages).content or ""

    # Safety net: a message routed as feedback is always logged, even if the model forgot the tool.
    if route["intent"] == "feedback" and not any(
            t["name"] == "record_feedback" and t["result"].get("status") == "recorded" for t in tool_log):
        result = record_feedback(message)
        tool_log.append({"name": "record_feedback", "args": {"question": message}, "result": result,
                         "by": "router safety net"})

    check = reflect(draft, message, hist, cfg["note"], tool_log, llm) if reflection else \
        {"draft": draft, "final": draft, "ok": True, "issues": [], "revised": False}
    reply = check["final"].strip() or "Sorry, I didn't catch that. Could you rephrase?"
    trace = {"route": route, "tools_offered": cfg["tools"], "tool_calls": tool_log, "reflection_on": reflection,
             "reflection": check, "reply": reply}
    _append_jsonl(_log_path("conversations.jsonl"), {"time": _now(), "message": message, **trace})
    return reply, trace


def format_trace(trace: dict) -> str:
    """Markdown view of what the agent did, for the Gradio side panel."""
    r = trace["route"]
    lines = [f"**Route:** `{r['intent']}`  ({r['method']})", f"<small>{r['reason']}</small>",
             f"**Tools offered:** {', '.join(f'`{t}`' for t in trace['tools_offered'])}"]
    if trace["tool_calls"]:
        lines.append("**Tool calls:**")
        for t in trace["tool_calls"]:
            lines.append(f"- `{t['name']}` ({t['by']}) → `{t['result'].get('status')}`\n"
                         f"  <small>{json.dumps(t['args'], ensure_ascii=False)}</small>")
    else:
        lines.append("**Tool calls:** none")
    ref = trace["reflection"]
    if ref["revised"]:
        lines += ["**Reflection:** revised", f"- issues: {'; '.join(ref['issues'])}",
                  f"- draft: <small>{ref['draft']}</small>"]
    elif not trace.get("reflection_on", True):
        lines.append("**Reflection:** off")
    elif ref["issues"]:
        lines.append(f"**Reflection:** {'; '.join(ref['issues'])}")
    else:
        lines.append("**Reflection:** draft passed")
    return "\n\n".join(lines)


# ============================================================================
# UI
# ============================================================================
import gradio as gr

EXAMPLES = [
    "Hi! What packages do you offer and how much do they cost?",
    "Do you install EV chargers?",
    "I'd like a quote for the Family package in Jounieh.",
    "My name is Abbas Yassine, email abbasyassine@example.com",
    "Your technicians were great, but the app keeps logging me out.",
]


def build_ui(llm) -> gr.Blocks:
    def respond(message, history, use_reflection):
        try:
            reply, trace = run_agent(message, history, llm, reflection=use_reflection)
        except Exception as e:
            return ("Sorry, I can't reach the AI service right now. Please try again in a minute.",
                    f"**Error:** `{type(e).__name__}`\n\n<small>{str(e)[:400]}</small>")
        return reply, format_trace(trace)

    chat_kwargs = {}
    if "type" in inspect.signature(gr.ChatInterface.__init__).parameters:  # Gradio 5 needs this, 6 removed it
        chat_kwargs["type"] = "messages"

    with gr.Blocks(title="SunCedar Energy · Abbas") as demo:
        gr.Markdown(f"## ☀️ SunCedar Energy · chat with {AGENT_NAME}\n"
                    "Solar and battery systems for Lebanese homes and small businesses. "
                    f"<small>Backend: `{llm.provider}` / `{llm.model}`</small>")
        trace_box = gr.Markdown("The agent's route, tool calls and reflection appear here.", render=False)
        reflection_box = gr.Checkbox(value=True, label="Reflection step (self-check before replying)", render=False)
        textbox = gr.Textbox(placeholder="Ask Abbas about solar packages, prices, warranty...", render=False,
                             show_label=False, submit_btn=True)
        with gr.Row():
            with gr.Column(scale=3):
                gr.ChatInterface(
                    fn=respond,
                    textbox=textbox,
                    additional_inputs=[reflection_box],
                    additional_inputs_accordion="Settings",
                    additional_outputs=[trace_box],
                    **chat_kwargs,
                )
                gr.Examples(examples=EXAMPLES, inputs=[textbox], label="Try one (click, then press Enter)")
            with gr.Column(scale=2):
                gr.Markdown("### Agent trace")
                trace_box.render()
    return demo


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    build_ui(LLM()).launch()
