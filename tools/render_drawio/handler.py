"""render_drawio: AgentCore Gateway Lambda tool.

Reads a graph saved by collect_inventory, optionally hides nodes the agent judged
irrelevant, lays it out and writes the .drawio file to S3. Returns a download link.
"""
import json
import os
import re

import boto3

from generator import build_drawio

URI = re.compile(r"^s3://([^/]+)/(inventory/[^\s]+\.json)$")


def lambda_handler(event, context):
    bucket = os.environ["WORK_BUCKET"]
    match = URI.match(event.get("graph_s3_uri") or "")
    if not match or match[1] != bucket:  # only graphs this system wrote
        return {"ok": False, "error": f"graph_s3_uri must be s3://{bucket}/inventory/....json"}
    s3 = boto3.client("s3")
    graph = json.loads(s3.get_object(Bucket=bucket, Key=match[2])["Body"].read())

    hide = set(event.get("hide_ids") or [])
    auto_hidden = []
    if event.get("hide_empty_default_vpc", True):
        network = {"vpc", "subnet", "internet_gateway", "nat_gateway"}
        for vpc in [n for n in graph["nodes"] if n["type"] == "vpc" and n.get("is_default")]:
            if not any(n.get("vpc") == vpc["id"] and n["type"] not in network for n in graph["nodes"]):
                ids = {vpc["id"]} | {n["id"] for n in graph["nodes"] if n.get("vpc") == vpc["id"]}
                hide |= ids
                auto_hidden.append(f"{vpc['name']} (empty default VPC)")
    if hide:
        graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in hide]
        graph["edges"] = [e for e in graph["edges"] if e["from"] not in hide and e["to"] not in hide]

    # Connections from the customer's flow description: only between resources that exist,
    # so a described step can never add a box. Drawn dashed and labelled.
    known = {n["id"] for n in graph["nodes"]}
    existing = {(e["from"], e["to"]) for e in graph["edges"]}
    added, rejected = 0, []
    for c in (event.get("connections") or [])[:50]:
        src, dst = str(c.get("from", "")), str(c.get("to", ""))
        if src not in known or dst not in known or src == dst:
            rejected.append({"from": src, "to": dst, "reason": "not in the drawn inventory"})
            continue
        if (src, dst) in existing:
            continue
        label = re.sub(r"[^\w .,:/()+-]", "", str(c.get("label") or ""))[:40]
        graph["edges"].append({"from": src, "to": dst, "kind": "described", "label": label})
        existing.add((src, dst))
        added += 1

    customer = (graph.get("customer") or {}).get("name")
    title = (event.get("title") or f"{customer or 'AWS'} architecture")[:80]
    xml = build_drawio(graph, title=title, region=graph.get("region", "Region"))
    key = "diagrams/" + match[2][len("inventory/"):].replace(".json", ".drawio")
    s3.put_object(Bucket=bucket, Key=key, Body=xml.encode("utf-8"), ContentType="application/xml")
    url = s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                    ExpiresIn=3600)
    shown = {n["type"] for n in graph["nodes"]}
    return {"ok": True, "drawio_s3_uri": f"s3://{bucket}/{key}", "download_url": url,
            "expires_in_seconds": 3600, "resources_drawn": len(graph["nodes"]),
            "connections_drawn": len(graph["edges"]), "described_connections_drawn": added,
            "described_connections_rejected": rejected, "hidden_automatically": auto_hidden,
            "types": sorted(shown)}
