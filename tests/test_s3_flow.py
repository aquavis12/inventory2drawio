"""Collector -> S3 -> renderer, the way the agent will call them through the Gateway."""
import importlib.util, json, os, sys
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

with mock_aws():
    boto3.client("s3").create_bucket(Bucket="d2-work")
    ec2 = boto3.client("ec2")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    sn = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.0.0/24", AvailabilityZone="us-east-1a")["Subnet"]["SubnetId"]
    ids = [ec2.run_instances(ImageId="ami-12c6146b", MinCount=1, MaxCount=1, SubnetId=sn)["Instances"][0]["InstanceId"] for _ in range(2)]

    out = collect.lambda_handler({"region": "us-east-1"}, None)
    assert out["graph_s3_uri"].startswith("s3://d2-work/inventory/") and "edges" not in out
    assert all("tags" not in n for n in out["nodes"]), "compact view must stay small"
    print("collector returned", len(json.dumps(out)), "bytes to the agent;", out["summary"]["nodes"], "nodes")

    r = render.lambda_handler({"graph_s3_uri": out["graph_s3_uri"], "title": "test", "hide_ids": [ids[0]]}, None)
    assert r["ok"] and r["resources_drawn"] == out["summary"]["nodes"] - 1, r
    body = boto3.client("s3").get_object(Bucket="d2-work", Key=r["drawio_s3_uri"].split("d2-work/")[1])["Body"].read()
    assert ids[0] not in body.decode() and body.startswith(b"<mxfile")
    bad = render.lambda_handler({"graph_s3_uri": "s3://someone-else/inventory/x.json"}, None)
    assert bad["ok"] is False
    print("s3 flow: all tests passed")
