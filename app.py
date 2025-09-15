from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import uuid, random, re, subprocess, datetime
from rapidfuzz import fuzz, process
import spacy
from dateutil import parser as dtparser

# -----------------------------
# Auto-download spaCy model if missing
# -----------------------------
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
    nlp = spacy.load("en_core_web_sm")

app = FastAPI()

# Allow frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Business Profile (multi-biz ready)
# -----------------------------
BUSINESS = {
    "name": "Kai Demo Salon",
    "hours_text": "Mon–Sat, 9am–6pm",
    "contact_phone": "01234 567890",
    "contact_email": "hello@example.com",
    # Services are used for suggestions + service detection
    "services": ["Haircut", "Massage", "Nails"],
    # Later you can map service -> duration/price
}

# -----------------------------
# Data Models
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

def get_session(session_id: Optional[str]) -> str:
    if not session_id or session_id not in sessions:
        session_id = str(uuid.uuid4())
        sessions[session_id] = {
            "last_intent": None,
            "slots": {
                "service": None,
                "datetime": None,
                "name": None,
                "contact": None,
            },
            "history": [],
            "bookings": []  # simple in-mem log for this session
        }
    return session_id

def remember(session: Dict, user_msg: str, bot_reply: str):
    session["history"].append((user_msg, bot_reply))
    if len(session["history"]) > 10:
        session["history"] = session["history"][-10:]

# -----------------------------
# Name Extraction (Hybrid)
# -----------------------------
common_names = ["Jake", "John", "Alex", "Kai", "Sarah", "Emma", "Tom", "Michael", "Emily", "Sophia"]

def extract_name(message: str) -> Optional[str]:
    msg = message.strip()

    patterns = [
        r"\b(?:i am|i'm|im)\s+([A-Z][a-z]+)",
        r"\bmy name is\s+([A-Z][a-z]+)",
        r"\bthis is\s+([A-Z][a-z]+)",
        r"\bcall me\s+([A-Z][a-z]+)",
        r"\bthey call me\s+([A-Z][a-z]+)",
        r"\bit'?s\s+([A-Z][a-z]+)",
        r"([A-Z][a-z]+)\s+here"
    ]
    for pattern in patterns:
        match = re.search(pattern, msg, re.IGNORECASE)
        if match:
            candidate = match.group(1).title()
            return fuzzy_name(candidate)

    if len(msg.split()) == 1 and msg[0].isupper():
        return fuzzy_name(msg.title())

    doc = nlp(msg)
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            return fuzzy_name(ent.text.title())

    return None

def fuzzy_name(name: str) -> str:
    best, score, _ = process.extractOne(name, common_names, scorer=fuzz.ratio)
    if score > 80:
        return best
    return name

# -----------------------------
# Date/Time Helpers (lightweight)
# -----------------------------
WEEKDAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

