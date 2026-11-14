import io, json, os, sys, zipfile, xml.etree.ElementTree as ET
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x")
import boto3
from moto import mock_aws

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools/render_drawio"))
from generator import build_drawio
import importlib.util
_c = importlib.util.spec_from_file_location("collect", os.path.join(ROOT, "tools/collect_inventory/handler.py"))
collect_mod = importlib.util.module_from_spec(_c); _c.loader.exec_module(collect_mod)
collect_handler = collect_mod.lambda_handler
spec = importlib.util.spec_from_file_location("parser", "/home/claude/diagram2tf/tools/parse_diagram/handler.py")
parser = importlib.util.module_from_spec(spec); spec.loader.exec_module(parser)

R = "us-east-1"

def tags(name, app="orders"):
    return [{"Key": "Name", "Value": name}, {"Key": "App", "Value": app}]

def build_account():
    ec2 = boto3.client("ec2", R)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16", TagSpecifications=[{"ResourceType": "vpc", "Tags": tags("orders-vpc")}])["Vpc"]["VpcId"]
    igw = ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc)
    pub_rt = ec2.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=pub_rt, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw)

    def subnet(cidr, az, name, public):
        sid = ec2.create_subnet(VpcId=vpc, CidrBlock=cidr, AvailabilityZone=az,
                                TagSpecifications=[{"ResourceType": "subnet", "Tags": tags(name)}])["Subnet"]["SubnetId"]
        if public:
            ec2.associate_route_table(RouteTableId=pub_rt, SubnetId=sid)
        return sid
    pa = subnet("10.0.0.0/24", "us-east-1a", "public-a", True)
    pb = subnet("10.0.1.0/24", "us-east-1b", "public-b", True)
    qa = subnet("10.0.10.0/24", "us-east-1a", "private-a", False)
    qb = subnet("10.0.11.0/24", "us-east-1b", "private-b", False)
    trap = subnet("10.0.20.0/24", "us-east-1a", "public-trap", False)   # name lies: no IGW route

    eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
    ec2.create_nat_gateway(SubnetId=pa, AllocationId=eip,
                           TagSpecifications=[{"ResourceType": "natgateway", "Tags": tags("nat-a")}])
    ami = "ami-12c6146b"
    def instance(sub, name, app="orders"):
        return ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1, InstanceType="t3.small", SubnetId=sub,
                                 TagSpecifications=[{"ResourceType": "instance", "Tags": tags(name, app)}])["Instances"][0]["InstanceId"]
    i1, i2 = instance(qa, "api-1"), instance(qb, "api-2")
    instance(trap, "batch-worker")

    elb = boto3.client("elbv2", R)
    lb = elb.create_load_balancer(Name="orders-alb", Subnets=[pa, pb], Type="application")["LoadBalancers"][0]["LoadBalancerArn"]
    tg = elb.create_target_group(Name="api", Protocol="HTTP", Port=8080, VpcId=vpc, TargetType="instance")["TargetGroups"][0]["TargetGroupArn"]
    elb.register_targets(TargetGroupArn=tg, Targets=[{"Id": i1}, {"Id": i2}])
    elb.create_listener(LoadBalancerArn=lb, Protocol="HTTP", Port=80, DefaultActions=[{"Type": "forward", "TargetGroupArn": tg}])

    rds = boto3.client("rds", R)
    rds.create_db_subnet_group(DBSubnetGroupName="orders-db", DBSubnetGroupDescription="db", SubnetIds=[qa, qb])
    rds.create_db_instance(DBInstanceIdentifier="orders-db", DBInstanceClass="db.t4g.micro", Engine="postgres",
                           MasterUsername="admin1", MasterUserPassword="Password123!", DBSubnetGroupName="orders-db",
                           AvailabilityZone="us-east-1a", AllocatedStorage=20, Tags=[{"Key": "App", "Value": "orders"}])

    iam = boto3.client("iam", R)
    role = iam.create_role(RoleName="fn", AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": []}))["Role"]["Arn"]
    buf = io.BytesIO(); zipfile.ZipFile(buf, "w").writestr("app.py", "def h(e,c): pass")
    lam = boto3.client("lambda", R)
    fn = lam.create_function(FunctionName="order-worker", Runtime="python3.12", Role=role, Handler="app.h",
                             Code={"ZipFile": buf.getvalue()}, VpcConfig={"SubnetIds": [qa, qb], "SecurityGroupIds": []},
                             Tags={"App": "orders"})["FunctionArn"]
    sqs = boto3.client("sqs", R)
    q = sqs.create_queue(QueueName="orders-queue")["QueueUrl"]
    qarn = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    lam.create_event_source_mapping(EventSourceArn=qarn, FunctionName=fn)
    boto3.client("dynamodb", R).create_table(TableName="orders", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}], KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}])
    boto3.client("s3", R).create_bucket(Bucket="orders-assets")

    other = ec2.create_vpc(CidrBlock="10.9.0.0/16", TagSpecifications=[{"ResourceType": "vpc", "Tags": tags("billing-vpc", "billing")}])["Vpc"]["VpcId"]
    osn = ec2.create_subnet(VpcId=other, CidrBlock="10.9.0.0/24", AvailabilityZone="us-east-1a")["Subnet"]["SubnetId"]
    instance(osn, "billing-1", app="billing")
    return dict(vpc=vpc, other=other, trap=trap, pa=pa, i1=i1, i2=i2, lb=lb, fn=fn, qarn=qarn)


