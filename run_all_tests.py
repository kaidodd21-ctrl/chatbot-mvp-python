import requests, json, re, datetime

BASE = "http://127.0.0.1:8000"

def jprint(x):
    print(json.dumps(x, indent=2))

def call_chat(message, sid=None):
    payload = {"message": message}
    if sid: payload["session_id"] = sid
    r = requests.post(f"{BASE}/chat", json=payload, timeout=8)
    r.raise_for_status()
    return r.json()

def get_slots(resp):
    return (resp.get("debug") or {}).get("slots") or {}

def dt_pretty(slots):
    dt = slots.get("datetime")
    if isinstance(dt, dict):  # new format
        return dt.get("pretty")
    return dt  # old fallback (string)

print("==========================================")
print("   Kai Chatbot - Unified Test Runner")
print("==========================================\n")

# Health
print("=== Health Check ===")
h = requests.get(f"{BASE}/health", timeout=5).json()
print("✅ Health check\n", h, "\n")

# Happy path
print("=== Happy Path Booking ===")
resp1 = call_chat("I want a haircut")
jprint(resp1)
sid = resp1["session_id"]
print("Slots:", get_slots(resp1))

resp2 = call_chat("Tomorrow at 1pm", sid)
jprint(resp2)
print("Slots:", get_slots(resp2))

resp3 = call_chat("My name is Kai", sid)
jprint(resp3)
print("Slots:", get_slots(resp3))

resp4 = call_chat("07123456789", sid)
jprint(resp4)
print("Slots:", get_slots(resp4))

ok_happy = "Booked" in resp4.get("reply", "")

if ok_happy:
    print("✅ Booking flow complete\n")
else:
    print("⚠️ Booking flow failed\n")

# Validation tests
print("=== Validation Tests ===\n")

print("-- Invalid Service --")
respA = call_chat("I want a spaceship")
jprint(respA)
print("Slots:", get_slots(respA))
# We just ensure it didn't crash; smalltalk may answer casually

print("\n-- Past Datetime --")
sid2 = call_chat("I want a haircut")["session_id"]
past = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d 10:00")
respB = call_chat(past, sid2)
jprint(respB)
print("Slots:", get_slots(respB))

print("\n-- Invalid Contact --")
sid3 = call_chat("I want a haircut")["session_id"]
call_chat("Tomorrow 3pm", sid3)
call_chat("My name is Kai", sid3)
respC = call_chat("abc", sid3)
jprint(respC)
print("Slots:", get_slots(respC))

# Summary
passed = 1 if ok_happy else 0
failed = 1 - passed
print("\n==========================================")
print("   Test Summary")
print("==========================================")
print(f"✅ Passed: {passed}")
print(f"⚠️ Failed: {failed}")
print("==========================================")
print("   ✅ All tests complete")
print("==========================================")
