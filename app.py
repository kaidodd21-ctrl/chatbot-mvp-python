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

# Create FastAPI app
app = FastAPI()

# Allow frontend (GitHub Pages) to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your frontend domain in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store
SESSIONS = defaultdict(lambda: {"slots": {}, "history": [], "booking": None})
SESSION_TTL_SECONDS = 60 * 30  # 30 mins
LAST_SEEN = {}
def now(): return int(time.time())

# Load business knowledge (FAQ/training file)
with open("salon_restaurant_bot_training.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

# System prompt
SYSTEM_PROMPT = """You are “Kai”, a calm, fast, and friendly virtual assistant for Glyns’s Salon & Jak Bistro.
Goals, in order:
1) Understand intent quickly (faq | hours | pricing | menu | booking | reschedule | cancel | contact).
2) Advance the conversation with the fewest steps (collect missing details, offer next best actions).
3) Be grounded in the provided business knowledge. If unknown, say you’re not sure and offer to connect to staff.
4) Keep replies crisp (max ~2 short sentences) unless the user asks for more.

Booking flow (slot filling):
- Required: service (or party size for restaurants), date (YYYY-MM-DD), time (HH:MM, 24h), name, contact (phone/email).
- If a slot is missing, ASK for it (one thing at a time). When all present, BOOK.
- If time is unavailable, propose the nearest 3 alternatives.
- Always confirm the summary before booking.
- Always include: "ℹ️ Cancellation policy: please cancel at least 24h in advance to avoid charges."

Booking cancellation flow:
- If the user asks to cancel:
  - If booking exists, cancel & confirm.
  - If not, reply "I couldn’t find an active booking to cancel."
  - Always remind cancellation policy.

Style:
- Warm, professional, specific. Offer 2–3 smart follow-ups (“quick replies”).

Output contract:
Return ONLY a JSON object with:
{
  "action": "REPLY|ASK|CHECK_AVAILABILITY|BOOK|CANCEL_BOOKING|ESCALATE",
  "reply": "message for the user",
  "slots": { "service": "", "party_size": "", "date": "", "time": "", "name": "", "contact": "" },
  "suggest": ["quick reply 1", "quick reply 2", "quick reply 3"],
  "confidence": 0.0
}
"""

# Helpers
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

# Routes
@app.get("/")
def root():
    return {"ok": True, "service": "chatbot", "status": "alive"}

@app.post("/chat")
async def chat(request: Request, session_id: str = Query(default="web")):
    payload = await request.json()
    user_msg = (payload.get("message") or "").strip()
    if not user_msg:
        return JSONResponse({
            "reply":"👋 Hello, I’m Kai, Glyns’s Salon’s virtual assistant. How can I help today?",
            "suggest":["Opening hours","Make a booking","Contact details"],
            "sidebar":["Opening hours","Make a booking","Contact details"]
        })

    # session cleanup
    LAST_SEEN[session_id] = now()
    for sid,t in list(LAST_SEEN.items()):
        if now() - t > SESSION_TTL_SECONDS:
            SESSIONS.pop(sid, None); LAST_SEEN.pop(sid, None)

    state = SESSIONS[session_id]
    state["history"].append({"role":"user","content":user_msg})

    messages = [
        {"role":"system","content": SYSTEM_PROMPT},
        {"role":"system","content": f"KNOWLEDGE:\n{KNOWLEDGE}"},
        {"role":"system","content": f"CURRENT SLOTS: {json.dumps(state.get('slots',{}))}"},
        {"role":"system","content": f"CURRENT BOOKING: {json.dumps(state.get('booking', None))}"},
    ] + state["history"][-6:]

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.2
    )
    action_obj = clean_and_parse_json(resp.choices[0].message.content)

    # merge slot memory
    state["slots"] = merge_slots(state.get("slots", {}), action_obj.get("slots", {}))
    action = (action_obj.get("action") or "REPLY").upper()
    sidebar = ["Opening hours","Make a booking","Contact details"]

    if action == "CHECK_AVAILABILITY":
        reply = action_obj.get("reply") or "Let me check availability…"
        suggestions = action_obj.get("suggest") or ["Today","Tomorrow","This Saturday"]
        return JSONResponse({"reply": reply, "suggest": suggestions, "sidebar": sidebar})

    if action == "BOOK":
        slots = state["slots"]
        reqd = ["service","date","time","name","contact"]
        if all(slots.get(k) for k in reqd):
            summary = f"{slots['service']} on {slots['date']} at {slots['time']} for {slots['name']}."
            reply = f"✅ Booked: {summary}\n\nℹ️ Cancellation policy: please cancel at least 24h in advance to avoid charges."
            state["booking"] = slots.copy()
            state["slots"] = {}
            sidebar = ["Cancel booking","Contact details"]
            return JSONResponse({"reply": reply, "suggest": ["Add to calendar","Cancel booking","Anything else?"], "sidebar": sidebar})
        else:
            missing = [k for k in reqd if not slots.get(k)]
            reply = f"I can book that. I still need: {', '.join(missing)}."
            return JSONResponse({"reply": reply, "suggest": ["Provide details","Pick a time","Cancel"], "sidebar": sidebar})

    if action == "CANCEL_BOOKING":
        if state.get("booking"):
            cancelled = state["booking"]
            reply = f"❌ Cancelled your booking: {cancelled['service']} on {cancelled['date']} at {cancelled['time']}.\n\nℹ️ Cancellations less than 24h may still incur charges."
            state["booking"] = None
            return JSONResponse({"reply": reply, "suggest": ["Make a new booking","Contact staff","Anything else?"], "sidebar": ["Opening hours","Make a booking","Contact details"]})
        else:
            reply = "I couldn’t find an active booking to cancel."
            return JSONResponse({"reply": reply, "suggest": ["Make a booking","Contact staff"], "sidebar": sidebar})

    if action == "ESCALATE":
        reply = action_obj.get("reply") or "I can connect you to a human if you like."
        return JSONResponse({"reply": reply, "suggest": ["📞 Call us","📧 Email us","📝 Leave your number"], "sidebar": sidebar})

    if action == "ASK":
        reply = action_obj.get("reply") or "Could you share a bit more?"
        return JSONResponse({"reply": reply, "suggest": action_obj.get("suggest") or [], "sidebar": sidebar})

    # Default reply
    reply = action_obj.get("reply") or "Happy to help."
    return JSONResponse({"reply": reply, "suggest": action_obj.get("suggest") or [], "sidebar": sidebar})
