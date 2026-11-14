"""An account shaped like the real LMS app: CloudFront + ACM + S3 site, HTTP API with a
Cognito authorizer, Lambda using DynamoDB tables and an SNS topic. Tags use mixed case."""
import importlib.util, io, json, os, sys, zipfile
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x")
import boto3
from moto import mock_aws
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools/render_drawio"))
from generator import build_drawio
spec = importlib.util.spec_from_file_location("collect", os.path.join(ROOT, "tools/collect_inventory/handler.py"))
collect = importlib.util.module_from_spec(spec); spec.loader.exec_module(collect)
R = "us-east-1"
TAG = [{"Key": "App", "Value": "LMS"}]          # stored as App=LMS, searched as app=lms

with mock_aws():
    ddb = boto3.client("dynamodb", R)
    for t in ("vr-activity", "vr-quiz", "vr-submissions"):
        ddb.create_table(TableName=t, BillingMode="PAY_PER_REQUEST", Tags=TAG,
                         AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                         KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}])
    ddb.create_table(TableName="unrelated-table", BillingMode="PAY_PER_REQUEST",
                     AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                     KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}])
    sns = boto3.client("sns", R)
    topic = sns.create_topic(Name="vr-daily-submissions", Tags=TAG)["TopicArn"]
    role = boto3.client("iam", R).create_role(RoleName="fn", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    buf = io.BytesIO(); zipfile.ZipFile(buf, "w").writestr("app.py", "def h(e,c): pass")
    lam = boto3.client("lambda", R)
    fn = lam.create_function(FunctionName="vr-activity-fn", Runtime="python3.12", Role=role, Handler="app.h",
        Code={"ZipFile": buf.getvalue()}, Tags={"App": "LMS"},
        Environment={"Variables": {"ACTIVITY_TABLE": "vr-activity", "QUIZ_TABLE": "vr-quiz",
                                   "SUBMISSIONS_TABLE": "vr-submissions", "TOPIC_ARN": topic,
                                   "SECRET_TOKEN": "s3cr3t-value-never-returned", "LOG_LEVEL": "INFO"}})["FunctionArn"]
    notifier = lam.create_function(FunctionName="vr-notifier", Runtime="python3.12", Role=role, Handler="app.h",
        Code={"ZipFile": buf.getvalue()})["FunctionArn"]
    sns.subscribe(TopicArn=topic, Protocol="lambda", Endpoint=notifier)

    idp = boto3.client("cognito-idp", R)
    pool = idp.create_user_pool(PoolName="lms-users", UserPoolTags={"App": "LMS"})["UserPool"]
    gw = boto3.client("apigatewayv2", R)
    api = gw.create_api(Name="vr-activity-api", ProtocolType="HTTP", Tags={"App": "LMS"})["ApiId"]
    gw.create_integration(ApiId=api, IntegrationType="AWS_PROXY", PayloadFormatVersion="2.0",
        IntegrationUri=f"arn:aws:apigateway:{R}:lambda:path/2015-03-31/functions/{fn}/invocations")
    gw.create_authorizer(ApiId=api, AuthorizerType="JWT", Name="cognito", IdentitySource=["$request.header.Authorization"],
        JwtConfiguration={"Audience": ["x"], "Issuer": f"https://cognito-idp.{R}.amazonaws.com/{pool['Id']}"})

    s3 = boto3.client("s3", R); s3.create_bucket(Bucket="vr-lms-site")
    cert = boto3.client("acm", R).request_certificate(DomainName="lms.example.com", ValidationMethod="DNS",
                                                      Tags=TAG)["CertificateArn"]
    cf = boto3.client("cloudfront", R)
    dist = cf.create_distribution_with_tags(DistributionConfigWithTags={"Tags": {"Items": TAG}, "DistributionConfig": {
        "CallerReference": "r1", "Comment": "", "Enabled": True, "Aliases": {"Quantity": 1, "Items": ["lms.example.com"]},
        "ViewerCertificate": {"ACMCertificateArn": cert, "SSLSupportMethod": "sni-only"},
        "Origins": {"Quantity": 1, "Items": [{"Id": "site", "DomainName": "vr-lms-site.s3.us-east-1.amazonaws.com",
                    "S3OriginConfig": {"OriginAccessIdentity": ""}}]},
        "DefaultCacheBehavior": {"TargetOriginId": "site", "ViewerProtocolPolicy": "redirect-to-https", "MinTTL": 0,
                                 "ForwardedValues": {"QueryString": False, "Cookies": {"Forward": "none"}}}}})["Distribution"]["ARN"]

    # The mock lacks two calls that real AWS has: HTTP API get_authorizers and Cognito tag
    # listing. Fill them in from what we created, so the linking logic is still tested.
    issuer = f"https://cognito-idp.{R}.amazonaws.com/{pool['Id']}"
    pool_arn = f"arn:aws:cognito-idp:{R}:123456789012:userpool/{pool['Id']}"
    real_session = boto3.session.Session
    class Proxy:
        def __init__(self, client, extra): self._c, self._x = client, extra
        def __getattr__(self, name): return self._x.get(name) or getattr(self._c, name)
    class PatchedSession(real_session):
        def client(self, name, *a, **k):
            cl = super().client(name, *a, **k)
            if name == "apigatewayv2":
                return Proxy(cl, {"get_authorizers": lambda ApiId: {"Items": [
                    {"AuthorizerType": "JWT", "JwtConfiguration": {"Issuer": issuer}}] if ApiId == api else []}})
            if name == "cloudfront":
                def list_distributions(**kw):
                    out = cl.list_distributions(**kw)
                    for item in out["DistributionList"].get("Items", []):
                        item["ViewerCertificate"]["ACMCertificateArn"] = cert   # mock ignores it
                    return out
                return Proxy(cl, {"list_distributions": list_distributions})
            if name == "cognito-idp":
                return Proxy(cl, {"list_tags_for_resource": lambda ResourceArn: {
                    "Tags": {"App": "LMS"} if ResourceArn == pool_arn else {}}})
            return cl
    collect.boto3.session.Session = PatchedSession
    strict = collect.lambda_handler({"region": R, "tag_filter": {"key": "app", "value": "lms"}}, None)
    out = collect.lambda_handler({"region": R, "tag_filter": {"key": "app", "value": "lms"},
                                  "include_connected": True}, None)
    collect.boto3.session.Session = real_session
    strict_names = {n["name"] for n in strict["nodes"]}
    assert "vr-notifier" not in strict_names and "vr-lms-site" not in strict_names, strict_names
    assert {"vr-activity-fn", "vr-activity-api", "lms-users", "lms.example.com"} <= strict_names, strict_names
    assert out["ok"], out
    names = {n["name"] for n in out["nodes"]}
    print("notes:", out["notes"])
    assert not any(n.startswith(("API Gateway (", "Cognito skipped", "SNS skipped", "ACM skipped")) for n in out["notes"]), out["notes"]
    print("types:", out["summary"]["by_type"])
    edges = {(e["kind"]) for e in out["edges"]}
    print("edge kinds:", sorted(edges))
    for want in ["vr-activity-fn", "vr-activity-api", "vr-activity", "vr-quiz", "vr-submissions",
                 "vr-daily-submissions", "lms-users", "lms.example.com", "vr-lms-site"]:
        assert want in names, f"missing {want}: {sorted(names)}"
    assert "unrelated-table" not in names, "tag filter leaked"
    assert "vr-notifier" in names, "SNS subscriber should come in as a connected resource"
    uses = {e["to"] for e in out["edges"] if e["kind"] == "uses" and e["from"] == fn}
    assert {f"dynamodb:{t}" for t in ("vr-activity", "vr-quiz", "vr-submissions")} | {topic} <= uses, uses
    assert {"invokes", "authorizes", "delivers", "origin", "uses certificate"} <= edges, edges
    assert "s3cr3t-value-never-returned" not in json.dumps(out), "env var value leaked"

    xml = build_drawio(out, region=R)
    for icon in ("resIcon=mxgraph.aws4.sns;", "resIcon=mxgraph.aws4.cognito;", "resIcon=mxgraph.aws4.certificate_manager_3;"):
        assert icon in xml, icon
    os.makedirs(os.path.join(ROOT, "samples"), exist_ok=True)
    open(os.path.join(ROOT, "samples/lms_shaped.drawio"), "w").write(xml)

# resource-group scope (moto can't list group members, so feed membership directly)
nodes = {
    "fn": {"id": "fn", "type": "lambda_function", "name": "f", "arn": "a1", "tags": {}},
    "t": {"id": "t", "type": "dynamodb_table", "name": "t", "arn": "a2", "tags": {}},
    "x": {"id": "x", "type": "dynamodb_table", "name": "x", "arn": "a3", "tags": {}},
    "b": {"id": "b", "type": "s3_bucket", "name": "b", "arn": "a4", "tags": {}},
}
collect.assign_groups(nodes, {"lms-study-site": ["a1", "a2"]})
import copy
edges = [{"from": "fn", "to": "b", "kind": "uses"}]
g = collect._finish(copy.deepcopy(nodes), edges, [], None, None, "LMS-Study-Site")
assert {n["id"] for n in g["nodes"]} == {"fn", "t"}, g["nodes"]            # strict: group members only
g = collect._finish(copy.deepcopy(nodes), edges, [], None, None, "LMS-Study-Site", include_connected=True)
assert {n["id"] for n in g["nodes"]} == {"fn", "t", "b"}, g["nodes"]       # on request: + connected
print("lms shape: all tests passed")
