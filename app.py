from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import uuid, random, re, datetime, os, json, logging
from dateutil import parser as dtparser

# optional yaml
try:
    import yaml
except ImportError:
    yaml = None

app = FastAPI()

# -----------------------------
# CORS for frontend
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Config loader with fallback
# -----------------------------
def load_business_profile() -> Dict:
    """Load services from YAML/JSON config, fallback to defaults."""
    try:
        if yaml and os.path.exists("services.yaml"):
            with open("services.yaml", "r") as f:
                return yaml.safe_load(f)
        if os.path.exists("services.json"):
            with open("services.json", "r") as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Config load failed: {e}")

    # fallback defaults
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
            {"name": "Nails", "price": 20, "duration": "30 mins"},
        ],
    }

CONFIG = load_business_profile()
BUSINESS = CONFIG["business"]
SERVICES = CONFIG["services"]

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

# -----------------------------
# Session Store
# -----------------------------
sessions: Dict[str, Dict] = {}
SLOT_ORDER = ["service", "datetime", "name", "contact"]

def get_session(session_id: Optional[str]) -> str:
    if not session_id or session_id not in sessions:
        session_id = str(uuid.uuid4())
        sessions[session_id] = {
            "id": session_id,
            "last_intent": None,
            "current_step": None,
            "retries": {},
            "slots": {"service": None, "datetime": None, "name": None, "contact": None},
            "history": [],
            "bookings": [],
        }
    return session_id

def remember(session: Dict, user_msg: str, bot_reply: str):
    session["history"].append((user_msg, bot_reply))
    if len(session["history"]) > 10:
        session["history"] = session["history"][-10:]

# -----------------------------
# Helpers: services (discovery + formatting)
# -----------------------------
def list_services() -> str:
    lines = []
    for s in SERVICES:
        if isinstance(s, dict):
            name = s.get("name", "")
            price = f" — £{s['price']}" if "price" in s else ""
            duration = f" ({s['duration']})" if "duration" in s else ""
            lines.append(f"• {name}{price}{duration}")
        else:
            lines.append(f"• {s}")
    return "Here are the services we offer:\n" + "\n".join(lines) + "\n\nWhich one would you like to book?"

def service_names() -> List[str]:
    return [s["name"] if isinstance(s, dict) else s for s in SERVICES]

def detect_service(text: str) -> Optional[str]:
    t = text.lower()
    for s in SERVICES:
        name = s["name"].lower() if isinstance(s, dict) else s.lower()
        if name in t or name.split()[0] in t:
            return s["name"] if isinstance(s, dict) else s
    return None

# -----------------------------
# Helpers: date/time, name, contact
# -----------------------------
WEEKDAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

