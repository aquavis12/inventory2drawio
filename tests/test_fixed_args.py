"""The app, not the model, decides customer and Region for inventory calls."""
import asyncio, json, os, sys
os.environ.update(GATEWAY_URL="https://x/mcp", MODEL_ID="nvidia.nemotron-super-3-120b", AWS_REGION="us-east-1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
import agent as a
from mcp.types import Tool

schema = json.load(open(os.path.join(ROOT, "tools/collect_inventory/tool_schema.json")))[0]
sent = []
class FakeClient:
    async def call_tool_async(self, **kw):
        sent.append(kw["arguments"])
        return {"toolUseId": kw["tool_use_id"], "status": "success", "content": [{"text": "{\"ok\": true}"}]}

collect = a.ModelFriendlyTool(Tool(name="collect-inventory___collect_inventory", description=schema["description"],
                                   inputSchema=schema["inputSchema"]), FakeClient(), name_override="collect_inventory")
render = a.ModelFriendlyTool(Tool(name="render-drawio___render_drawio", description="d",
                                  inputSchema={"type": "object", "properties": {"customer_id": {"type": "string"}}}),
                             FakeClient(), name_override="render_drawio")

props = collect.tool_spec["inputSchema"]["json"]["properties"]
assert "customer_id" not in props and "region" not in props, props
assert "required" not in collect.tool_spec["inputSchema"]["json"], "region must not be required from the model"
assert {"tag_filter", "resource_group", "vpc_ids", "include_services"} <= set(props)

async def call(tool, args, fixed):
    return [e async for e in tool.stream({"toolUseId": "t", "input": args}, {"fixed_args": fixed})]

# sandbox: no customer_id sent at all, Region from the UI
asyncio.run(call(collect, {"resource_group": "lms"}, {"customer_id": None, "region": "us-east-1"}))
assert sent[-1] == {"resource_group": "lms", "region": "us-east-1"}, sent[-1]
# customer selected: injected
asyncio.run(call(collect, {}, {"customer_id": "acme", "region": "me-central-1"}))
assert sent[-1] == {"customer_id": "acme", "region": "me-central-1"}, sent[-1]
# the model tries to switch customer and Region: overridden
asyncio.run(call(collect, {"customer_id": "globex", "region": "eu-west-1", "vpc_ids": ["vpc-1"]},
                 {"customer_id": "acme", "region": "me-central-1"}))
assert sent[-1] == {"vpc_ids": ["vpc-1"], "customer_id": "acme", "region": "me-central-1"}, sent[-1]
# other tools are untouched
asyncio.run(call(render, {"graph_s3_uri": "s3://b/inventory/x.json"}, {"customer_id": "acme", "region": "x"}))
assert sent[-1] == {"graph_s3_uri": "s3://b/inventory/x.json"}, sent[-1]

# described links are dropped unless this request carries a flow file
asyncio.run(call(render, {"graph_s3_uri": "s3://b/inventory/x.json",
                          "connections": [{"from": "a", "to": "b"}]}, {"has_flow": False}))
assert sent[-1] == {"graph_s3_uri": "s3://b/inventory/x.json"}, sent[-1]
async def call_state(tool, args, state):
    return [e async for e in tool.stream({"toolUseId": "t", "input": args}, state)]
asyncio.run(call_state(render, {"graph_s3_uri": "s3://b/inventory/x.json",
                                "connections": [{"from": "a", "to": "b"}]}, {"has_flow": True}))
assert sent[-1]["connections"] == [{"from": "a", "to": "b"}], sent[-1]

# the entrypoint passes the UI selection through, and refuses without a Region
seen, flows = [], []
class FakeAgent:
    async def stream_async(self, prompt, invocation_state=None):
        seen.append(invocation_state["fixed_args"]); flows.append(invocation_state["has_flow"])
        yield {"data": "Your diagram is ready."}
a._tools = ["t"]; a._agents[(None, None)] = FakeAgent(); a._agents[("acme", None)] = FakeAgent()
async def go(p): return [e async for e in a.invoke(p)]
asyncio.run(go({"prompt": "draw", "region": "us-east-1"}))
asyncio.run(go({"prompt": "draw", "region": "us-east-1", "customer_id": "acme"}))
assert seen == [{"customer_id": None, "region": "us-east-1"}, {"customer_id": "acme", "region": "us-east-1"}], seen
assert flows == [False, False], flows
asyncio.run(go({"prompt": "draw", "region": "us-east-1", "flow_notes": "1. site calls api"}))
assert flows[-1] is True
assert asyncio.run(go({"prompt": "draw"}))[0] == {"type": "error", "data": "Pick a Region in the sidebar first."}
assert "you never pass them" in a.SYSTEM_PROMPT and "omit it when" not in a.SYSTEM_PROMPT
print("fixed args: all tests passed")
