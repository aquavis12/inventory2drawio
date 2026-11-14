"""Greetings must not reach the tool-using agent; drawing requests must."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))
from intent import SectionCutter, user_words, wants_diagram

TRAILER = "\n\n[Scope from the UI]\ntag app=lms, resource group vishnu-lms-study-site, in this Region"

for msg in ["HI", "hi", "Hello there", "thanks!", "good morning", "what can you do?", "ok", "who are you"]:
    assert not wants_diagram(msg), msg
    assert not wants_diagram(msg + TRAILER), "UI scope text must not count as intent: " + msg

for msg in ["Draw the orders application", "draw only my S3 buckets", "Show me the network",
            "Draw everything in this Region.", "hide the ACM certificate", "what is my preference?",
            "Draw the application tagged app=lms and include connected resources",
            "Review risks", "redo it", "update the diagram", "Draw this application"]:
    assert wants_diagram(msg + TRAILER), msg

assert user_words("HI" + TRAILER) == "HI"


def stream(chunks, active):
    c, out = SectionCutter(), ""
    for ch in chunks:
        out += c.feed(ch, active)
    return out + c.flush(active)


# No findings: the closing section is dropped, even when the marker is split across chunks.
body = "Your diagram is ready.\n\nThe API calls the function."
assert stream([body, "\n\nWorth a look: (none)"], True).strip() == body
assert stream([body + "\n\nWor", "th a lo", "ok:\n- nothing"], True).strip() == body
assert stream([body, "\n\n**Worth a look:** none"], True).strip() == body
# Findings present, or not known yet: everything is kept.
full = body + "\n\nWorth a look:\n- Database db runs in one Availability Zone."
assert stream([full[:40], full[40:]], False) == full
assert stream([full[:40], full[40:]], None) == full
assert stream([body], True) == body          # nothing to cut: all text still arrives
print("intent: all tests passed")