def check_geometry(xml):
    """Children must sit inside their parent; siblings must not overlap."""
    cells = ET.fromstring(xml).find(".//root")
    geo, parent = {}, {}
    for c in cells.iter("mxCell"):
        g = c.find("mxGeometry")
        if c.get("vertex") == "1" and g is not None:
            geo[c.get("id")] = tuple(float(g.get(k)) for k in ("x", "y", "width", "height"))
            parent[c.get("id")] = c.get("parent")
    problems = []
    for cid, (x, y, w, h) in geo.items():
        p = parent[cid]
        if p in geo:
            pw, ph = geo[p][2], geo[p][3]
            if x < 0 or y < 0 or x + w > pw + 0.5 or y + h > ph + 0.5:
                problems.append(f"{cid} spills out of {p}")
    by_parent = {}
    for cid, p in parent.items():
        by_parent.setdefault(p, []).append(cid)
    for p, kids in by_parent.items():
        for i, a in enumerate(kids):
            for b in kids[i + 1:]:
                ax, ay, aw, ah = geo[a]; bx, by, bw, bh = geo[b]
                if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                    problems.append(f"{a} overlaps {b}")
    return problems


with mock_aws():
    ids = build_account()
    full = collect_handler({"region": R}, None)
    assert full["ok"], full
    nodes = {n["id"]: n for n in full["nodes"]}
    assert nodes[ids["trap"]]["public"] is False, "route-based public detection failed"
    assert nodes[ids["pa"]]["public"] is True
    assert {"from": ids["qarn"], "to": ids["fn"], "kind": "triggers"} in full["edges"]
    routes = [e for e in full["edges"] if e["kind"] == "routes_to"]
    assert {e["to"] for e in routes} == {ids["i1"], ids["i2"]}, routes

    strict = collect_handler({"region": R, "tag_filter": {"key": "App", "value": "orders"}}, None)
    strict_names = {n["name"] for n in strict["nodes"]}
    assert not any(n.get("included_because") for n in strict["nodes"]), "strict scope added untagged resources"
    assert "orders-alb" not in strict_names and "orders-queue" not in strict_names, strict_names  # untagged
    assert "nat-a" in strict_names, "nat-a carries App=orders, so it belongs"
    assert any("untagged resources are connected" in n for n in strict["notes"]), strict["notes"]
    print("inventory  strict App=orders:", strict["summary"]["nodes"], "nodes (tagged only)")
    scoped = collect_handler({"region": R, "tag_filter": {"key": "App", "value": "orders"},
                              "include_connected": True}, None)
    snodes = {n["id"]: n for n in scoped["nodes"]}
    assert ids["other"] not in snodes, "tag filter leaked the billing VPC"
    assert not any(n["name"] == "billing-1" for n in scoped["nodes"])
    assert scoped["summary"]["edges"] == 3, scoped["edges"]
    assert any(n.get("included_because") == "connected to a match" for n in scoped["nodes"])
    print("inventory  full:", full["summary"]["by_type"])
    print("inventory  App=orders + connected:", scoped["summary"]["nodes"], "nodes,", scoped["summary"]["edges"], "edges")

    open(os.path.join(ROOT, "samples/orders_scoped.drawio"), "w").write(
        build_drawio(scoped, title="orders (App=orders)", region="us-east-1"))
    assert not check_geometry(open(os.path.join(ROOT, "samples/orders_scoped.drawio")).read())
    real = open("/tmp/real_shape.drawio").read() if os.path.exists("/tmp/real_shape.drawio") else None
    assert real is None or not check_geometry(real), check_geometry(real)
    xml = build_drawio(full, title="orders", region="us-east-1")
    problems = check_geometry(xml)
    assert not problems, problems
    print("layout: no overlaps, every shape inside its parent")

    back = parser.parse_drawio(xml)
    print("round trip (Nov parser):", back["summary"]["by_type"])
    print("  needs clarification:", [(c["label"].split("<")[0] if c["label"] else c["id"], c["icon"]) for c in back["needs_clarification"]])
    print("  unmapped icons:", back["unmapped_icons"])
    alb = next(r for r in back["resources"] if r["tf_type"] == "aws_lb")
    assert any(w["kind"].startswith("group_vpc") for w in alb["within"])
    os.makedirs(os.path.join(ROOT, "samples"), exist_ok=True)
    open(os.path.join(ROOT, "samples/orders_account.drawio"), "w").write(xml)
    open(os.path.join(ROOT, "samples/orders_inventory.json"), "w").write(json.dumps(full, indent=2, default=str))
    print("end to end: all tests passed")