def parse_datetime_text(text: str) -> Optional[str]:
    """
    Returns a normalized readable datetime string or None.
    Handles simple terms: 'today', 'tomorrow', weekdays, or free-form like 'Fri 3pm', '2025-09-20 14:00'.
    """
    t = text.lower()

    now = datetime.datetime.now()
    # Simple keywords
    if "today" in t:
        return now.strftime("today at %I:%M %p")
    if "tomorrow" in t:
        target = now + datetime.timedelta(days=1)
        return target.strftime("tomorrow at %I:%M %p")

    # Weekday like "friday 11am"
    for i, wd in enumerate(WEEKDAYS):
        if wd in t:
            # find next wd (including this week)
            days_ahead = (i - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = now + datetime.timedelta(days=days_ahead)
            # try extracting a time with dateutil; if none, default to 11:00
            try:
                dt = dtparser.parse(text, fuzzy=True, default=target.replace(hour=11, minute=0, second=0, microsecond=0))
                return dt.strftime("%A %d %b, %I:%M %p")
            except Exception:
                return target.replace(hour=11, minute=0).strftime("%A %d %b, %I:%M %p")

    # Free-form parse attempt (e.g., "Fri 3pm", "20 Sept 14:00")
    try:
        dt = dtparser.parse(text, fuzzy=True)
        return dt.strftime("%a %d %b, %I:%M %p")
    except Exception:
        return None

def detect_service(text: str) -> Optional[str]:
    t = text.lower()
    for s in BUSINESS["services"]:
        if s.lower().split()[0] in t or s.lower() in t:
            return s
    return None

# -----------------------------
# Payment Stub (placeholder)
# -----------------------------
def make_payment_link(session_id: str) -> str:
    # Replace this later with a real Stripe/Checkout URL
    tail = session_id.split("-")[0]
    return f"https://example-payments.test/checkout/{tail}"

# -----------------------------
# Booking Flow (preserves existing logic; adds parsing/validation)
# -----------------------------
def handle_booking(session: Dict, message: str) -> ChatResponse:
    slots = session["slots"]
    sid = session["id"]

    # 1) Service
    if not slots["service"]:
        maybe = detect_service(message)
        if maybe:
            slots["service"] = maybe
        else:
            reply = f"Sure 👍 what service would you like to book at {BUSINESS['name']}?"
            return ChatResponse(reply=reply, suggestions=BUSINESS["services"], session_id=sid)

    # 2) Datetime
    if not slots["datetime"]:
        guessed = parse_datetime_text(message)
        if guessed:
            slots["datetime"] = guessed
        else:
            reply = "When would you like your appointment?"
            return ChatResponse(
                reply=reply,
                suggestions=["Tomorrow 3pm", "Friday 11am", "Saturday 2pm"],
                session_id=sid
            )

    # 3) Name
    if not slots["name"]:
        # Try extraction one more time from this message
        nm = extract_name(message)
        if nm:
            slots["name"] = nm
        else:
            reply = "Got it 👍 What’s your name?"
            return ChatResponse(reply=reply, session_id=sid)

    # 4) Contact
    if not slots["contact"]:
        # very light validation
        text = message.strip()
        looks_email = "@" in text and "." in text
        looks_phone = any(ch.isdigit() for ch in text) and len(re.sub(r"\D", "", text)) >= 7
        if looks_email or looks_phone:
            slots["contact"] = text
        else:
            reply = "And finally, could you share your phone or email for confirmation?"
            return ChatResponse(reply=reply, session_id=sid)

    # All slots present → "confirm" the booking (in-memory)
    booking = {
        "service": slots["service"],
        "datetime": slots["datetime"],
        "name": slots["name"],
        "contact": slots["contact"],
        "created_at": datetime.datetime.now().isoformat()
    }
    session["bookings"].append(booking)

    pay_url = make_payment_link(sid)
    reply = (
        f"Perfect ✅ I’ve pencilled your **{slots['service']}** on **{slots['datetime']}** for **{slots['name']}**.\n"
        f"Confirmation will be sent to **{slots['contact']}**.\n\n"
        f"To secure the slot, tap **Pay now** to complete checkout."
    )
    return ChatResponse(
        reply=reply,
        suggestions=["Pay now", "Change time", "Make another booking"],
        session_id=sid
    )

# -----------------------------
# Smalltalk (unchanged content, uses business details where helpful)
# -----------------------------
def handle_smalltalk(message: str, session: Dict) -> Optional[ChatResponse]:
    msg = message.lower().strip()
    sid = session["id"]

    responses = {
        "greeting": ["Hey there 👋", "Hiya 🙌", "Yo, what’s up? 😎"],
        "how_are_you": ["I’m doing great, thanks 😊 How about you?", "Feeling chatty today 😁 How are you?"],
        "thanks": ["Anytime 🙌", "You’re very welcome!", "No worries 👍"],
        "bye": ["Catch you later 👋", "Bye! Take care 😊", "See ya soon 🚀"],
        "joke": ["😂 Why don’t skeletons fight? They don’t have the guts!", "🤣 I tried to book a haircut… but couldn’t make the cut!"],
        "hungry": ["I can’t cook 🍔 but I can schedule your pampering.", "Food is life 😋 but I’m here for bookings."]
    }

    if any(w in msg for w in ["hi", "hello", "hey", "yo"]):
        name = session["slots"].get("name")
        reply = f"Hey {name} 👋 great to see you again!" if name else random.choice(responses["greeting"])
        return ChatResponse(reply=reply, suggestions=["Make a booking", "Opening hours"], session_id=sid)

    if "how are you" in msg:
        reply = random.choice(responses["how_are_you"])
        return ChatResponse(reply=reply, suggestions=["Good 👍", "Not bad", "Could be better"], session_id=sid)

    if "thank" in msg:
        reply = random.choice(responses["thanks"])
        return ChatResponse(reply=reply, suggestions=["Make a booking", "Contact details"], session_id=sid)

    if any(w in msg for w in ["bye", "goodbye", "later", "see ya"]):
        reply = random.choice(responses["bye"])
        return ChatResponse(reply=reply, suggestions=["Restart chat", "Contact details"], session_id=sid)

    if "joke" in msg:
        reply = random.choice(responses["joke"])
        return ChatResponse(reply=reply, suggestions=["Tell me another joke", "Back to booking"], session_id=sid)

    if "hungry" in msg or "food" in msg:
        reply = random.choice(responses["hungry"])
        return ChatResponse(reply=reply, suggestions=["Any food recommendations?", "Make a booking"], session_id=sid)

    return None

# -----------------------------
# Chat Endpoint
# -----------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    session_id = get_session(payload.session_id)
    session = sessions[session_id]
    session["id"] = session_id
    message = payload.message.strip()

    # Special: "Pay now" button → return stub link
    if message.lower() in ["pay now", "pay", "checkout"]:
        url = make_payment_link(session_id)
        reply = f"Here’s your secure checkout link: {url}\n\nOnce paid, you’ll receive a confirmation by email/SMS."
        response = ChatResponse(reply=reply, suggestions=["Make another booking", "Contact support"], session_id=session_id)
        remember(session, message, reply)
        return response

    # Update name if found (keeps original behavior)
    possible_name = extract_name(message)
    if possible_name:
        session["slots"]["name"] = possible_name
        reply = f"Nice to meet you, {possible_name} 😃"
        response = ChatResponse(reply=reply, suggestions=["Make a booking", "Opening hours"], session_id=session_id)
        remember(session, message, reply)
        return response

    # Booking intent (keeps original trigger)
    if session["last_intent"] == "booking" or "book" in message.lower():
        session["last_intent"] = "booking"
        response = handle_booking(session, message)
        remember(session, message, response.reply)
        return response

    # Opening hours / contact (uses business profile)
    if "opening" in message.lower() or "hours" in message.lower():
        reply = f"We’re open {BUSINESS['hours_text']} ⏰"
        response = ChatResponse(reply=reply, suggestions=["Make a booking", "Contact details"], session_id=session_id)
        remember(session, message, reply)
        return response

    if "contact" in message.lower() or "phone" in message.lower() or "email" in message.lower():
        reply = f"You can reach us at 📞 {BUSINESS['contact_phone']} or ✉️ {BUSINESS['contact_email']}"
        response = ChatResponse(reply=reply, suggestions=["Make a booking", "Opening hours"], session_id=session_id)
        remember(session, message, reply)
        return response

    if "cancel" in message.lower():
        # simple cancel: clear slots (preserve history)
        session["slots"] = {"service": None, "datetime": None, "name": session["slots"].get("name"), "contact": None}
        reply = "Okay, I’ve cancelled your booking details here ✅"
        response = ChatResponse(reply=reply, suggestions=["Make a new booking", "Contact support"], session_id=session_id)
        remember(session, message, reply)
        return response

    # Smalltalk
    smalltalk = handle_smalltalk(message, session)
    if smalltalk:
        remember(session, message, smalltalk.reply)
        return smalltalk

    # Default fallback (personalized if name known)
    name = session["slots"].get("name")
    reply = (
        f"That’s interesting, {name} 🤔 I mostly help with bookings, opening hours, or contact details. Want me to show you?"
        if name else
        "That’s interesting 🤔 I mostly help with bookings, opening hours, or contact details. Want me to show you?"
    )

    response = ChatResponse(
        reply=reply,
        suggestions=["Make a booking", "Opening hours", "Contact details"],
        session_id=session_id
    )
    remember(session, message, reply)
    return response

# -----------------------------
# Health Check Endpoint
# -----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# -----------------------------
# Root
# -----------------------------
@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Kai Virtual Assistant backend (V8.0: business profile + payment stub + improved datetime)"
    }
