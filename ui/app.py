"""Streamlit UI for the inventory-to-diagram agent.

  pip install streamlit boto3
  export AGENT_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:<account>:runtime/<id>
  streamlit run ui/app.py

Uses your local AWS credentials; they need bedrock-agentcore:InvokeAgentRuntime and
s3:GetObject on <work bucket>/diagrams/* and s3:ListBucket on the bucket (the UI downloads
the diagram itself).
"""
import json
import os
import uuid

import boto3
import streamlit as st
from botocore.config import Config

from customers import list_customers
from templates import TEMPLATES, fill
import datetime

from stream import newest_diagrams, parse_events, parse_s3_uri, strip_presigned_links

TOOL_LABELS = {
    "collect_inventory": "Reading the account (read-only)",
    "render_drawio": "Laying out the diagram",
}

st.set_page_config(page_title="AWS account to draw.io", page_icon="🗺️", layout="centered")
st.title("AWS account to draw.io")
st.caption("Draws an editable architecture diagram from what is actually running. Read-only.")

with st.sidebar:
    st.header("Connection")
    runtime_arn = st.text_input("Agent runtime ARN", os.environ.get("AGENT_RUNTIME_ARN", ""))
    api_region = st.text_input("Agent's Region", os.environ.get("AWS_REGION", "us-east-1"))
    work_bucket = st.text_input("Work bucket", os.environ.get("WORK_BUCKET", ""),
                                help="Customers are read from its customers/ folder.")

    @st.cache_data(ttl=300, show_spinner=False)
    def _customers(bucket, region):
        return list_customers(boto3.client("s3", region_name=region), bucket)

    st.header("What to draw")
    customers = _customers(work_bucket.strip(), api_region.strip())
    customer = st.selectbox("Customer", customers, format_func=lambda c: c["name"])
    if customer["allowed_regions"]:
        target_region = st.selectbox("Region to read", customer["allowed_regions"])
    else:
        target_region = st.text_input("Region to read", "us-east-1")
    tag_key = st.text_input("Tag key (optional)", placeholder="App")
    tag_value = st.text_input("Tag value (optional)", placeholder="orders")
    resource_group = st.text_input("Resource group (optional)", placeholder="lms-study-site")
    vpc_ids = st.text_input("VPC IDs (optional, comma-separated)", placeholder="vpc-0abc123")
    st.header("Flow description")
    st.caption("Optional. Describe how requests or data move, in plain words. Connections "
               "AWS can't show (a website calling an API, a function emailing users) are "
               "drawn as dashed lines.")
    uploaded = st.file_uploader("Upload .txt or .md", type=["txt", "md"])
    if uploaded is not None:
        try:
            st.session_state["flow_notes"] = uploaded.getvalue().decode("utf-8")[:8000]
            st.session_state["flow_name"] = uploaded.name
        except UnicodeDecodeError:
            st.error("That file isn't plain text. Save it as .txt or .md and try again.")
    pasted = st.text_area("...or paste it here", value="", height=120,
                          placeholder="1. Users open the site through CloudFront.\n"
                                      "2. The site calls vr-activity-api.\n"
                                      "3. vr-activity-fn saves results in vr-submissions.")
    if pasted.strip():
        st.session_state["flow_notes"] = pasted.strip()[:8000]
        st.session_state["flow_name"] = "pasted text"
    if st.session_state.get("flow_notes"):
        st.caption(f"Using: {st.session_state['flow_name']} "
                   f"({len(st.session_state['flow_notes'])} characters)")
        if st.button("Clear flow description", use_container_width=True):
            st.session_state.pop("flow_notes", None)
            st.session_state.pop("flow_name", None)
            st.rerun()
    if st.button("New conversation", use_container_width=True):
        st.session_state.clear()
        st.rerun()

# Runtime keeps one isolated session per ID (must be at least 33 characters).
st.session_state.setdefault("session_id", f"ui-{uuid.uuid4()}-{uuid.uuid4().hex[:8]}")
st.session_state.setdefault("messages", [])

def show_downloads(files, key_prefix):
    for i, f in enumerate(files):
        st.download_button(f"Download {f['name']}", data=f["data"], file_name=f["name"],
                           mime="application/xml", key=f"{key_prefix}-{i}")
    if files:
        st.caption("Open the file at app.diagrams.net or in draw.io desktop.")


for n, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        show_downloads(msg.get("files", []), f"hist-{n}")


