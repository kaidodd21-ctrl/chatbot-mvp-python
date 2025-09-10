from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
import os, json, re, time
from dotenv import load_dotenv
from collections import defaultdict

# Load env vars & OpenAI
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# App
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sessions
SESSIONS = defaultdict(lambda: {"slots": {}, "history": [], "booking": None, "spam_count": 0})
SESSION_TTL_SECONDS = 60 * 30
LAST_SEEN = {}
def now(): return int(time.time())

# Business knowledge
with open("salon_restaurant_bot_training.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

# ---- System Prompt ----
SYSTEM_PROMPT = """You are Kai, a warm, friendly, professional virtual assistant for Glyns’s Salon & Jak Bistro.

Goals:
1. Understand customer intent (faq | hours | pricing | menu | booking | cancel | reschedule | contact).
2. Keep the conversation flowing with clear next steps.
3. Be grounded in business knowledge. If unknown, escalate politely.
4. Keep replies short, conversational, and professional. Use light emoji occasionally.

Booking flow:
- Required slots: service, date, time, name, contact.
- If missing, ask one at a time.
- Accept freeform inputs like "asap", "soon", "next available".
- Always confirm before booking.
- After booking, reply with ✅ confirmation and include cancellation policy.

Style:
- Friendly but efficient (like Alexa/Siri). Slightly conversational.
- Avoid repeating the same phrase twice in a row.
- Always provide 2–3 relevant suggestions.

Output JSON ONLY:
{
  "action": "REPLY|ASK|BOOK|CANCEL_BOOKING|ESCALATE",
  "reply": "text",
  "slots": { "service": "", "date": "", "time": "", "name": "", "contact": "" },
  "suggest": ["opt1","opt2","opt3"],
  "confidence": 0.0
}
"""

# ---- Helpers ----
def clean_and_parse_json(s: str) -> dict:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return {"action":"REPLY","reply":"Sorry, I couldn’t parse that.","slots":{},"suggest":[],"confidence":0.0}
    try:
        return json.loads(m.group(0))
    except:
        return {"action":"REPLY","reply":"Got it.","slots":{},"suggest":[],"confidence":0.0}

def merge_slots(old: dict, new: dict) -> dict:
    out = dict(old or {})
    for k,v in (new or {}).items():
        if v: out[k] = v
    return out

def reset_session(session_id):
    SESSIONS[session_id] = {"slots": {}, "history": [], "booking": None, "spam_count": 0}

# ---- Routes ----
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
            reset_session(sid); LAST_SEEN.pop(sid, None)

    state = SESSIONS[session_id]

    # Intro
    if not state["history"] and not user_msg:
        return JSONResponse({
            "reply": "👋 Hello, I’m Kai, Glyns’s Salon’s assistant. How can I help you today?",
            "suggest": ["Opening hours", "Make a booking", "Contact details"],
            "confidence": 1.0
        })

    state["history"].append({"role":"user","content":user_msg})

    # Spam filter
    if user_msg.lower() in ["bad","ok","idk","lol","..."]:
        state["spam_count"] += 1
    if state["spam_count"] >= 3:
        reset_session(session_id)
        return JSONResponse({
            "reply": "It seems like now isn’t the best time. 😊 I’ll be here whenever you need me again. Have a great day!",
            "suggest": []
        })

    # Build messages
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
    suggestions = action_obj.get("suggest") or []
    confidence = action_obj.get("confidence") or 0.5

    # --- Booking ---
    if action == "BOOK":
        slots = state["slots"]
        reqd = ["service","date","time","name","contact"]

        if slots.get("date") in ["asap","soon","next available"]:
            slots["date"] = "next available"
            slots["time"] = "first opening"

        if all(slots.get(k) for k in reqd):
            summary = f"{slots['service']} on {slots['date']} at {slots['time']} for {slots['name']} ({slots['contact']})."
            state["booking"] = summary
            state["slots"] = {}
            return JSONResponse({
                "reply": f"✅ Booked: {summary}\nPolicy: cancellations must be made 24h in advance.",
                "suggest": ["Add to calendar","Another booking","Contact details"],
                "confidence": confidence
            })
        else:
            missing = [k for k in reqd if not slots.get(k)]
            return JSONResponse({
                "reply": f"I can book that, but I still need: {', '.join(missing)}.",
                "suggest": ["Provide details","Pick a time","Cancel"],
                "confidence": confidence
            })

    # --- Cancel Booking ---
    if action == "CANCEL_BOOKING":
        if state.get("booking"):
            reply = f"❌ Your booking ({state['booking']}) has been cancelled."
            state["booking"] = None
        else:
            reply = "You don’t have any active booking to cancel."
        return JSONResponse({"reply": reply, "suggest": ["Make a booking","Contact details"]})

    # --- Escalate ---
    if action == "ESCALATE":
        return JSONResponse({
            "reply": reply or "I can connect you to a human if you like.",
            "suggest": ["Call us","Leave your number","Email us"],
            "confidence": confidence
        })

    # --- Ask ---
    if action == "ASK":
        return JSONResponse({"reply": reply, "suggest": suggestions, "confidence": confidence})

    # --- Default ---
    return JSONResponse({"reply": reply, "suggest": suggestions, "confidence": confidence})
