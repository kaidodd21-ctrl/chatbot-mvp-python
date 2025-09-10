from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
import os, json, re, time, random
from dotenv import load_dotenv
from collections import defaultdict

# Load env vars & init OpenAI client
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Create FastAPI app
app = FastAPI()

# Allow frontend (GitHub Pages) to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict to your frontend domain later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store
SESSIONS = defaultdict(lambda: {"slots": {}, "history": [], "booking": None, "smalltalk_count": 0})
SESSION_TTL_SECONDS = 60 * 30  # 30 mins
LAST_SEEN = {}
def now(): return int(time.time())

# Sidebar defaults
DEFAULT_SIDEBAR = ["Opening hours", "Make a booking", "Contact details"]

def get_sidebar(state):
    """Return sidebar options depending on context"""
    if state.get("booking"):
        return ["Cancel booking", "Contact details"]
    return DEFAULT_SIDEBAR

# Escalation rule
def should_escalate(reply: str, confidence: float) -> bool:
    low_conf = confidence < 0.7
    unsure = any(phrase in reply.lower() for phrase in ["not sure", "don’t know", "cannot help"])
    return low_conf or unsure

# Load business knowledge (FAQ/training file)
with open("salon_restaurant_bot_training.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

# Reset triggers (start fresh)
RESET_TRIGGERS = ["hello", "hi", "hey", "start", "restart", "new chat"]

# --- Smalltalk ---
SMALLTALK = {
    "greeting": {
        "triggers": ["hello", "helo", "hi", "hii", "hey", "heyy", "hiya"],
        "responses": [
            "Hello 👋 How can I help you today?",
            "Hi there! I’m here to help with bookings, services, or opening hours.",
            "Hey! Need help finding a service or making an appointment?",
            "Hi 👋 What would you like to do today — check hours, book, or ask about treatments?",
            "Hello! 😊 I can help you make a booking or answer your questions right away."
        ]
    },
    "how_are_you": {
        "triggers": ["how are you", "how’s it going", "you ok", "you okay", "hru", "how r u"],
        "responses": [
            "I’m doing great, thanks for asking! 😊 Want me to help with a booking?",
            "I’m well, thank you! How about you? Would you like me to check services?",
            "All good here! ✨ Do you want opening hours or a booking?",
            "I’m feeling good and ready to help. Shall I find availability for you?",
            "Doing well, thanks 🙏 I can check services or book something for you if you’d like."
        ]
    },
    # ... (other 13 categories like thanks, identity, compliments, jokes, etc. from V5.6) ...
}

def handle_smalltalk(user_msg: str, state: dict) -> str | None:
    msg = user_msg.lower().strip()
    msg = re.sub(r"[^a-z0-9\s]", "", msg)
    for intent, data in SMALLTALK.items():
        for trigger in data["triggers"]:
            if trigger in msg:
                state["smalltalk_count"] += 1
                response = random.choice(data["responses"])
                # Failsafe chit-chat redirect
                if state["smalltalk_count"] == 3:
                    response += " By the way, I can also help with bookings or opening hours!"
                elif state["smalltalk_count"] >= 5:
                    response = "It’s been nice chatting 😊 If that’s all for now, I’ll wish you a great day! 👋"
                    state["smalltalk_count"] = 0
                return response
    return None

# --- Routes ---

@app.get("/")
def root():
    return {"ok": True, "service": "chatbot", "status": "alive"}

@app.post("/chat")
async def chat(request: Request, session_id: str = Query(default="web")):
    payload = await request.json()
    user_msg = (payload.get("message") or "").strip().lower()

    # Session cleanup
    LAST_SEEN[session_id] = now()
    for sid, t in list(LAST_SEEN.items()):
        if now() - t > SESSION_TTL_SECONDS:
            SESSIONS.pop(sid, None)
            LAST_SEEN.pop(sid, None)

    state = SESSIONS[session_id]

    # ✅ Restart triggers
    if user_msg in RESET_TRIGGERS:
        SESSIONS[session_id] = {"slots": {}, "history": [], "booking": None, "smalltalk_count": 0}
        reply = "👋 Hello again, I’m Kai, Glyns’s Salon’s virtual assistant. How can I help today?"
        return JSONResponse({
            "reply": reply,
            "suggest": DEFAULT_SIDEBAR,
            "sidebar": DEFAULT_SIDEBAR,
            "confidence": 1.0,
            "escalate": False
        })

    # ✅ Empty intro case
    if not user_msg:
        reply = "👋 Hello, I’m Kai, Glyns’s Salon’s virtual assistant. How can I help today?"
        return JSONResponse({
            "reply": reply,
            "suggest": DEFAULT_SIDEBAR,
            "sidebar": DEFAULT_SIDEBAR,
            "confidence": 1.0,
            "escalate": False
        })

    # ✅ Smalltalk handling
    smalltalk_reply = handle_smalltalk(user_msg, state)
    if smalltalk_reply:
        return JSONResponse({
            "reply": smalltalk_reply,
            "suggest": DEFAULT_SIDEBAR,
            "sidebar": get_sidebar(state),
            "confidence": 1.0,
            "escalate": False
        })

    # If not smalltalk → normal AI logic
    state["history"].append({"role": "user", "content": user_msg})

    messages = [
        {"role": "system", "content": "You are Kai, Glyns’s Salon’s virtual assistant. Be helpful and concise."},
        {"role": "system", "content": f"KNOWLEDGE:\n{KNOWLEDGE}"},
        {"role": "system", "content": f"CURRENT SLOTS: {json.dumps(state.get('slots', {}))}"},
    ] + state["history"][-6:]

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.3
    )
    reply = resp.choices[0].message.content.strip()

    return JSONResponse({
        "reply": reply,
        "suggest": DEFAULT_SIDEBAR,
        "sidebar": get_sidebar(state),
        "confidence": 0.9,
        "escalate": should_escalate(reply, 0.9)
    })
