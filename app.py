from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

app = FastAPI()

# Allow frontend (GitHub Pages / localhost) to call backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Data Models
# -----------------------------
class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str
    suggestions: List[str] = []

# -----------------------------
# Mock availability (placeholder for V6.5 calendar integration)
# -----------------------------
def get_available_slots() -> List[str]:
    return ["Tomorrow 3pm", "Friday 11am", "Saturday 2pm"]

# -----------------------------
# Chat Endpoint
# -----------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    message = payload.message.lower()

    if "book" in message:
        return ChatResponse(
            reply="Sure 👍 here are some available times:",
            suggestions=get_available_slots()
        )
    elif "opening" in message:
        return ChatResponse(
            reply="We’re open Mon–Sat, 9am–6pm ⏰",
            suggestions=["Make a booking", "Contact details"]
        )
    elif "contact" in message:
        return ChatResponse(
            reply="You can reach us at 📞 01234 567890 or ✉️ hello@example.com",
            suggestions=["Make a booking", "Opening hours"]
        )
    elif "cancel" in message:
        return ChatResponse(
            reply="Okay, I’ve cancelled your booking ✅",
            suggestions=["Make a new booking", "Contact support"]
        )
    elif "hi" in message or "hello" in message:
        return ChatResponse(
            reply="Hi 👋 I’m Kai, your assistant. How can I help today?",
            suggestions=["Make a booking", "Opening hours", "Contact details"]
        )
    else:
        return ChatResponse(
            reply="Got it 👍",
            suggestions=[]
        )

# -----------------------------
# Root Endpoint (health check)
# -----------------------------
@app.get("/")
def root():
    return {"status": "ok", "message": "Kai Virtual Assistant backend (V6.4 polished)"}
