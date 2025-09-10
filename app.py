from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
import os, json, re, time
from dotenv import load_dotenv
from collections import defaultdict

# Load env vars & init OpenAI client
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# FastAPI app
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session state
SESSIONS = defaultdict(lambda: {"slots": {}, "history": [], "booking": None})
SESSION_TTL_SECONDS = 60 * 30
LAST_SEEN = {}
def now(): return int(time.time())

# Sidebar defaults
DEFAULT_SIDEBAR = ["Opening hours", "Make a booking", "Contact details"]

def get_sidebar(state):
    if state.get("booking"):
        return ["Cancel booking", "Contact details"]
    return DEFAULT_SIDEBAR

# Escalation check
def should_escalate(reply: str, confidence: float) -> bool:
    low_conf = confidence < 0.65
    unsure = any(p in reply.lower() for p in ["not sure", "don’t know", "cannot help"])
    return low_conf or unsure

# Load knowledge
with open("salon_restaurant_bot_training.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

# System prompt
SYSTEM_PROMPT = """You are Kai, a calm, warm, and professional virtual assistant for Glyns’s Salon & Jak Bistro.

Goals:
1) Understand intent quickly (faq | hours | pricing | menu | booking | reschedule | cancel | contact).
2) Advance conversations efficiently — ask only one missing detail at a time.
3) Be grounded in salon/bistro knowledge. If unknown, escalate politely with contact info.
4) Keep replies friendly, natural, and human-like but still concise. Avoid repeating the same phrasing.

Booking flow:
- Required: service, date, time, name, contact.
- Accept freeform like "asap", "next available".
- Always confirm before booking.
- After booking, mention cancellation policy.

Style:
- Conversational but focused, like Alexa or Siri.
- Use varied phrasing so replies don’t sound robotic.
- End with 2–3 smart follow-ups.

Output contract:
Return ONLY JSON:
{
  "action": "REPLY|ASK|CHECK_AVAILABILITY|BOOK|ESCALATE|CANCEL_BOOKING",
  "reply": "message",
  "slots": { "service": "", "date": "", "time": "", "name": "", "contact": "" },
  "suggest": ["opt1","opt2","opt3"],
  "confidence": 0.0
}
"""

# --- Helpers ---
def clean_and_parse_json(s: str) -> dict:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return {"action":"REPLY","reply":"Sorry, I couldn’t parse that.","slots":{},"suggest":DEFAULT_SIDEBAR,"confidence":0.0}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"action":"REPLY","reply":"Got it.","slots":{},"suggest":DEFAULT_SIDEBAR,"confidence":0.0}

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

    # Intro message
    if not user_msg:
        return JSONResponse({
            "reply": "👋 Hello, I’m Kai, Glyns’s Salon’s virtual assistant. How can I help you today?",
            "suggest": DEFAULT_SIDEBAR,
            "sidebar": get_sidebar(state),
            "confidence": 1.0,
            "escalate": False
        })

    # History
    state["history"].append({"role":"user","content":user_msg})

    messages = [
        {"role":"system","content": SYSTEM_PROMPT},
        {"role":"system","content": f"KNOWLEDGE:\n{KNOWLEDGE}"},
        {"role":"system","content": f"CURRENT SLOTS: {json.dumps(state.get('slots',{}))}"},
    ] + state["history"][-6:]

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.3
    )
    action_obj = clean_and_parse_json(resp.choices[0].message.content)

    # Merge slots
    state["slots"] = merge_slots(state.get("slots", {}), action_obj.get("slots", {}))
    action = (action_obj.get("action") or "REPLY").upper()
    reply = action_obj.get("reply") or "Happy to help."
    suggestions = (action_obj.get("suggest") or []) or DEFAULT_SIDEBAR
    confidence = action_obj.get("confidence") or 0.5

    # Booking
    if action == "BOOK":
        slots = state["slots"]
        reqd = ["service","date","time","name","contact"]
        if all(slots.get(k) for k in reqd):
            summary = f"{slots['service']} on {slots['date']} at {slots['time']} for {slots['name']}."
            state["booking"] = summary
            state["slots"] = {}
            reply = f"✅ Booked: {summary}. Policy: cancellations must be made 24h in advance."
            return JSONResponse({
                "reply": reply,
                "suggest": ["Add to calendar","Another booking","Cancel booking"],
                "sidebar": get_sidebar(state),
                "confidence": confidence,
                "escalate": False
            })
        else:
            missing = [k for k in reqd if not slots.get(k)]
            return JSONResponse({
                "reply": f"I can book that. I still need: {', '.join(missing)}.",
                "suggest": ["Provide details","Pick a time","Cancel"],
                "sidebar": get_sidebar(state),
                "confidence": confidence,
                "escalate": False
            })

    # Cancel booking
    if action == "CANCEL_BOOKING":
        if state.get("booking"):
            reply = f"❌ Your booking ({state['booking']}) has been cancelled."
            state["booking"] = None
        else:
            reply = "You don’t have any active booking to cancel."
        return JSONResponse({
            "reply": reply,
            "suggest": DEFAULT_SIDEBAR,
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "escalate": False
        })

    # Escalate
    if action == "ESCALATE":
        return JSONResponse({
            "reply": reply or "I can connect you to a human if you like.",
            "suggest": ["Call us","Leave your number","Email us"],
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "escalate": True
        })

    # Default
    return JSONResponse({
        "reply": reply,
        "suggest": suggestions,
        "sidebar": get_sidebar(state),
        "confidence": confidence,
        "escalate": should_escalate(reply, confidence)
    })
