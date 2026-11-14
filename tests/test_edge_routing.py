"""Edges are routed around icons. The check below is independent of the router: it takes the
points written into the .drawio file and tests every segment against every other icon."""
import importlib.util, json, os, re, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("g", os.path.join(ROOT, "tools/render_drawio/generator.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

C = g.ICON / 2


def blocked_area(x, y):          # icon plus its label, the same area the router avoids
    return (x + C - g.HALF_W, y - 2, x + C + g.HALF_W, y + g.ICON + g.LABEL_H)


def crosses(p, q, rect):
    (x0, y0, x1, y1) = rect
    (ax, ay), (bx, by) = p, q
    if ay == by:
        return y0 < ay < y1 and max(ax, bx) > x0 and min(ax, bx) < x1
    if ax == bx:
        return x0 < ax < x1 and max(ay, by) > y0 and min(ay, by) < y1
    raise AssertionError(f"diagonal segment {p} -> {q}")


def check(icons, pairs):
    routes = g._route_edges(icons, pairs)
    for (a, b), pts in routes.items():
        ax, ay = icons[a][0] + C, icons[a][1] + C
        bx, by = icons[b][0] + C, icons[b][1] + C
        line = [(ax, ay)] + pts + [(bx, by)]
        for p, q in zip(line, line[1:]):
            for k, (x, y) in icons.items():
                if k in (a, b):
                    continue
                assert not crosses(p, q, blocked_area(x, y)), (a, b, k, p, q)
    return routes


# A row of five with a long edge from the first to the last: it must go around the middle three.
icons = {f"n{i}": (i * 230, 0) for i in range(5)}
r = check(icons, [("n0", "n4"), ("n0", "n1"), ("n1", "n2")])
assert len(r) == 3 and r[("n0", "n4")], "long edge must bend around the icons between"
assert r[("n0", "n1")] == [], "neighbours connect with a straight line"

# Same column, one below the other, and an edge pointing back to an earlier column.
icons = {"a": (0, 0), "b": (0, 120), "c": (230, 0), "d": (460, 120)}
r = check(icons, [("a", "b"), ("d", "a"), ("c", "d")])
assert len(r) == 3, r

# A whole diagram: a small serverless app, rendered end to end, every routed line checked.
def fn(i, name, kind, **kw): return {"id": i, "type": kind, "name": name, **kw}
graph = {"nodes": [
    fn("cf", "site", "cloudfront_distribution", is_global=True),
    fn("api", "api", "api_gateway"), fn("lam", "worker", "lambda_function"),
    fn("t1", "orders", "dynamodb_table"), fn("t2", "quotes", "dynamodb_table"),
    fn("t3", "audit", "dynamodb_table"), fn("sns", "events", "sns_topic"),
    fn("q", "jobs", "sqs_queue"), fn("b", "assets", "s3_bucket")],
    "edges": [{"from": "cf", "to": "b", "kind": "origin"}, {"from": "api", "to": "lam", "kind": "invokes"},
              {"from": "lam", "to": "t1", "kind": "uses"}, {"from": "lam", "to": "t2", "kind": "uses"},
              {"from": "lam", "to": "t3", "kind": "uses"}, {"from": "lam", "to": "sns", "kind": "uses"},
              {"from": "q", "to": "lam", "kind": "triggers"}, {"from": "sns", "to": "t3", "kind": "uses"}]}
captured = []
original = g._route_edges
g._route_edges = lambda icons, pairs: (captured.append((icons, pairs)), original(icons, pairs))[1]
start = time.time()
xml = g.build_drawio(graph)
assert time.time() - start < 10, "routing too slow"
icons, pairs = captured[0]
routed = len(check(icons, pairs))      # straight lines have no bend points in the XML
edges = xml.count('edge="1"')
assert edges == len(graph["edges"]) and routed == edges, (routed, edges)
print(f"edge routing: all tests passed ({routed} of {edges} edges routed clear of icons)")
