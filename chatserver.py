import os
print(f">>> RUNNING {os.path.basename(__file__)} (final backend)")
print(">>> Running from:", os.path.abspath(__file__))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional, Tuple
import re, datetime, json, logging, random
from dateutil import parser as dtparser

# 🔗 Session persistence (JSON-backed)
from session_store import sessions, get_session, load_sessions, save_sessions

try:
    import yaml
except ImportError:
    yaml = None

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# -----------------------------
# Config & Defaults
# -----------------------------
def load_config() -> Dict:
    try:
        if yaml and os.path.exists("services.yaml"):
            return yaml.safe_load(open("services.yaml", "r", encoding="utf-8"))
        if os.path.exists("services.json"):
            return json.load(open("services.json", "r", encoding="utf-8"))
    except Exception as e:
        logging.error(f"Config load failed: {e}")
    return {
        "business": {
            "name": "Kai Demo Salon",
            "hours_text": "Mon–Sat, 9am–6pm",
            "contact_phone": "01234 567890",
            "contact_email": "hello@example.com",
        },
        "services": [
            {"name": "Haircut", "price": 25, "duration": "30 mins"},
            {"name": "Massage", "price": 40, "duration": "60 mins"},
            {"name": "Nails",   "price": 20, "duration": "30 mins"},
        ],
    }

CONFIG = load_config()
BUSINESS, SERVICES = CONFIG["business"], CONFIG["services"]

# -----------------------------
# Models
# -----------------------------
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    suggestions: List[str] = []
    session_id: str
    debug: Optional[Dict] = None

# -----------------------------
# Slot Order (strict)
# -----------------------------
SLOT_ORDER = ["service", "datetime", "name", "contact"]

# Ensure sessions are loaded on startup
load_sessions()

def make_response(reply: str, sugg: List[str], session: Dict) -> ChatResponse:
    # Persist every time we respond (cheap write, small JSON)
    save_sessions()
    return ChatResponse(
        reply=reply,
        suggestions=sugg,
        session_id=session["id"],
        debug={"slots": session["slots"]}
    )

# -----------------------------
# Helpers
# -----------------------------
SERVICE_SYNONYMS = {
    "haircut": ["cut", "trim", "style"],
    "massage": ["relaxation", "therapy"],
    "nails":   ["manicure", "pedicure"]
}

def detect_service(text: str) -> Optional[str]:
    t = text.lower()
    for s in SERVICES:
        name = s["name"].lower()
        if name in t:
            return s["name"]
        for syn in SERVICE_SYNONYMS.get(name, []):
            if syn in t:
                return s["name"]
    return None

def _pretty(dt: datetime.datetime) -> str:
    return dt.strftime("%A %d %b, %I:%M %p")

def parse_datetime_text(text: str) -> Optional[Dict[str, str]]:
    """
    Returns {'iso': ISO8601 string, 'pretty': human string} or None if invalid/past.
    """
    t = text.lower()
    now = datetime.datetime.now()

    # Basic relative parsing
    if "today" in t:
        dt = now
    elif "tomorrow" in t:
        dt = now + datetime.timedelta(days=1)
    else:
        m = re.match(r"in (\d+) (days?|weeks?)", t)
        if m:
            delta = int(m.group(1)) * (7 if "week" in m.group(2) else 1)
            dt = now + datetime.timedelta(days=delta)
        else:
            try:
                dt = dtparser.parse(text, fuzzy=True)
                # If time was not specified, keep it as parsed; the validator will handle "past"
            except Exception:
                return None

    # Reject past
    if dt < now:
        return None

    return {
        "iso": dt.isoformat(),
        "pretty": _pretty(dt)
    }

# Strict name capture only through regex phrases
def extract_name(msg: str) -> Optional[str]:
    for pat in [
        r"(?:i am|i'm|im)\s+(\w+)",
        r"my name is\s+(\w+)",
        r"call me\s+(\w+)"
    ]:
        m = re.search(pat, msg, re.I)
        if m:
            return m.group(1).capitalize()
    return None

def is_valid_contact(t: str) -> bool:
    # email
    if "@" in t and re.match(r"[^@]+@[^@]+\.[^@]+", t):
        return True
    # phone
    digits = re.sub(r"\D", "", t)
    return len(digits) >= 7

def list_services() -> str:
    return "Here are the services:\n" + "\n".join(
        [f"• {s['name']} — £{s['price']} ({s['duration']})" for s in SERVICES]
    )

