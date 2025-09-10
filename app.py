from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
import os, json, re, time
from dotenv import load_dotenv
from collections import defaultdict
from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0  # make langdetect deterministic

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
SESSIONS = defaultdict(lambda: {"slots": {}, "history": [], "booking": None, "language": "en"})
SESSION_TTL_SECONDS = 60 * 30
LAST_SEEN = {}
def now(): return int(time.time())

# Sidebar defaults
DEFAULT_SIDEBAR = ["Opening hours", "Make a booking", "Contact details"]

def get_sidebar(state):
    if state.get("booking"):
        return ["Cancel booking", "Contact details"]
    return DEFAULT_SIDEBAR

# Escalation rule
def should_escalate(reply: str, confidence: float) -> bool:
    low_conf = confidence < 0.6
    unsure = any(phrase in reply.lower() for phrase in ["not sure", "don’t know", "cannot help"])
    return low_conf or unsure

# Load business knowledge
with open("salon_restaurant_bot_training.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

# System prompt with multilingual logic
SYSTEM_PROMPT = """You are “Kai”, a calm, fast, and friendly virtual assistant for Glyns’s Salon & Jak Bistro.
You can reply in English (default), French, Spanish, or most other major languages.
Rules:
- Reply in the user’s detected language if it is supported.
- If user explicitly asks what languages you speak, say:
  "I can assist you in English 🇬🇧, French 🇫🇷, Spanish 🇪🇸, or most other languages 🌍."
- If confidence <0.5, fallback politely in English.
- Remain consistent in the chosen language until user switches.
"""

# --- Helpers ---
def clean_and_parse_json(s: str) -> dict:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return {"action":"REPLY","reply":"Sorry, I couldn’t parse that.","slots":{},"suggest":[],"confidence":0.0}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"action":"REPLY","reply":"Got it.","slots":{},"suggest":[],"confidence":0.0}

def merge_slots(old: dict, new: dict) -> dict:
    out = dict(old or {})
    for k,v in (new or {}).items():
        if v: out[k]=v
    return out

# --- Routes ---

@app.get("/")
def root():
    return {"ok": True, "service": "chatbot", "status": "alive"}

@app.post("/chat")
async def chat(request: Request, session_id: str = Query(default="web")):
    payload = await request.json()
    user_msg = (payload.get("message") or "").strip()

    # Session cleanup
    LAST_SEEN[session_id] = now()
    for sid,t in list(LAST_SEEN.items()):
        if now() - t > SESSION_TTL_SECONDS:
            SESSIONS.pop(sid, None); LAST_SEEN.pop(sid, None)

    state = SESSIONS[session_id]

    # Intro
    if not user_msg:
        reply = ("👋 Hello, I’m Kai, Glyns’s Salon’s virtual assistant.\n"
                 "I can assist you in English 🇬🇧, French 🇫🇷, Spanish 🇪🇸, or most other languages 🌍.\n"
                 "Type in your preferred language and I’ll reply accordingly.")
        return JSONResponse({
            "reply": reply,
            "suggest": ["Opening hours", "Make a booking", "Contact details"],
            "sidebar": get_sidebar(state),
            "confidence": 1.0,
            "language": state["language"],
            "escalate": False
        })

    # Language detection
    try:
        detected_lang = detect(user_msg)
        state["language"] = detected_lang
    except Exception:
        detected_lang = "en"
        state["language"] = "en"

    # Add to history
    state["history"].append({"role":"user","content":user_msg})

    messages = [
        {"role":"system","content": SYSTEM_PROMPT},
        {"role":"system","content": f"KNOWLEDGE:\n{KNOWLEDGE}"},
        {"role":"system","content": f"CURRENT LANGUAGE: {state['language']}"},
        {"role":"system","content": f"CURRENT SLOTS (if any): {json.dumps(state.get('slots',{}))}"},
    ] + state["history"][-6:]

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.2
    )
    action_obj = clean_and_parse_json(resp.choices[0].message.content)

    # Merge slots
    state["slots"] = merge_slots(state.get("slots", {}), action_obj.get("slots", {}))
    action = (action_obj.get("action") or "REPLY").upper()
    reply = action_obj.get("reply") or "Happy to help."
    suggestions = action_obj.get("suggest") or []
    confidence = action_obj.get("confidence") or 0.5

    # --- Branches ---
    if action == "BOOK":
        slots = state["slots"]
        reqd = ["service","date","time","name","contact"]
        if all(slots.get(k) for k in reqd):
            summary = f"{slots['service']} on {slots['date']} at {slots['time']} for {slots['name']}."
            reply = f"✅ Booked: {summary} A confirmation has been sent. Policy: cancellations must be made 24h in advance."
            state["booking"] = summary
            state["slots"] = {}
            return JSONResponse({
                "reply": reply,
                "suggest": ["Add to calendar","Another booking","Cancel booking"],
                "sidebar": get_sidebar(state),
                "confidence": confidence,
                "language": state["language"],
                "escalate": False
            })

    if action == "CANCEL_BOOKING":
        if state.get("booking"):
            reply = f"❌ Your booking ({state['booking']}) has been cancelled."
            state["booking"] = None
        else:
            reply = "You don’t have any active booking to cancel."
        return JSONResponse({
            "reply": reply,
            "suggest": ["Make a new booking","Contact details"],
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "language": state["language"],
            "escalate": False
        })

    if action == "ESCALATE":
        return JSONResponse({
            "reply": reply or "I can connect you to a human if you like.",
            "suggest": ["Call us","Leave your number","Email us"],
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "language": state["language"],
            "escalate": True
        })

    # Default REPLY
    return JSONResponse({
        "reply": reply,
        "suggest": suggestions,
        "sidebar": get_sidebar(state),
        "confidence": confidence,
        "language": state["language"],
        "escalate": should_escalate(reply, confidence)
    })
