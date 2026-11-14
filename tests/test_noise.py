"""Region-wide discovery keeps architecture and drops plumbing (from a real account's output)."""
import importlib.util, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("c", os.path.join(ROOT, "tools/collect_inventory/handler.py"))
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
R, A = "us-east-1", "123456789012"
found = [  # (arn, service, rtype, tags, name) as discovery sees them
    (f"arn:aws:amplify:{R}:{A}:apps/d1xq5cfc3oshm", "amplify", "apps", {}, None),
    (f"arn:aws:amplify:{R}:{A}:apps/d1xq5cfc3oshm/branches/main", "amplify", "apps", {}, None),
    (f"arn:aws:amplify:{R}:{A}:apps/d1xq5cfc3oshm/domains/example.com", "amplify", "apps/domains", {}, None),
    (f"arn:aws:apprunner:{R}:{A}:autoscalingconfiguration/DefaultConfiguration/1/0000000000", "apprunner", "autoscalingconfiguration", {}, None),
    (f"arn:aws:ecs:{R}:{A}:task-definition/worker:1", "ecs", "task-definition", {}, None),
    (f"arn:aws:ecs:{R}:{A}:task-definition/worker:2", "ecs", "task-definition", {}, None),
    (f"arn:aws:ecs:{R}:{A}:cluster/lms-workers", "ecs", "cluster", {}, None),
    (f"arn:aws:events:{R}:{A}:rule/AutoScalingManagedRule", "events", "rule", {}, None),
    (f"arn:aws:events:{R}:{A}:rule/AWSUserNotificationsManagedRule-1", "events", "rule", {}, None),
    (f"arn:aws:events:{R}:{A}:rule/nightly-report", "events", "rule", {}, None),
    (f"arn:aws:backup:{R}:{A}:backup-plan:73d922fb-9312", "backup", "backup-plan", {}, None),
    (f"arn:aws:backup:{R}:{A}:backup-vault:automatic-backup-vault", "backup", "backup-vault", {}, None),
    (f"arn:aws:resiliencehub:{R}:{A}:app/031a6afc-7f52", "resiliencehub", "app", {}, None),
    (f"arn:aws:cloudformation:{R}:{A}:stack/24ebc050/abc", "cloudformation", "stack", {}, None),
    (f"arn:aws:states:{R}:{A}:stateMachine:lms-grading", "states", "stateMachine", {}, None),
    (f"arn:aws:wafv2:{R}:{A}:regional/webacl/site-acl/123", "wafv2", "regional/webacl", {}, "site-acl"),
]
class Fake:
    def client(self, name, **k):
        if name == "resource-explorer-2":
            class P:
                def get_paginator(self, op):
                    class Q:
                        def paginate(self, **kw):
                            return [{"Resources": [{"Arn": a, "Service": s, "ResourceType": f"{s}:{t}",
                                                    "Properties": []} for a, s, t, _, _ in found]}]
                    return Q()
            return P()
        raise RuntimeError("unused")
nodes, notes = {}, []
c.discover(Fake(), R, nodes, notes)
kept = sorted(n["name"] for n in nodes.values())
print("kept:", kept); print(notes[-1])
assert kept == ["automatic-backup-vault", "d1xq5cfc3oshm", "lms-grading", "lms-workers", "nightly-report", "site-acl"], kept
nodes, notes = {}, []
c.discover(Fake(), R, nodes, notes, include_services=["ecs", "events"])
assert {"worker:1", "worker:2", "AutoScalingManagedRule"} <= {n["name"] for n in nodes.values()}, \
    sorted(n["name"] for n in nodes.values())
print("noise: all tests passed")
