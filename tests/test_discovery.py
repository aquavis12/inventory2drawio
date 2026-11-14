"""Discovery: every resource appears once. Resource Explorer first, tagging API fallback."""
import importlib.util, io, json, os, sys, zipfile
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x")
import boto3
from moto import mock_aws
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools/render_drawio"))
from generator import build_drawio
spec = importlib.util.spec_from_file_location("collect", os.path.join(ROOT, "tools/collect_inventory/handler.py"))
collect = importlib.util.module_from_spec(spec); spec.loader.exec_module(collect)
R, ACCT = "us-east-1", "123456789012"

with mock_aws():
    role = boto3.client("iam", R).create_role(RoleName="fn", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    buf = io.BytesIO(); zipfile.ZipFile(buf, "w").writestr("app.py", "def h(e,c): pass")
    fn = boto3.client("lambda", R).create_function(FunctionName="vr-activity-fn", Runtime="python3.12", Role=role,
        Handler="app.h", Code={"ZipFile": buf.getvalue()})["FunctionArn"]
    sm = boto3.client("secretsmanager", R).create_secret(Name="lms-db-password", SecretString="x",
        Tags=[{"Key": "App", "Value": "LMS"}])["ARN"]

    # What Resource Explorer would return for this account (moto doesn't implement it).
    rex_resources = [
        {"Arn": fn, "Service": "lambda", "ResourceType": "lambda:function"},                       # covered: skip
        {"Arn": sm, "Service": "secretsmanager", "ResourceType": "secretsmanager:secret",
         "Properties": [{"Name": "tags", "Data": [{"Key": "App", "Value": "LMS"}]}]},
        {"Arn": f"arn:aws:states:{R}:{ACCT}:stateMachine:lms-grading", "Service": "states", "ResourceType": "states:stateMachine"},
        {"Arn": f"arn:aws:ecs:{R}:{ACCT}:cluster/lms-workers", "Service": "ecs", "ResourceType": "ecs:cluster"},
        {"Arn": f"arn:aws:foo:{R}:{ACCT}:widget/odd-one", "Service": "foo", "ResourceType": "foo:widget"},  # unknown icon
        {"Arn": f"arn:aws:iam::{ACCT}:role/some-role", "Service": "iam", "ResourceType": "iam:role"},        # noise
        {"Arn": f"arn:aws:logs:{R}:{ACCT}:log-group:/aws/lambda/x", "Service": "logs", "ResourceType": "logs:log-group"},
        {"Arn": f"arn:aws:ec2:{R}:{ACCT}:network-interface/eni-1", "Service": "ec2", "ResourceType": "ec2:network-interface"},
        {"Arn": f"arn:aws:ec2:{R}:{ACCT}:transit-gateway/tgw-1", "Service": "ec2", "ResourceType": "ec2:transit-gateway"},
        {"Arn": f"arn:aws:kms:{R}:{ACCT}:key/abcd", "Service": "kms", "ResourceType": "kms:key"},
    ]
    calls = []
    class FakeRex:
        def get_paginator(self, op):
            assert op == "list_resources"
            class P:
                def paginate(self, **kw):
                    calls.append(kw); return [{"Resources": rex_resources}]
            return P()
    real = boto3.session.Session
    class WithRex(real):
        def client(self, name, *a, **k):
            return FakeRex() if name == "resource-explorer-2" else super().client(name, *a, **k)

    collect.boto3.session.Session = WithRex
    out = collect.lambda_handler({"region": R}, None)
    collect.boto3.session.Session = real
    assert calls and calls[0]["Filters"] == {"FilterString": f"region:{R}"}, calls
    others = {n["name"]: n for n in out["nodes"] if n["type"] == "other"}
    print("discovered:", sorted(others), "| note:", [n for n in out["notes"] if "Discovery" in n])
    assert set(others) == {"lms-db-password", "lms-grading", "lms-workers", "odd-one", "tgw-1"}, sorted(others)
    assert sum(1 for n in out["nodes"] if n["id"] == fn) == 1, "covered resource duplicated"
    assert others["lms-db-password"]["tags"] == {"App": "LMS"}
    assert any("5 more resources" in n and "4 routine resources" in n for n in out["notes"]), out["notes"]

    # Tag filter applies to discovered resources too
    collect.boto3.session.Session = WithRex
    scoped = collect.lambda_handler({"region": R, "tag_filter": {"key": "app", "value": "lms"}}, None)
    collect.boto3.session.Session = real
    assert {n["name"] for n in scoped["nodes"] if n["type"] == "other"} == {"lms-db-password"}, scoped["nodes"]

    # include_services brings back a routine service on request
    collect.boto3.session.Session = WithRex
    inc = collect.lambda_handler({"region": R, "include_services": ["kms"]}, None)
    collect.boto3.session.Session = real
    assert any(n["name"] == "abcd" for n in inc["nodes"]), "include_services ignored"

    xml = build_drawio(out, region=R)
    assert "resIcon=mxgraph.aws4.secrets_manager;" in xml and "resIcon=mxgraph.aws4.step_functions;" in xml
    assert "resIcon=mxgraph.aws4.ecs;" in xml and "odd-one" in xml and "without connection details" in xml
    open(os.path.join(ROOT, "samples/discovery.drawio"), "w").write(xml)

    # Fallback: no Resource Explorer -> tagging API (moto implements it); only tagged resources
    fb = collect.lambda_handler({"region": R}, None)
    print("fallback notes:", [n for n in fb["notes"] if "Resource Explorer" in n or "Discovery" in n])
    assert any("tagging API" in n for n in fb["notes"]), fb["notes"]
    assert {n["name"] for n in fb["nodes"] if n["type"] == "other"} == {"lms-db-password"}, \
        [n["name"] for n in fb["nodes"] if n["type"] == "other"]
print("discovery: all tests passed")
