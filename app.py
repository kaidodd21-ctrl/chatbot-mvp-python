from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import uuid, random, re, subprocess
from rapidfuzz import fuzz, process
import spacy

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
            "history": []
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
# Booking Flow
# -----------------------------
def handle_booking(session: Dict, message: str) -> ChatResponse:
    slots = session["slots"]

    if not slots["service"]:
        if "hair" in message.lower():
            slots["service"] = "Haircut"
        elif "massage" in message.lower():
            slots["service"] = "Massage"
        elif "nail" in message.lower():
            slots["service"] = "Nails"
        else:
            reply = "Sure 👍 what service would you like to book?"
            return ChatResponse(reply=reply, suggestions=["Haircut", "Massage", "Nails"], session_id=session["id"])

    if not slots["datetime"]:
        if any(x in message.lower() for x in ["tomorrow", "friday", "saturday", "asap", "next"]):
            slots["datetime"] = message
        else:
            reply = "When would you like your appointment?"
            return ChatResponse(reply=reply, suggestions=["Tomorrow 3pm", "Friday 11am", "Saturday 2pm"], session_id=session["id"])

    if not slots["name"]:
        slots["name"] = message
        reply = "Got it 👍 What’s your name?"
        return ChatResponse(reply=reply, session_id=session["id"])

    if not slots["contact"]:
        if "@" in message or any(ch.isdigit() for ch in message):
            slots["contact"] = message
        else:
            reply = "And finally, could you share your phone or email for confirmation?"
            return ChatResponse(reply=reply, session_id=session["id"])

    reply = f"Perfect ✅ I’ve booked your {slots['service']} on {slots['datetime']} for {slots['name']}. Confirmation will be sent to {slots['contact']}."
    return ChatResponse(reply=reply, suggestions=["Cancel booking", "Make another booking"], session_id=session["id"])

# -----------------------------
# Smalltalk
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
        "hungry": ["I can’t cook 🍔 but I can schedule your pampering.", "Food is life 😋 but I’m here for bookings."],
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

    possible_name = extract_name(message)
    if possible_name:
        session["slots"]["name"] = possible_name
        reply = f"Nice to meet you, {possible_name} 😃"
        response = ChatResponse(reply=reply, suggestions=["Make a booking", "Opening hours"], session_id=session_id)
        remember(session, message, reply)
        return response

    if session["last_intent"] == "booking" or "book" in message.lower():
        session["last_intent"] = "booking"
        response = handle_booking(session, message)
        remember(session, message, response.reply)
        return response

    if "opening" in message.lower():
        reply = "We’re open Mon–Sat, 9am–6pm ⏰"
        response = ChatResponse(reply=reply, suggestions=["Make a booking", "Contact details"], session_id=session_id)
        remember(session, message, reply)
        return response

    if "contact" in message.lower():
        reply = "You can reach us at 📞 01234 567890 or ✉️ hello@example.com"
        response = ChatResponse(reply=reply, suggestions=["Make a booking", "Opening hours"], session_id=session_id)
        remember(session, message, reply)
        return response

    if "cancel" in message.lower():
        reply = "Okay, I’ve cancelled your booking ✅"
        response = ChatResponse(reply=reply, suggestions=["Make a new booking", "Contact support"], session_id=session_id)
        remember(session, message, reply)
        return response

    smalltalk = handle_smalltalk(message, session)
    if smalltalk:
        remember(session, message, smalltalk.reply)
        return smalltalk

    name = session["slots"].get("name")
    reply = (
        f"That’s interesting, {name} 🤔 I mostly help with bookings, opening hours, or contact details. Want me to show you?"
        if name else
        "That’s interesting 🤔 I mostly help with bookings, opening hours, or contact details. Want me to show you?"
    )

    response = ChatResponse(reply=reply, suggestions=["Make a booking", "Opening hours", "Contact details"], session_id=session_id)
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
    return {"status": "ok", "message": "Kai Virtual Assistant backend (V7.4 with pulsing status dot)"}
