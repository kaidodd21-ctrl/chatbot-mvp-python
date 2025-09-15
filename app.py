from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import uuid, random

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
            }
        }
    return session_id

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
            return ChatResponse(
                reply="Sure 👍 what service would you like to book?",
                suggestions=["Haircut", "Massage", "Nails"],
                session_id=session["id"]
            )

    if not slots["datetime"]:
        if any(x in message.lower() for x in ["tomorrow", "friday", "saturday", "asap", "next"]):
            slots["datetime"] = message
        else:
            return ChatResponse(
                reply="When would you like your appointment?",
                suggestions=["Tomorrow 3pm", "Friday 11am", "Saturday 2pm"],
                session_id=session["id"]
            )

    if not slots["name"]:
        slots["name"] = message
        return ChatResponse(
            reply="Got it 👍 What’s your name?",
            session_id=session["id"]
        )

    if not slots["contact"]:
        if "@" in message or any(ch.isdigit() for ch in message):
            slots["contact"] = message
        else:
            return ChatResponse(
                reply="And finally, could you share your phone or email for confirmation?",
                session_id=session["id"]
            )

    return ChatResponse(
        reply=f"Perfect ✅ I’ve booked your {slots['service']} on {slots['datetime']} for {slots['name']}. Confirmation will be sent to {slots['contact']}.",
        suggestions=["Cancel booking", "Make another booking"],
        session_id=session["id"]
    )

# -----------------------------
# Smalltalk / Smart Suggestions
# -----------------------------
def handle_smalltalk(message: str, session_id: str) -> Optional[ChatResponse]:
    msg = message.lower()

    responses = {
        "greeting": ["Hey there 👋", "Hiya 🙌", "Yo, what’s up? 😎"],
        "how_are_you": ["I’m doing great, thanks 😊 How about you?", "Feeling chatty today 😁 How are you?"],
        "thanks": ["Anytime 🙌", "You’re very welcome!", "No worries 👍"],
        "bye": ["Catch you later 👋", "Bye! Take care 😊", "See ya soon 🚀"],
        "joke": ["😂 Why don’t skeletons fight? They don’t have the guts!", "🤣 I tried to book a haircut… but couldn’t make the cut!"],
        "hungry": ["I can’t cook 🍔 but I can schedule your pampering.", "Food is life 😋 but I’m here for bookings."],
    }

    if any(w in msg for w in ["hi", "hello", "hey", "yo"]):
        return ChatResponse(
            reply=random.choice(responses["greeting"]),
            suggestions=["Make a booking", "Opening hours"],
            session_id=session_id
        )

    if "how are you" in msg:
        return ChatResponse(
            reply=random.choice(responses["how_are_you"]),
            suggestions=["Good 👍", "Not bad", "Could be better"],
            session_id=session_id
        )

    if "thank" in msg:
        return ChatResponse(
            reply=random.choice(responses["thanks"]),
            suggestions=["Make a booking", "Contact details"],
            session_id=session_id
        )

    if any(w in msg for w in ["bye", "goodbye", "later", "see ya"]):
        return ChatResponse(
            reply=random.choice(responses["bye"]),
            suggestions=["Restart chat", "Contact details"],
            session_id=session_id
        )

    if "joke" in msg:
        return ChatResponse(
            reply=random.choice(responses["joke"]),
            suggestions=["Tell me another joke", "Back to booking"],
            session_id=session_id
        )

    if "hungry" in msg or "food" in msg:
        return ChatResponse(
            reply=random.choice(responses["hungry"]),
            suggestions=["Any food recommendations?", "Make a booking"],
            session_id=session_id
        )

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

    # Booking intent
    if "book" in message.lower():
        session["last_intent"] = "booking"
        return handle_booking(session, message)

    if "opening" in message.lower():
        return ChatResponse(
            reply="We’re open Mon–Sat, 9am–6pm ⏰",
            suggestions=["Make a booking", "Contact details"],
            session_id=session_id
        )

    if "contact" in message.lower():
        return ChatResponse(
            reply="You can reach us at 📞 01234 567890 or ✉️ hello@example.com",
            suggestions=["Make a booking", "Opening hours"],
            session_id=session_id
        )

    if "cancel" in message.lower():
        return ChatResponse(
            reply="Okay, I’ve cancelled your booking ✅",
            suggestions=["Make a new booking", "Contact support"],
            session_id=session_id
        )

    # Smalltalk
    smalltalk = handle_smalltalk(message, session_id)
    if smalltalk:
        return smalltalk

    # Continue booking if already in flow
    if session["last_intent"] == "booking":
        return handle_booking(session, message)

    # Fallback
    return ChatResponse(
        reply="That’s interesting 🤔 I mostly help with bookings, opening hours, or contact details. Want me to show you?",
        suggestions=["Make a booking", "Opening hours", "Contact details"],
        session_id=session_id
    )

# -----------------------------
# Root
# -----------------------------
@app.get("/")
def root():
    return {"status": "ok", "message": "Kai Virtual Assistant backend (V7.1)"}
