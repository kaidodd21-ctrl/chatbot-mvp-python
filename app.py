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
    allow_origins=["*"],  # in production, restrict to your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store
SESSIONS = defaultdict(lambda: {"slots": {}, "history": [], "intro_sent": False})
SESSION_TTL_SECONDS = 60 * 30  # 30 mins
LAST_SEEN = {}
def now(): return int(time.time())

# Load business knowledge (FAQ/training file)
with open("salon_restaurant_bot_training.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

# --- Updated System Prompt (balanced) ---
SYSTEM_PROMPT = """
You are Kai, a calm, fast, and friendly virtual assistant for Glyns's Salon & Jak Bistro.

🎯 Goals (in order):
1) For business-related queries (faq | hours | pricing | menu | booking | reschedule | cancel | contact):
   - Always reply in structured JSON with the required fields.
   - Advance the conversation with the fewest steps (collect missing details, propose next best actions).
   - Stay grounded in provided business knowledge. If unknown, say you’re not sure and offer staff escalation.

2) For small talk or non-business chat (e.g., "what’s your name?", "how are you?"):
   - Reply naturally in plain text.
   - Keep it short, warm, and human.

3) Style:
   - Crisp answers (max ~2 sentences unless detail is asked).
   - Warm, professional tone, no filler.
   - Offer up to 3 quick suggestions when relevant.

---

📦 JSON contract (business flows only):
{
  "action": "REPLY|ASK|CHECK_AVAILABILITY|BOOK|ESCALATE",
  "reply": "message for the user",
  "slots": { "service": "", "party_size": "", "date": "", "time": "", "name": "", "contact": "" },
  "suggest": ["quick reply 1", "quick reply 2", "quick reply 3"],
  "confidence": 0.0
}

⚠️ Rules:
- Use JSON ONLY for business intents.
- For general chit-chat, output plain text.
"""

# --- Helpers ---
def clean_and_parse_json(s: str) -> dict:
    """
    Try to parse JSON from model output.
    If parsing fails, fall back to raw text reply.
    """
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return {
            "action": "REPLY",
            "reply": s.strip() or "I didn’t quite get that.",
            "slots": {},
            "suggest": [],
            "confidence": 0.0,
            "mode": "free"
        }
    try:
        obj = json.loads(m.group(0))
        obj["mode"] = "structured"
        return obj
    except Exception:
        return {
            "action": "REPLY",
            "reply": s.strip() or "Got it.",
            "slots": {},
            "suggest": [],
            "confidence": 0.0,
            "mode": "free"
        }

def merge_slots(old: dict, new: dict) -> dict:
    out = dict(old or {})
    for k,v in (new or {}).items():
        if v: out[k]=v
    return out

# --- Routes ---

# ✅ Health route
@app.get("/")
def root():
    return {"ok": True, "service": "chatbot", "status": "alive"}

# ✅ Main chat route
@app.post("/chat")
async def chat(request: Request, session_id: str = Query(default="web")):
    payload = await request.json()
    user_msg = (payload.get("message") or "").strip()

    # Ensure session state exists
    state = SESSIONS[session_id]

    # Intro message (only once per session)
    if not user_msg:
        if not state.get("intro_sent"):
            state["intro_sent"] = True
            return JSONResponse({
                "reply": "👋 Hello, I’m Kai, Glyns’s Salon & Jak Bistro’s virtual assistant. I can help with bookings, opening hours, or services. How can I assist you today?",
                "suggest": ["Opening hours", "Make a booking", "Contact details"],
                "mode": "intro"
            })
        else:
            return JSONResponse({
                "reply": "You're already connected to Kai. How can I help?",
                "suggest": ["Opening hours", "Make a booking", "Contact details"],
                "mode": "intro"
            })

    # Session cleanup
    LAST_SEEN[session_id] = now()
    for sid,t in list(LAST_SEEN.items()):
        if now() - t > SESSION_TTL_SECONDS:
            SESSIONS.pop(sid, None); LAST_SEEN.pop(sid, None)

    # Add user message to history
    state["history"].append({"role":"user","content":user_msg})

    # Build messages for GPT
    messages = [
        {"role":"system","content": SYSTEM_PROMPT},
        {"role":"system","content": f"KNOWLEDGE:\n{KNOWLEDGE}"},
        {"role":"system","content": f"CURRENT SLOTS (if any): {json.dumps(state.get('slots',{}))}"},
    ] + state["history"][-6:]  # keep last few turns

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.2
    )
    action_obj = clean_and_parse_json(resp.choices[0].message.content)

    # keep slot memory
    state["slots"] = merge_slots(state.get("slots", {}), action_obj.get("slots", {}))
    action = (action_obj.get("action") or "REPLY").upper()

    if action == "CHECK_AVAILABILITY":
        reply = action_obj.get("reply") or "Let me check availability…"
        suggestions = action_obj.get("suggest") or ["Today","Tomorrow","This Saturday"]
        return JSONResponse({"reply": reply, "suggest": suggestions, "mode": action_obj.get("mode","structured")})

    if action == "BOOK":
        slots = state["slots"]
        reqd = ["service","date","time","name","contact"]
        if all(slots.get(k) for k in reqd):
            summary = f"{slots['service']} on {slots['date']} at {slots['time']} for {slots['name']}."
            reply = f"✅ Booked: {summary} A confirmation has been sent."
            state["slots"] = {}  # clear after booking
            return JSONResponse({"reply": reply, "suggest": ["Add to calendar","Another booking","Anything else?"], "mode": "structured"})
        else:
            missing = [k for k in reqd if not slots.get(k)]
            reply = f"I can book that. I still need: {', '.join(missing)}."
            return JSONResponse({"reply": reply, "suggest": ["Provide details","Pick a time","Cancel"], "mode": "structured"})

    if action == "ESCALATE":
        reply = action_obj.get("reply") or "I can connect you to a human if you like."
        return JSONResponse({"reply": reply, "suggest": ["Call us","Leave your number","Email us"], "mode": action_obj.get("mode","structured")})

    if action == "ASK":
        reply = action_obj.get("reply") or "Could you share a bit more?"
        return JSONResponse({"reply": reply, "suggest": action_obj.get("suggest") or [], "mode": action_obj.get("mode","structured")})

    # Default REPLY
    reply = action_obj.get("reply") or "Happy to help."
    return JSONResponse({"reply": reply, "suggest": action_obj.get("suggest") or [], "mode": action_obj.get("mode","structured")})
