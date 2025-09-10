from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
import os, json, re, time
from dotenv import load_dotenv
from collections import defaultdict
from langdetect import detect, DetectorFactory

# Make langdetect stable
DetectorFactory.seed = 0

# Load env vars & init OpenAI client
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Create FastAPI app
app = FastAPI()

# Allow frontend (GitHub Pages) to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict to frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store
SESSIONS = defaultdict(lambda: {"slots": {}, "history": [], "booking": None, "lang": "en"})
SESSION_TTL_SECONDS = 60 * 30  # 30 mins
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
    unsure = any(p in reply.lower() for p in ["not sure", "don’t know", "cannot help"])
    return low_conf or unsure

# Load business knowledge
with open("salon_restaurant_bot_training.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

# --- Prompt ---
SYSTEM_PROMPT = """You are Kai, a calm, fast, and friendly virtual assistant for Glyns’s Salon & Jak Bistro.
Goals:
1) Understand intent quickly (faq | hours | pricing | menu | booking | reschedule | cancel | contact).
2) Advance the conversation with the fewest steps.
3) Be grounded in business knowledge. If unknown, escalate politely.
4) Keep replies short unless asked for detail.

Booking flow:
- Required: service, date, time, name, contact.
- If missing, ask one at a time.
- Accept freeform values like "next available", "asap", "anytime".
- Always confirm before booking.
- After booking, mention cancellation policy.

Style:
- Warm, professional, specific. Offer 2–3 smart follow-ups.

Output contract:
Return ONLY JSON with fields:
{
  "action": "REPLY|ASK|CHECK_AVAILABILITY|BOOK|ESCALATE|CANCEL_BOOKING",
  "reply": "message",
  "slots": { "service": "", "date": "", "time": "", "name": "", "contact": "" },
  "suggest": ["opt1","opt2","opt3"],
  "confidence": 0.0
}
"""

# --- JSON Repair Helper ---
def safe_parse_json(raw: str) -> dict:
    """Ensure valid JSON response, repair if needed."""
    try:
        return json.loads(raw)
    except:
        pass

    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except:
            pass

    # Try repair with secondary GPT call
    try:
        repair = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Fix the following into valid JSON matching schema."},
                {"role": "user", "content": raw}
            ],
            temperature=0
        )
        return json.loads(repair.choices[0].message.content)
    except:
        return {
            "action": "ESCALATE",
            "reply": "I’m not sure I understood, would you like me to connect you to staff?",
            "slots": {},
            "suggest": ["Call us", "Email us"],
            "confidence": 0.3
        }

def merge_slots(old: dict, new: dict) -> dict:
    out = dict(old or {})
    for k, v in (new or {}).items():
        if v:
            out[k] = v
    return out

# --- Routes ---

@app.get("/")
def root():
    return {"ok": True, "service": "chatbot", "status": "alive"}

@app.post("/chat")
async def chat(request: Request, session_id: str = Query(default="web")):
    payload = await request.json()
    user_msg = (payload.get("message") or "").strip()

    # Cleanup expired sessions
    LAST_SEEN[session_id] = now()
    for sid, t in list(LAST_SEEN.items()):
        if now() - t > SESSION_TTL_SECONDS:
            SESSIONS.pop(sid, None); LAST_SEEN.pop(sid, None)

    state = SESSIONS[session_id]

    # Intro message
    if not user_msg:
        reply = "👋 Hello, I’m Kai, Glyns’s Salon’s virtual assistant. I can help with bookings, hours, or services. You can also chat with me in English, French, Spanish, or other languages 🌍."
        return JSONResponse({
            "reply": reply,
            "suggest": DEFAULT_SIDEBAR,
            "sidebar": get_sidebar(state),
            "confidence": 1.0,
            "escalate": False,
            "language": state.get("lang", "en")
        })

    # Detect language
    lang = "en"
    try:
        lang = detect(user_msg)
    except:
        pass
    state["lang"] = lang

    # Add user message
    state["history"].append({"role": "user", "content": user_msg})

    # Call model
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"User language: {lang}. Reply in this language."},
        {"role": "system", "content": f"KNOWLEDGE:\n{KNOWLEDGE}"},
        {"role": "system", "content": f"CURRENT SLOTS: {json.dumps(state.get('slots', {}))}"},
    ] + state["history"][-6:]

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.2
    )
    action_obj = safe_parse_json(resp.choices[0].message.content)

    # Slot handling
    state["slots"] = merge_slots(state.get("slots", {}), action_obj.get("slots", {}))
    action = (action_obj.get("action") or "REPLY").upper()
    reply = action_obj.get("reply") or "Let me check that."
    suggestions = action_obj.get("suggest") or []
    confidence = action_obj.get("confidence") or 0.5

    # --- Actions ---
    if action == "BOOK":
        slots = state["slots"]
        reqd = ["service", "date", "time", "name", "contact"]
        if all(slots.get(k) for k in reqd):
            summary = f"{slots['service']} on {slots['date']} at {slots['time']} for {slots['name']}."
            reply = f"✅ Booked: {summary}. Policy: cancellations must be made 24h in advance."
            state["booking"] = summary
            state["slots"] = {}
            return JSONResponse({
                "reply": reply,
                "suggest": ["Add to calendar", "Another booking", "Cancel booking"],
                "sidebar": get_sidebar(state),
                "confidence": confidence,
                "escalate": False,
                "language": lang
            })
        else:
            missing = [k for k in reqd if not slots.get(k)]
            reply = f"I can book that, but I still need: {', '.join(missing)}."
            return JSONResponse({
                "reply": reply,
                "suggest": ["Provide details", "Pick a time", "Cancel"],
                "sidebar": get_sidebar(state),
                "confidence": confidence,
                "escalate": False,
                "language": lang
            })

    if action == "CANCEL_BOOKING":
        if state.get("booking"):
            reply = f"❌ Your booking ({state['booking']}) has been cancelled."
            state["booking"] = None
        else:
            reply = "You don’t have any active booking to cancel."
        return JSONResponse({
            "reply": reply,
            "suggest": ["Make a new booking", "Contact details"],
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "escalate": False,
            "language": lang
        })

    if action == "ESCALATE":
        return JSONResponse({
            "reply": reply or "I can connect you to a human if you like.",
            "suggest": ["Call us", "Leave your number", "Email us"],
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "escalate": True,
            "language": lang
        })

    if action == "ASK":
        return JSONResponse({
            "reply": reply or "Could you share a bit more?",
            "suggest": suggestions,
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "escalate": should_escalate(reply, confidence),
            "language": lang
        })

    # Default REPLY
    return JSONResponse({
        "reply": reply,
        "suggest": suggestions,
        "sidebar": get_sidebar(state),
        "confidence": confidence,
        "escalate": should_escalate(reply, confidence),
        "language": lang
    })
