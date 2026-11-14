import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui"))
from templates import TEMPLATES, fill, scope_phrase
assert scope_phrase(resource_group="lms-study-site") == " in resource group lms-study-site"
assert scope_phrase(tag_key="app", tag_value="lms") == " tagged app=lms"
assert scope_phrase() == " in this Region"
flow = next(t for t in TEMPLATES if t["needs"] == "flow")
assert fill(flow, False)[0] is None
for t in TEMPLATES:
    out, _ = fill(t, True, tag_key="app", tag_value="lms")
    assert "{" not in out and len(out) < 400, out
print("templates: all tests passed")
tc = next(t for t in TEMPLATES if t["label"] == "Tagged + connected")
assert fill(tc, False)[0] is None and "tag or a resource group" in fill(tc, False)[1]
assert fill(tc, False, tag_key="app", tag_value="lms")[0].startswith("Draw the application tagged app=lms")
assert fill(tc, False, resource_group="g")[0]
print("templates: scope check passed")
