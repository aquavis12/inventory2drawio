"""only_services scoping, code-computed findings, and the 'connected, not in scope' box.
No AWS or moto needed: these call the pure functions directly."""
import importlib.util, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


c = load("tools/collect_inventory/handler.py", "collector")
g = load("tools/render_drawio/generator.py", "generator")

nodes = {
    "vpc-1": {"id": "vpc-1", "type": "vpc", "name": "v", "tags": {}},
    "sn-1": {"id": "sn-1", "type": "subnet", "name": "public-1", "vpc": "vpc-1", "public": False, "tags": {}},
    "sn-2": {"id": "sn-2", "type": "subnet", "name": "app-1", "vpc": "vpc-1", "public": False, "tags": {}},
    "s3:a": {"id": "s3:a", "type": "s3_bucket", "name": "a", "tags": {}},
    "fn": {"id": "fn", "type": "lambda_function", "name": "fn", "vpc": "vpc-1", "subnet": "sn-2", "tags": {}},
    "db": {"id": "db", "type": "rds_instance", "name": "db", "multi_az": False, "vpc": "vpc-1", "subnet": "sn-1", "tags": {}},
    "k": {"id": "k", "type": "other", "service": "kms", "name": "k"},
}
edges = [{"from": "fn", "to": "s3:a", "kind": "uses"}]


def ids(only):
    out = c._finish(dict(nodes), list(edges), [], None, None, only_services=only)
    return sorted(n["id"] for n in out["nodes"])


# S3 only: the original bug was getting the whole account back.
assert ids(["s3"]) == ["s3:a"]
assert ids(["s3", "lambda"]) == ["fn", "s3:a"]
assert ids(["kms"]) == ["k"]                    # services without a dedicated collector
assert len(ids(None)) == len(nodes)

# Findings come from code and name real resources.
full = c._finish(dict(nodes), list(edges), [], None, None)["findings"]
text = " ".join(f["text"] for f in full)
assert "one Availability Zone" in text and "named public" in text, text
assert "no NAT gateway" in text
# A scoped view must not claim 'no connections' or 'no NAT': neighbours may be hidden.
scoped = c._finish(dict(nodes), list(edges), [], None, None, only_services=["lambda"])["findings"]
assert not [f for f in scoped if "no connections" in f["text"] or "NAT" in f["text"]], scoped

# Long names wrap instead of ending in '...'.
assert g._wrap_name("example-site-bucket-123456789012") == "example-site-bucket-<br>123456789012"
assert "..." not in g._wrap_name("example-sessions-bucket")

# Untagged connected resources get their own box, apart from the resource group.
graph = {"nodes": [{"id": "f", "type": "lambda_function", "name": "f", "group": "rg"},
                   {"id": "b", "type": "s3_bucket", "name": "b", "included_because": "connected to a match"}],
         "edges": [{"from": "f", "to": "b", "kind": "uses"}]}
xml = g.build_drawio(graph)
assert "Connected, not in scope" in xml and "Resource group: rg" in xml
print("scope and findings: all tests passed")
