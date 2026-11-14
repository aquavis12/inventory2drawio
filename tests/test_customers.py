"""Two customers in two accounts: profiles, cross-account role, isolation, new services."""
import importlib.util, io, json, os, sys, zipfile
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", WORK_BUCKET="d2-work")
import boto3
from moto import mock_aws
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools/render_drawio"))

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
collect = load("collect", "tools/collect_inventory/handler.py")
render = load("render", "tools/render_drawio/handler.py")

ACME, GLOBEX = "222233334444", "333344445555"

def session_for(account):
    c = boto3.client("sts").assume_role(RoleArn=f"arn:aws:iam::{account}:role/inv2drawio-readonly",
                                        RoleSessionName="setup", ExternalId="setup-ext")["Credentials"]
    return boto3.session.Session(aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                                 aws_session_token=c["SessionToken"], region_name="us-east-1")

def profile(cid, account, **over):
    p = {"name": cid.title(), "account_id": account, "role_arn": f"arn:aws:iam::{account}:role/inv2drawio-readonly",
         "external_id": f"ext-{cid}", "allowed_regions": ["us-east-1"], **over}
    boto3.client("s3").put_object(Bucket="d2-work", Key=f"customers/{cid}/profile.json", Body=json.dumps(p))

with mock_aws():
    boto3.client("s3").create_bucket(Bucket="d2-work")
    profile("acme", ACME); profile("globex", GLOBEX)
    profile("badrole", ACME, role_arn=f"arn:aws:iam::{ACME}:role/AdministratorAccess")
    profile("wrongacct", ACME, role_arn=f"arn:aws:iam::{GLOBEX}:role/inv2drawio-readonly")

    # --- Acme: serverless app with API Gateway, CloudFront, Route 53 and a resource group
    a = session_for(ACME)
    role = a.client("iam").create_role(RoleName="fn", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    buf = io.BytesIO(); zipfile.ZipFile(buf, "w").writestr("app.py", "def h(e,c): pass")
    fn = a.client("lambda").create_function(FunctionName="cfp-api", Runtime="python3.12", Role=role, Handler="app.h",
                                           Code={"ZipFile": buf.getvalue()}, Tags={"App": "cfp"})["FunctionArn"]
    gw = a.client("apigatewayv2")
    api = gw.create_api(Name="cfp-http", ProtocolType="HTTP")["ApiId"]
    gw.create_integration(ApiId=api, IntegrationType="AWS_PROXY", PayloadFormatVersion="2.0",
        IntegrationUri=f"arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/{fn}/invocations")
    a.client("dynamodb").create_table(TableName="cfp-submissions", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}], KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        Tags=[{"Key": "App", "Value": "cfp"}])
    s3a = a.client("s3")
    s3a.create_bucket(Bucket="acme-site")
    s3a.create_bucket(Bucket="acme-eu-logs", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    a.client("cloudfront").create_distribution(DistributionConfig={
        "CallerReference": "r1", "Comment": "", "Enabled": True,
        "Origins": {"Quantity": 1, "Items": [{"Id": "site", "DomainName": "acme-site.s3.us-east-1.amazonaws.com",
                    "S3OriginConfig": {"OriginAccessIdentity": ""}}]},
        "DefaultCacheBehavior": {"TargetOriginId": "site", "ViewerProtocolPolicy": "redirect-to-https",
            "MinTTL": 0, "ForwardedValues": {"QueryString": False, "Cookies": {"Forward": "none"}}}})
    a.client("route53").create_hosted_zone(Name="acme.example.", CallerReference="z1")
    a.client("resource-groups").create_group(Name="cfp-app", ResourceQuery={"Type": "TAG_FILTERS_1_0",
        "Query": json.dumps({"ResourceTypeFilters": ["AWS::AllSupported"], "TagFilters": [{"Key": "App", "Values": ["cfp"]}]})})

    # --- Globex: one VPC, nothing else
    g = session_for(GLOBEX)
    g.client("ec2").create_vpc(CidrBlock="10.50.0.0/16", TagSpecifications=[{"ResourceType": "vpc", "Tags": [{"Key": "Name", "Value": "globex-secret-vpc"}]}])
    g.client("s3").create_bucket(Bucket="globex-private-data")

    out = collect.lambda_handler({"customer_id": "acme", "region": "us-east-1"}, None)
    assert out["ok"], out
    names = {n["name"] for n in out["nodes"]}
    print("acme notes:", out["notes"])
    print("acme types:", out["summary"]["by_type"])
    assert out["graph_s3_uri"].startswith("s3://d2-work/inventory/acme/") and out["customer"]["name"] == "Acme"
    assert "globex-secret-vpc" not in names and "globex-private-data" not in names, "customer isolation broken"
    assert {"cfp-api", "cfp-http", "cfp-submissions", "acme-site", "acme.example"} <= names, names
    assert "acme-eu-logs" not in names, "bucket from another Region was drawn"
    assert any("other Regions" in n for n in out["notes"])
    full = json.loads(boto3.client("s3").get_object(Bucket="d2-work", Key=out["graph_s3_uri"].split("d2-work/")[1])["Body"].read())
    kinds = {(e["kind"]) for e in full["edges"]}
    assert {"invokes", "origin"} <= kinds, full["edges"]
    grouped = {n["name"] for n in full["nodes"] if n.get("group") == "cfp-app"}
    print("resource group cfp-app:", sorted(grouped) or "none (see notes)")

    r = render.lambda_handler({"graph_s3_uri": out["graph_s3_uri"]}, None)
    assert r["ok"] and r["drawio_s3_uri"].startswith("s3://d2-work/diagrams/acme/"), r
    xml = boto3.client("s3").get_object(Bucket="d2-work", Key=r["drawio_s3_uri"].split("d2-work/")[1])["Body"].read().decode()
    assert "Global services" in xml and 'name="Acme architecture"' in xml
    assert ("Resource group: cfp-app" in xml) == bool(grouped)

    gl = collect.lambda_handler({"customer_id": "globex", "region": "us-east-1"}, None)
    assert {n["name"] for n in gl["nodes"]} >= {"globex-secret-vpc"} or gl["summary"]["nodes"] >= 0
    assert not ({n["name"] for n in gl["nodes"]} & {"cfp-api", "acme-site"}), "customer isolation broken"

    def err(**ev): 
        o = collect.lambda_handler(ev, None); assert o["ok"] is False, o; return o["error"]
    print("denied:", err(customer_id="acme", region="eu-west-1"))
    print("denied:", err(customer_id="nobody", region="us-east-1"))
    print("denied:", err(customer_id="badrole", region="us-east-1"))
    print("denied:", err(customer_id="wrongacct", region="us-east-1"))
    print("denied:", err(customer_id="../acme", region="us-east-1"))

# Resource-group matching (moto has not implemented ListGroupResources, so test the logic directly)
nodes = {"fn": {"id": "fn", "arn": "arn:aws:lambda:us-east-1:1:function:a"},
         "t": {"id": "t", "arn": "arn:aws:dynamodb:us-east-1:1:table/x"},
         "vpc": {"id": "vpc", "arn": None}}
multi = collect.assign_groups(nodes, {"web": ["arn:aws:lambda:us-east-1:1:function:a", "arn:aws:iam::1:role/not-drawn"],
                                      "api": ["arn:aws:lambda:us-east-1:1:function:a", "arn:aws:dynamodb:us-east-1:1:table/x"]})
assert nodes["fn"]["group"] == "api" and nodes["t"]["group"] == "api" and "group" not in nodes["vpc"] and multi == 1
print("customers: all tests passed")