# -----------------------------
# Booking Flow
# -----------------------------
def handle_booking(session: Dict, msg: str) -> ChatResponse:
    slots = session["slots"]

    # 1) Service
    if not slots["service"]:
        s = detect_service(msg)
        if s:
            slots["service"] = s
            return make_response(
                f"Great, I’ve got you down for {s}. When would you like it?",
                ["Tomorrow 3pm", "Saturday 11am"],
                session
            )
        else:
            # Offer services list if not detected
            return make_response(
                list_services(),
                [s["name"] for s in SERVICES],
                session
            )

    # 2) Datetime
    if slots["service"] and not slots["datetime"]:
        dt_obj = parse_datetime_text(msg)
        if dt_obj:
            slots["datetime"] = dt_obj  # {'iso': ..., 'pretty': ...}
        else:
            return make_response(
                "That time has already passed or wasn’t clear. Could you pick a future slot?",
                ["Tomorrow 3pm", "Saturday 11am"],
                session
            )

    # 3) Name (strict: must match phrases)
    if slots["service"] and slots["datetime"] and not slots["name"]:
        nm = extract_name(msg)
        if nm:
            slots["name"] = nm
        else:
            return make_response("What’s your name?", [], session)

    # 4) Contact
    if slots["service"] and slots["datetime"] and slots["name"] and not slots["contact"]:
        if is_valid_contact(msg):
            slots["contact"] = msg
        else:
            return make_response(
                "That doesn’t look like a valid phone or email. Could you try again?",
                [],
                session
            )

    # All filled → confirmation
    if all(slots[k] for k in ["service", "datetime", "name", "contact"]):
        b = {**slots, "created_at": datetime.datetime.now().isoformat()}
        session["bookings"].append(b)
        dt_display = slots["datetime"]["pretty"] if isinstance(slots["datetime"], dict) else str(slots["datetime"])
        return make_response(
            f"✅ Booked {slots['service']} on {dt_display} for {slots['name']}.\n"
            f"Confirm to {slots['contact']}.\nPay now?",
            ["Pay now", "Change time"],
            session
        )

    # If somehow still missing, ask for next piece (safety)
    order = ["service", "datetime", "name", "contact"]
    prompts = {
        "service": "Which service?",
        "datetime": "When would you like it?",
        "name": "What’s your name?",
        "contact": "Your phone or email?",
    }
    for k in order:
        if not slots[k]:
            return make_response(prompts[k], [], session)

    # Fallback
    return make_response("Something went wrong, please try again.", [], session)

# -----------------------------
# Smalltalk
# -----------------------------
SMALLTALK = {
    "hi":    ["Hey 👋", "Hiya 🙌"],
    "thank": ["You’re welcome 👍"],
    "bye":   ["See you 👋"]
}

def handle_smalltalk(msg: str, session: Dict) -> Optional[ChatResponse]:
    m = msg.lower()
    for k, v in SMALLTALK.items():
        if k in m:
            return make_response(random.choice(v), ["Make a booking"], session)
    return None

# -----------------------------
# /chat
# -----------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(p: ChatRequest):
    try:
        sid, session = get_session(p.session_id, SLOT_ORDER)
        msg = p.message.strip()

        # Utility commands
        low = msg.lower()
        if low in ["pay", "pay now", "checkout"]:
            return make_response("Here’s your checkout link: https://example-payments/abc",
                                 ["Make another booking"], session)
        if "opening" in low:
            return make_response(f"We’re open {BUSINESS['hours_text']}", ["Make a booking"], session)
        if "contact" in low and ("details" in low or "number" in low or "email" in low):
            return make_response(f"☎ {BUSINESS['contact_phone']} ✉ {BUSINESS['contact_email']}",
                                 ["Make a booking"], session)
        if "cancel" in low:
            session["slots"] = {k: None for k in SLOT_ORDER}
            return make_response("Booking cancelled ✅", ["Make a new booking"], session)

        # Booking intent
        if session["last_intent"] == "booking" or "book" in low or detect_service(low):
            session["last_intent"] = "booking"
            return handle_booking(session, msg)

        # Smalltalk
        st = handle_smalltalk(msg, session)
        if st:
            return st

        # Default help
        return make_response(
            "I mostly help with bookings, hours or contact. Want me to show you?",
            ["Make a booking", "Opening hours"],
            session
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return ChatResponse(
            reply=f"⚠️ Internal Error: {e}",
            suggestions=[],
            session_id=p.session_id or "none",
            debug={
                "trace": traceback.format_exc(),
                "slots": sessions.get(p.session_id, {}).get("slots") if p.session_id else None
            }
        )

# -----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Kai Virtual Assistant backend (session persistence)"
    }

# -----------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("chatserver:app", host="127.0.0.1", port=8000, reload=True)
