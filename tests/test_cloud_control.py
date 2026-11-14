"""Discovery when Resource Explorer is off (the usual case): Cloud Control + tagging API."""
import importlib.util, json, os, sys
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x")
import boto3
from botocore.exceptions import ClientError
from moto import mock_aws
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("collect", os.path.join(ROOT, "tools/collect_inventory/handler.py"))
collect = importlib.util.module_from_spec(spec); spec.loader.exec_module(collect)
R, ACCT = "us-east-1", "123456789012"

def err(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "Op")

with mock_aws():
    secret = boto3.client("secretsmanager", R).create_secret(Name="lms-db-password", SecretString="x",
        Tags=[{"Key": "App", "Value": "LMS"}])["ARN"]
    sm_arn = f"arn:aws:states:{R}:{ACCT}:stateMachine:lms-grading"
    cc_data = {   # what Cloud Control returns: often identifiers only, sometimes an Arn
        "AWS::SecretsManager::Secret": [{"Identifier": secret, "Properties": json.dumps({"Id": secret})}],
        "AWS::StepFunctions::StateMachine": [{"Identifier": sm_arn, "Properties": json.dumps({"Arn": sm_arn})}],
        "AWS::ECS::Cluster": [{"Identifier": "lms-workers", "Properties": json.dumps(
            {"ClusterName": "lms-workers", "Arn": f"arn:aws:ecs:{R}:{ACCT}:cluster/lms-workers"})}],
        "AWS::RDS::DBCluster": [{"Identifier": "lms-aurora", "Properties": json.dumps({"DBClusterIdentifier": "lms-aurora"})}],
        "AWS::Events::Rule": [{"Identifier": "nightly-report", "Properties": "{}"}],
    }
    calls = []
    class FakeCC:
        def get_paginator(self, op):
            class P:
                def paginate(self, TypeName, **kw):
                    calls.append((TypeName, kw.get("ResourceModel")))
                    if TypeName == "AWS::Bedrock::Agent":
                        raise err("AccessDeniedException")
                    if TypeName == "AWS::MQ::Broker":
                        raise err("UnsupportedActionException")
                    return [{"ResourceDescriptions": cc_data.get(TypeName, [])}]
            return P()
    class NoRex:
        def get_paginator(self, op):
            class P:
                def paginate(self, **kw): raise err("UnauthorizedException")
            return P()
    real = boto3.session.Session
    class S(real):
        def client(self, name, *a, **k):
            if name == "cloudcontrol": return FakeCC()
            if name == "resource-explorer-2": return NoRex()
            return super().client(name, *a, **k)
    collect.boto3.session.Session = S
    out = collect.lambda_handler({"region": R}, None)
    scoped = collect.lambda_handler({"region": R, "tag_filter": {"key": "app", "value": "lms"}}, None)
    collect.boto3.session.Session = real

    assert len({c[0] for c in calls}) == len(collect.CLOUD_CONTROL_TYPES), "every curated type should be queried"
    assert ("AWS::WAFv2::WebACL", json.dumps({"Scope": "REGIONAL"})) in calls
    others = {n["name"]: n for n in out["nodes"] if n["type"] == "other"}
    print("discovered:", sorted(others))
    print("notes:", [n for n in out["notes"] if "Cloud Control" in n or "Discovery" in n])
    assert set(others) == {"lms-db-password", "lms-grading", "lms-workers", "lms-aurora", "nightly-report"}, sorted(others)
    assert sum(1 for n in out["nodes"] if n["id"] == secret) == 1, "secret drawn twice (Cloud Control + tagging API)"
    assert others["lms-db-password"]["tags"] == {"App": "LMS"}, "tags from the tagging API should be merged"
    assert others["lms-aurora"]["id"] == "cc:AWS::RDS::DBCluster:lms-aurora"
    notes = " ".join(out["notes"])
    assert "Cloud Control (27 of 29 types)" in notes and "Resource Explorer is off" in notes, notes
    assert "AccessDeniedException" in notes and "Bedrock::Agent" in notes
    assert {n["name"] for n in scoped["nodes"] if n["type"] == "other"} == {"lms-db-password"}
print("cloud control: all tests passed")