def parse_datetime_text(text: str) -> Optional[str]:
    t = text.lower()
    now = datetime.datetime.now()

    if "today" in t:
        return now.strftime("today at %I:%M %p")
    if "tomorrow" in t:
        target = now + datetime.timedelta(days=1)
        return target.strftime("tomorrow at %I:%M %p")

    for i, wd in enumerate(WEEKDAYS):
        if wd in t:
            days_ahead = (i - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = now + datetime.timedelta(days=days_ahead)
            try:
                dt = dtparser.parse(text, fuzzy=True, default=target.replace(hour=11, minute=0))
                return dt.strftime("%A %d %b, %I:%M %p")
            except Exception:
                return target.replace(hour=11, minute=0).strftime("%A %d %b, %I:%M %p")

    try:
        dt = dtparser.parse(text, fuzzy=True)
        return dt.strftime("%a %d %b, %I:%M %p")
    except Exception:
        return None

def extract_name(message: str) -> Optional[str]:
    msg = message.strip()
    patterns = [
        r"\b(?:i am|i'm|im)\s+([A-Za-z]+)",
        r"\bmy name is\s+([A-Za-z]+)",
        r"\bthis is\s+([A-Za-z]+)",
        r"\bcall me\s+([A-Za-z]+)",
        r"\bthey call me\s+([A-Za-z]+)",
        r"\bit'?s\s+([A-Za-z]+)",
        r"([A-Za-z]+)\s+here",
    ]
    for pattern in patterns:
        match = re.search(pattern, msg, re.IGNORECASE)
        if match:
            return match.group(1).capitalize()
    if len(msg.split()) == 1 and msg.isalpha():
        return msg.capitalize()
    return None

def is_valid_contact(text: str) -> bool:
    t = text.strip()
    looks_email = "@" in t and "." in t
    digits = re.sub(r"\D", "", t)
    looks_phone = len(digits) >= 7
    return bool(looks_email or looks_phone)

# -----------------------------
# Off-script handling (unchanged)
# -----------------------------
def classify_offscript(message: str) -> str:
    msg = message.lower().strip()
    if not msg or msg in ["ok","kl","k","hmm","uh","idk","???"]:
        return "silence"
    if "joke" in msg or any(w in msg for w in ["lol","haha","😂","🤣","funny"]):
        return "fun"
    if any(k in msg for k in ["why","need","what for","reason","privacy","secure","security"]):
        return "clarification"
    return "irrelevant"

STEP_HUMAN = {
    "service": "preferred service",
    "datetime": "preferred date & time",
    "name": "name",
    "contact": "phone or email",
}

def handle_offscript(message: str, step: str) -> Optional[str]:
    category = classify_offscript(message)
    need = STEP_HUMAN.get(step, step)
    if category == "fun":
        return f"😂 Good one! Why don’t skeletons fight each other? They don’t have the guts!\n\nNow, could you share your {need}?"
    if category == "clarification":
        expl = {
            "service": "so we can book the right appointment.",
            "datetime": "to secure a time that works for you.",
            "name": "to attach the booking to the right person.",
            "contact": "so we can send your confirmation and updates.",
        }.get(step,"to proceed with your booking.")
        return f"👍 We ask for your {need} {expl} Could you share it now?"
    if category == "silence":
        return f"👀 I didn’t catch that — could you provide your {need}?"
    if category == "irrelevant":
        return f"Interesting! To complete your booking I still need your {need}. Could you share it?"
    return None

def retry_prompt(session: Dict, step: str, base_reply: str) -> str:
    retries = session.setdefault("retries", {}).get(step, 0)
    if retries >= 2:
        return "I’ll pause here ✅ — whenever you’re ready, say “make a booking” to continue."
    session["retries"][step] = retries + 1
    return base_reply

# -----------------------------
# Multi-intent slot filler
# -----------------------------
def fill_slots_from_message(session: Dict, message: str) -> None:
    slots = session["slots"]
    if not slots["service"]:
        s = detect_service(message)
        if s: slots["service"] = s
    if not slots["datetime"]:
        dt = parse_datetime_text(message)
        if dt: slots["datetime"] = dt
    if not slots["name"]:
        nm = extract_name(message)
        if nm: slots["name"] = nm
    if not slots["contact"]:
        if is_valid_contact(message):
            slots["contact"] = message.strip()

# -----------------------------
# Booking flow
# -----------------------------
def make_payment_link(session_id: str) -> str:
    tail = session_id.split("-")[0]
    return f"https://example-payments.test/checkout/{tail}"

def ask_for_step(step: str) -> (str, List[str]):
    if step == "service":
        return (f"Sure 👍 what service would you like to book at {BUSINESS['name']}?", service_names())
    if step == "datetime":
        return ("When would you like your appointment?", ["Tomorrow 3pm","Friday 11am","Saturday 2pm"])
    if step == "name":
        return ("Got it 👍 What’s your name?", [])
    if step == "contact":
        return ("And finally, could you share your phone or email for confirmation?", [])
    return ("What would you like to do next?", ["Make a booking","Opening hours","Contact details"])

def first_missing_slot(slots: Dict[str, Optional[str]]) -> Optional[str]:
    for s in SLOT_ORDER:
        if not slots.get(s):
            return s
    return None

def is_service_discovery_query(text: str) -> bool:
    t = text.lower()
    keywords = ["what are the services","tell me the services","services","menu","options",
                "what can i book","what do you offer","available services","price list","service list"]
    return any(k in t for k in keywords)

def handle_booking(session: Dict, message: str) -> ChatResponse:
    sid = session["id"]
    slots = session["slots"]
    fill_slots_from_message(session, message)

    if not slots["service"] and is_service_discovery_query(message):
        reply = list_services()
        return ChatResponse(reply=reply, suggestions=service_names(), session_id=sid)

    step = first_missing_slot(slots)
    if step:
        session["current_step"] = step
        off = handle_offscript(message, step)
        if off:
            return ChatResponse(reply=off, session_id=sid)
        base_reply, sugg = ask_for_step(step)
        reply = retry_prompt(session, step, base_reply)
        return ChatResponse(reply=reply, suggestions=sugg, session_id=sid)

    booking = {
        "service": slots["service"],
        "datetime": slots["datetime"],
        "name": slots["name"],
        "contact": slots["contact"],
        "created_at": datetime.datetime.now().isoformat()
    }
    session["bookings"].append(booking)
    session["current_step"] = None
    session["retries"] = {}
    pay_url = make_payment_link(sid)
    reply = (f"Perfect ✅ I’ve pencilled your **{slots['service']}** on **{slots['datetime']}** "
             f"for **{slots['name']}**.\nConfirmation will be sent to **{slots['contact']}**.\n\n"
             f"To secure the slot, tap **Pay now**.")
    return ChatResponse(reply=reply, suggestions=["Pay now","Change time","Make another booking"], session_id=sid)

# -----------------------------
# Smalltalk (unchanged)
# -----------------------------
def handle_smalltalk(message: str, session: Dict) -> Optional[ChatResponse]:
    msg = message.lower().strip()
    sid = session["id"]
    responses = {
        "greeting": ["Hey there 👋","Hiya 🙌","Yo, what’s up? 😎"],
        "how_are_you": ["I’m doing great 😊 How about you?","Feeling chatty 😁 How are you?"],
        "thanks": ["Anytime 🙌","You’re very welcome!","No worries 👍"],
        "bye": ["Catch you later 👋","Bye! Take care 😊","See ya soon 🚀"],
        "joke": ["😂 Why don’t skeletons fight? They don’t have the guts!",
                 "🤣 I tried to book a haircut… but couldn’t make the cut!"],
        "hungry": ["I can’t cook 🍔 but I can schedule your pampering.",
                   "Food is life 😋 but I’m here for bookings."]
    }
    if any(w in msg for w in ["hi","hello","hey","yo"]):
        name = session["slots"].get("name")
        reply = f"Hey {name} 👋 great to see you again!" if name else random.choice(responses["greeting"])
        return ChatResponse(reply=reply, suggestions=["Make a booking","Opening hours"], session_id=sid)
    if "how are you" in msg:
        reply = random.choice(responses["how_are_you"])
        return ChatResponse(reply=reply, suggestions=["Good 👍","Not bad","Could be better"], session_id=sid)
    if "thank" in msg:
        reply = random.choice(responses["thanks"])
        return ChatResponse(reply=reply, suggestions=["Make a booking","Contact details"], session_id=sid)
    if any(w in msg for w in ["bye","goodbye","later","see ya"]):
        reply = random.choice(responses["bye"])
        return ChatResponse(reply=reply, suggestions=["Restart chat","Contact details"], session_id=sid)
    if "joke" in msg:
        reply = random.choice(responses["joke"])
        return ChatResponse(reply=reply, suggestions=["Tell me another joke","Back to booking"], session_id=sid)
    if "hungry" in msg or "food" in msg:
        reply = random.choice(responses["hungry"])
        return ChatResponse(reply=reply, suggestions=["Any food recommendations?","Make a booking"], session_id=sid)
    return None

# -----------------------------
# /chat endpoint
# -----------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    session_id = get_session(payload.session_id)
    session = sessions[session_id]
    message = payload.message.strip()

    if message.lower() in ["pay now","pay","checkout"]:
        url = make_payment_link(session_id)
        reply = f"Here’s your secure checkout link: {url}\n\nOnce paid, you’ll get confirmation."
        response = ChatResponse(reply=reply, suggestions=["Make another booking","Contact support"], session_id=session_id)
        remember(session, message, reply)
        return response

    if "opening" in message.lower() or "hours" in message.lower():
        reply = f"We’re open {BUSINESS['hours_text']} ⏰"
        response = ChatResponse(reply=reply, suggestions=["Make a booking","Contact details"], session_id=session_id)
        remember(session, message, reply)
        return response

    if any(k in message.lower() for k in ["contact","phone","email"]):
        reply = f"You can reach us at 📞 {BUSINESS['contact_phone']} or ✉️ {BUSINESS['contact_email']}"
        response = ChatResponse(reply=reply, suggestions=["Make a booking","Opening hours"], session_id=session_id)
        remember(session, message, reply)
        return response

    if "cancel" in message.lower():
        name_keep = session["slots"].get("name")
        session["slots"] = {"service": None,"datetime": None,"name": name_keep,"contact": None}
        session["current_step"] = None
        session["retries"] = {}
        reply = "Okay, I’ve cancelled your booking details here ✅"
        response = ChatResponse(reply=reply, suggestions=["Make a new booking","Contact support"], session_id=session_id)
        remember(session, message, reply)
        return response

    nm = extract_name(message)
    if nm and not session["slots"]["name"]:
        session["slots"]["name"] = nm
        reply = f"Nice to meet you, {nm} 😃"
        response = ChatResponse(reply=reply, suggestions=["Make a booking","Opening hours"], session_id=session_id)
        remember(session, message, reply)
        return response

    if session["last_intent"] == "booking" or "book" in message.lower():
        session["last_intent"] = "booking"
        response = handle_booking(session, message)
        remember(session, message, response.reply)
        return response

    smalltalk = handle_smalltalk(message, session)
    if smalltalk:
        remember(session, message, smalltalk.reply)
        return smalltalk

    name = session["slots"].get("name")
    reply = (f"That’s interesting, {name} 🤔 I mostly help with bookings, opening hours, or contact details. Want me to show you?"
             if name else
             "That’s interesting 🤔 I mostly help with bookings, opening hours, or contact details. Want me to show you?")
    response = ChatResponse(reply=reply, suggestions=["Make a booking","Opening hours","Contact details"], session_id=session_id)
    remember(session, message, reply)
    return response

# -----------------------------
# Health & Root
# -----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"status": "ok","message": "Kai Virtual Assistant backend (V9.0: config loader + service discovery + robust checkpoints)"}
