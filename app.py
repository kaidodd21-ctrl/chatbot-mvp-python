from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI
import os, json, re, time
from dotenv import load_dotenv
from collections import defaultdict
from langdetect import detect, DetectorFactory

# Fix randomness in langdetect
DetectorFactory.seed = 0

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

Style:
- Warm, professional, specific. No filler. Use bullet points sparingly. Offer 2–3 smart follow-ups (“quick replies”).

Multilingual:
- Detect the user’s language (if not English). Reply in the same language when possible.
- If asked “what languages” → say you support English 🇬🇧, French 🇫🇷, Spanish 🇪🇸, and most others 🌍.
- If confidence is too low, fallback politely in English.

Output contract:
Return ONLY a compact JSON object with fields:
{
  "action": "REPLY|ASK|CHECK_AVAILABILITY|BOOK|ESCALATE|CANCEL_BOOKING",
  "reply": "message for the user",
  "slots": { "service": "", "party_size": "", "date": "", "time": "", "name": "", "contact": "" },
  "suggest": ["quick reply 1", "quick reply 2", "quick reply 3"],
  "confidence": 0.0
}
"""

# --- Helpers ---

def clean_and_parse_json(s: str) -> dict:
    """
    Try to parse JSON from model output.
    If it fails, retry by asking OpenAI to reformat into valid JSON.
    If still fails, fall back to plain REPLY.
    """
    # Step 1: Direct parse
    m = re.search(r"\{.*\}", s or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass

    # Step 2: Retry with OpenAI to fix JSON
    try:
        retry = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "You are a strict JSON reformatter."},
                {"role": "user", "content": f"Fix this into valid JSON only, no extra text:\n{s}"}
            ]
        )
        fixed = retry.choices[0].message.content
        m2 = re.search(r"\{.*\}", fixed or "", re.S)
        if m2:
            return json.loads(m2.group(0))
    except Exception:
        pass

    # Step 3: Graceful fallback
    return {
        "action": "REPLY",
        "reply": s.strip() if s else "I didn’t quite catch that.",
        "slots": {},
        "suggest": [],
        "confidence": 0.3
    }

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
    forced_lang = payload.get("language")

    # Session cleanup
    LAST_SEEN[session_id] = now()
    for sid,t in list(LAST_SEEN.items()):
        if now() - t > SESSION_TTL_SECONDS:
            SESSIONS.pop(sid, None); LAST_SEEN.pop(sid, None)

    state = SESSIONS[session_id]

    # Detect language if user sent a message
    if user_msg:
        try:
            detected = detect(user_msg)
            state["language"] = forced_lang or detected
        except Exception:
            state["language"] = forced_lang or "en"
    else:
        state["language"] = forced_lang or "en"

    # Intro case
    if not user_msg:
        reply = "👋 Hello, I’m Kai, Glyns’s Salon’s virtual assistant. I can chat in English 🇬🇧, French 🇫🇷, Spanish 🇪🇸, and most others 🌍. How can I help today?"
        return JSONResponse({
            "reply": reply,
            "suggest": ["Opening hours", "Make a booking", "Contact details"],
            "sidebar": get_sidebar(state),
            "confidence": 1.0,
            "language": state["language"],
            "escalate": False
        })

    # Add message to history
    state["history"].append({"role":"user","content":user_msg})

    # Build messages for GPT
    messages = [
        {"role":"system","content": SYSTEM_PROMPT},
        {"role":"system","content": f"KNOWLEDGE:\n{KNOWLEDGE}"},
        {"role":"system","content": f"CURRENT SLOTS: {json.dumps(state.get('slots',{}))}"},
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

    # --- Branch logic ---
    if action == "CHECK_AVAILABILITY":
        return JSONResponse({
            "reply": reply,
            "suggest": suggestions or ["Today","Tomorrow","This Saturday"],
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "language": state["language"],
            "escalate": should_escalate(reply, confidence)
        })

    if action == "BOOK":
        slots = state["slots"]
        reqd = ["service","date","time","name","contact"]
        if all(slots.get(k) for k in reqd):
            summary = f"{slots['service']} on {slots['date']} at {slots['time']} for {slots['name']}."
            reply = f"✅ Booked: {summary} A confirmation has been sent. Policy: Cancellations must be made 24h in advance."
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
        else:
            missing = [k for k in reqd if not slots.get(k)]
            reply = f"I can book that. I still need: {', '.join(missing)}."
            return JSONResponse({
                "reply": reply,
                "suggest": ["Provide details","Pick a time","Cancel"],
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
            "suggest": ["📞 Call us","📩 Leave your number","✉️ Email us"],
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "language": state["language"],
            "escalate": True
        })

    if action == "ASK":
        return JSONResponse({
            "reply": reply or "Could you share a bit more?",
            "suggest": suggestions,
            "sidebar": get_sidebar(state),
            "confidence": confidence,
            "language": state["language"],
            "escalate": should_escalate(reply, confidence)
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
