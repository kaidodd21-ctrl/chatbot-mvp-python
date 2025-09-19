import os, json, uuid
from typing import Dict, Tuple, List

STORE_FILE = "sessions.json"

# In-memory structure
sessions: Dict[str, Dict] = {}

def _default_session(sid: str, slot_order: List[str]) -> Dict:
    return {
        "id": sid,
        "last_intent": None,
        "slots": {k: None for k in slot_order},
        "bookings": []
    }

def load_sessions() -> None:
    global sessions
    try:
        if os.path.exists(STORE_FILE):
            with open(STORE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Back-compat guard
                if isinstance(data, dict):
                    sessions = data
                else:
                    sessions = {}
        else:
            sessions = {}
    except Exception:
        sessions = {}

def save_sessions() -> None:
    try:
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # best effort only

def get_session(sid: str, slot_order: List[str]) -> Tuple[str, Dict]:
    """
    Returns (session_id, session_dict). Creates a new session if not found.
    Ensures required slots exist.
    """
    global sessions
    if not sid or sid not in sessions:
        sid = str(uuid.uuid4())
        sessions[sid] = _default_session(sid, slot_order)
        save_sessions()
        return sid, sessions[sid]

    # Ensure slot keys exist (in case slot_order changed)
    sess = sessions[sid]
    if "slots" not in sess or not isinstance(sess["slots"], dict):
        sess["slots"] = {}
    for k in slot_order:
        sess["slots"].setdefault(k, None)
    if "last_intent" not in sess:
        sess["last_intent"] = None
    if "bookings" not in sess:
        sess["bookings"] = []

    save_sessions()
    return sid, sess
