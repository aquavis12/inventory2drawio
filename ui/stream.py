"""Parse the event stream AgentCore Runtime returns (server-sent events)."""
import json
import re

DRAWIO_LINK = re.compile(r"https://[^\s)\]>\"']+\.drawio[^\s)\]>\"']*")


def parse_events(lines):
    """Yield {"type", "data"} dicts from raw SSE lines (bytes or str)."""
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            event = body
        if isinstance(event, dict) and "type" in event:
            yield event
        elif isinstance(event, str) and event:   # plain-text chunk
            yield {"type": "text", "data": event}


PRESIGNED = re.compile(r"https://\S+?amazonaws\.com/\S*?[?&](X-Amz-Signature|Signature)=\S+")


def strip_presigned_links(text):
    """If the model still pastes a signed S3 link, hide it: copied links are often
    corrupted, and they carry temporary credentials."""
    return PRESIGNED.sub("(use the download button below)", text or "")


def parse_s3_uri(uri):
    m = re.match(r"^s3://([^/]+)/(diagrams/[^\s]+\.drawio)$", uri or "")
    return (m.group(1), m.group(2)) if m else None


def find_download_link(text):
    match = DRAWIO_LINK.search(text or "")
    return match.group(0).rstrip(".,") if match else None


def newest_diagrams(s3, bucket, customer_id, since, limit=1):
    """Diagrams written to s3://bucket/diagrams/<customer>/ at or after `since` (a timezone-
    aware datetime). Fallback when the agent didn't hand over the diagram location."""
    prefix = f"diagrams/{customer_id or 'own-account'}/"
    found = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".drawio") and obj["LastModified"] >= since:
                found.append(obj)
    found.sort(key=lambda o: o["LastModified"], reverse=True)
    return [o["Key"] for o in found[:limit]]
