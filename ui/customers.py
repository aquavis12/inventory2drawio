"""Customer list for the UI, read from the work bucket's customers/ prefix."""
import json

SANDBOX = {"id": None, "name": "This account (sandbox)", "allowed_regions": []}


def list_customers(s3, bucket):
    """Returns [{id, name, allowed_regions}], sandbox entry last. Never raises."""
    found = []
    if bucket:
        try:
            pages = s3.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix="customers/", Delimiter="/")
            for page in pages:
                for prefix in page.get("CommonPrefixes", []):
                    cid = prefix["Prefix"].split("/")[1]
                    try:
                        body = s3.get_object(Bucket=bucket, Key=f"customers/{cid}/profile.json")["Body"].read()
                        prof = json.loads(body)
                        found.append({"id": cid, "name": prof.get("name") or cid,
                                      "allowed_regions": list(prof.get("allowed_regions") or [])})
                    except Exception:
                        continue    # folder without a readable profile: not selectable
        except Exception:
            pass
    return sorted(found, key=lambda c: c["name"].lower()) + [SANDBOX]