def scope_text():
    parts = []
    if tag_key.strip():
        parts.append(f"Tag filter: {tag_key.strip()}={tag_value.strip() or '(any value)'}")
    if resource_group.strip():
        parts.append(f"Resource group: {resource_group.strip()}")
    if vpc_ids.strip():
        parts.append(f"VPC IDs: {vpc_ids.strip()}")
    return "\n".join(parts) or "No tag, resource group or VPC filter"


# Prompt templates: one click fills a well-phrased request from the sidebar settings.
with st.container():
    choice = st.pills("Start from a template", [t["label"] for t in TEMPLATES],
                      selection_mode="single", key=f"tpl-{len(st.session_state.messages)}")
    if choice:
        tpl = next(t for t in TEMPLATES if t["label"] == choice)
        text, why_not = fill(tpl, bool(st.session_state.get("flow_notes")),
                             tag_key=tag_key, tag_value=tag_value,
                             resource_group=resource_group, vpc_ids=vpc_ids)
        if why_not:
            st.warning(why_not)
        else:
            st.caption(f"Prompt: {text}")
            if st.button("Send this prompt", type="primary"):
                st.session_state["pending_prompt"] = text
                st.rerun()

prompt = st.chat_input("Draw the orders application") or st.session_state.pop("pending_prompt", None)
if not st.session_state.messages and not prompt:
    st.info("Set the Region and an optional tag in the sidebar, then say what to draw. "
            "Try: **Draw this application** or **Draw everything in this Region**.")

if prompt:
    if not runtime_arn.strip():
        st.error("Add the agent runtime ARN in the sidebar first.")
        st.stop()
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("Thinking", expanded=False)
        answer_box = st.empty()
        answer, failed, files = "", None, []
        started = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5)
        try:
            client = boto3.client("bedrock-agentcore", region_name=api_region.strip(),
                                  config=Config(read_timeout=300, retries={"max_attempts": 2}))
            response = client.invoke_agent_runtime(
                agentRuntimeArn=runtime_arn.strip(),
                runtimeSessionId=st.session_state.session_id,
                contentType="application/json",
                accept="text/event-stream",
                payload=json.dumps({
                    "prompt": f"{prompt}\n\n[Scope from the UI]\n{scope_text()}",
                    "customer_id": customer["id"],          # structured, not free text
                    "region": target_region.strip(),
                    "flow_notes": st.session_state.get("flow_notes", ""),
                }).encode(),
            )
            for event in parse_events(response["response"].iter_lines()):
                if event["type"] == "tool":
                    label = TOOL_LABELS.get(event["data"], event["data"])
                    status.update(label=label)
                    status.write(label)
                elif event["type"] == "text":
                    answer += event["data"]
                    answer_box.markdown(strip_presigned_links(answer) + "▌")
                elif event["type"] == "diagram":
                    loc = parse_s3_uri((event.get("data") or {}).get("drawio_s3_uri"))
                    if loc:
                        try:  # fetched with your own credentials: no link, no expiry
                            body = boto3.client("s3", region_name=api_region.strip()).get_object(
                                Bucket=loc[0], Key=loc[1])["Body"].read()
                            files.append({"name": loc[1].rsplit("/", 1)[-1], "data": body})
                        except Exception as err:
                            st.warning(f"Couldn't fetch the diagram: {type(err).__name__}: {err}")
                elif event["type"] == "error":
                    failed = event["data"]
        except Exception as err:  # credentials, permissions, wrong ARN, timeouts
            failed = f"{type(err).__name__}: {err}"

        if failed:
            status.update(label="Something went wrong", state="error")
            st.error(failed)
            answer = answer or "I couldn't finish that request."
        else:
            status.update(label="Done", state="complete")
        if not files and not failed and work_bucket.strip():
            # The agent didn't hand over the diagram (older agent code, or the model
            # skipped it): pick up the diagram this request wrote, straight from S3.
            try:
                s3 = boto3.client("s3", region_name=api_region.strip())
                for key in newest_diagrams(s3, work_bucket.strip(), customer["id"], started):
                    body = s3.get_object(Bucket=work_bucket.strip(), Key=key)["Body"].read()
                    files.append({"name": key.rsplit("/", 1)[-1], "data": body})
            except Exception as err:
                st.warning(f"Couldn't fetch the diagram from S3: {type(err).__name__}: {err}")
        answer = strip_presigned_links(answer)
        answer_box.markdown(answer)
        show_downloads(files, f"new-{len(st.session_state.messages)}")
        st.session_state.messages.append({"role": "assistant", "content": answer, "files": files})
