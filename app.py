from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
import os, json, re, time
from dotenv import load_dotenv
from collections import defaultdict

# --- Setup ---
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()

# Allow frontend (GitHub Pages)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Sessions ---
SESSIONS = defaultdict(lambda: {"slots": {}, "history": [], "booking": None, "spam_count": 0})
SESSION_TTL_SECONDS = 60 * 30
LAST_SEEN = {}
def now(): return int(time.time())

# --- Sidebar Defaults ---
DEFAULT_SIDEBAR = ["Opening hours", "Make a booking", "Contact details"]

def get_sidebar(state):
    """Dynamic sidebar: changes if booking exists."""
    if state.get("booking"):
        return ["Cancel booking", "Contact details"]
    return DEFAULT_SIDEBAR

# --- Escalation Rules ---
def should_escalate(reply: str, confidence: float) -> bool:
    low_conf = confidence < 0.7
    unsure = any(phrase in reply.lower() for phrase in ["not sure", "don’t know", "cannot help"])
    return low_conf or unsure

# --- Load knowledge base ---
with open("salon_restaurant_bot_training.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

# --- System Prompt ---
SYSTEM_PROMPT = """
You are Kai, a calm, fast, and friendly virtual assistant for Glyns’s Salon & Jak Bistro.

Goals:
1. Understand intent quickly (faq | hours | pricing | menu | booking | reschedule | cancel | contact).
2. Advance conversation with the fewest steps.
3. Be grounded in knowledge; if unknown, escalate politely.
4. Keep replies short, warm, and professional — but also personable. Avoid robotic repetition.

Booking flow:
- Required: service, date, time, name, contact.
- If missing, ask ONE at a time.
- Accept freeform like "next available", "anytime".
- Confirm summary before booking.
- Mention cancellation policy after booking.

Style:
- Conversational, like Alexa/Siri. Slight emoji use (😊👍).
- Offer 2–3 smart follow-ups.
- Avoid repeating the same phrase twice.

Output contract (always JSON only):
{
  "action": "REPLY|ASK|CHECK_AVAILABILITY|BOOK|ESCALATE|CANCEL_BOOKING|END_CHAT",
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
    user_msg = (payload.get("message") or "").strip().lower()

    # Session cleanup
    LAST_SEEN[session_id] = now()
    for sid,t in list(LAST_SEEN.items()):
        if now() - t > SESSION_TTL_SECONDS:
            SESSIONS.pop(sid, None); LAST_SEEN.pop(sid, None)

    state = SESSIONS[session_id]

    # --- Restart Chat ---
    if user_msg in ["restart", "reset", "new chat", "start over"]:
        SESSIONS[session_id] = {"slots": {}, "history": [], "booking": None, "spam_count": 0}
        return JSONResponse({
            "reply": "🔄 Chat restarted. Hi, I’m Kai! How can I help today?",
            "suggest": DEFAULT_SIDEBAR,
            "sidebar": get_sidebar(SESSIONS[session_id]),
            "status": "delivered"
        })

    # --- Spam filter ---
    if user_msg in ["bad","nonsense","blah","test","ok","end chat"]:
        state["spam_count"] += 1
        if state["spam_count"] > 4:
            return JSONResponse({
                "reply": "👋 I’ll step back now, but I wish you a great day!",
                "suggest": [],
                "sidebar": get_sidebar(state),
                "action": "END_CHAT",
                "status": "delivered"
            })

    # Intro (no input yet)
    if not payload.get("message"):
        return JSONResponse({
            "reply": "👋 Hello, I’m Kai, Glyns’s Salon’s virtual assistant. How can I help today?",
            "suggest": DEFAULT_SIDEBAR,
            "sidebar": get_sidebar(state),
            "status": "delivered"
        })

    # Append to history
    state["history"].append({"role":"user","content":user_msg})

    # Prepare LLM call
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
    suggestions = action_obj.get("suggest") or DEFAULT_SIDEBAR
    confidence = action_obj.get("confidence") or 0.5

    # --- Booking flow ---
    if action == "BOOK":
        slots = state["slots"]
        reqd = ["service","date","time","name","contact"]
        if all(slots.get(k) for k in reqd):
            summary = f"{slots['service']} on {slots['date']} at {slots['time']} for {slots['name']}."
            reply = f"✅ Booked: {summary} A confirmation has been sent. Policy: 24h cancellation."
            state["booking"] = summary
            state["slots"] = {}
            return JSONResponse({
                "reply": reply,
                "suggest": ["Add to calendar","Another booking","Cancel booking"],
                "sidebar": get_sidebar(state),
                "status": "delivered"
            })
        else:
            missing = [k for k in reqd if not slots.get(k)]
            reply = f"I can book that. I still need: {', '.join(missing)}."
            return JSONResponse({
                "reply": reply,
                "suggest": ["Provide details","Pick a time","Cancel"],
                "sidebar": get_sidebar(state),
                "status": "delivered"
            })

    # --- Cancel booking ---
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
            "status": "delivered"
        })

    # --- Escalation ---
    if action == "ESCALATE":
        return JSONResponse({
            "reply": reply or "I can connect you to a human if you like.",
            "suggest": ["📞 Call us","✉️ Email us","💬 Speak to staff"],
            "sidebar": get_sidebar(state),
            "status": "delivered"
        })

    # --- End chat ---
    if action == "END_CHAT":
        return JSONResponse({
            "reply": "👋 Thanks for chatting. Have a great day!",
            "suggest": [],
            "sidebar": [],
            "status": "read"
        })

    # --- Default reply ---
    return JSONResponse({
        "reply": reply,
        "suggest": suggestions,
        "sidebar": get_sidebar(state),
        "status": "delivered"
    })
