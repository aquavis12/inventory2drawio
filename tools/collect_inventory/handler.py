"""collect_inventory: AgentCore Gateway Lambda tool (read-only).

Builds a resource graph of one AWS Region: what exists, where it sits
(VPC > Availability Zone > subnet) and how it connects. Uses only Describe/List
calls; see iam-policy.json. The agent never sees raw API output, only this graph.
"""
import json
import os
import re
import time
import uuid

import boto3
from botocore.config import Config

MAX_NODES = 200
BOTO_CFG = Config(retries={"max_attempts": 8, "mode": "adaptive"})


CUSTOMER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
READ_ROLE_NAME = "inv2drawio-readonly"   # the only role name this tool will ever assume


def load_profile(customer_id, bucket):
    """Trusted customer config. Lives in S3, never passes through the model."""
    if not CUSTOMER_ID.match(customer_id or ""):
        raise ValueError("customer_id must be lowercase letters, digits and hyphens")
    try:
        body = boto3.client("s3").get_object(
            Bucket=bucket, Key=f"customers/{customer_id}/profile.json")["Body"].read()
    except Exception:
        raise ValueError(f"unknown customer '{customer_id}'")
    profile = json.loads(body)
    role, account = profile.get("role_arn", ""), str(profile.get("account_id", ""))
    if not re.match(rf"^arn:aws:iam::{re.escape(account)}:role/{READ_ROLE_NAME}$", role) or len(account) != 12:
        raise ValueError(f"profile for '{customer_id}' must use role {READ_ROLE_NAME} in its own account")
    if not profile.get("external_id") or not profile.get("allowed_regions"):
        raise ValueError(f"profile for '{customer_id}' needs external_id and allowed_regions")
    return profile


def customer_session(profile, region):
    creds = boto3.client("sts", config=BOTO_CFG).assume_role(
        RoleArn=profile["role_arn"], RoleSessionName="inv2drawio",
        ExternalId=profile["external_id"], DurationSeconds=900)["Credentials"]
    return boto3.session.Session(
        aws_access_key_id=creds["AccessKeyId"], aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"], region_name=region)


def lambda_handler(event, context):
    bucket = os.environ.get("WORK_BUCKET")
    customer_id = event.get("customer_id") or None
    region = event.get("region") or boto3.session.Session().region_name
    if not re.match(r"^[a-z]{2}(-[a-z]+)+-\d$", region or ""):
        return {"ok": False, "error": "region must look like us-east-1"}
    tag = event.get("tag_filter") or None  # {"key": "App", "value": "orders"}
    if tag and not (isinstance(tag, dict) and tag.get("key")):
        return {"ok": False, "error": "tag_filter must be {key, value}"}
    vpc_ids = event.get("vpc_ids") or None
    group = (event.get("resource_group") or "").strip() or None

    profile = None
    if customer_id:
        if not bucket:
            return {"ok": False, "error": "WORK_BUCKET is not set; customer profiles live there"}
        try:
            profile = load_profile(customer_id, bucket)
        except ValueError as err:
            return {"ok": False, "error": str(err)}
        if region not in profile["allowed_regions"]:
            return {"ok": False, "error": f"{region} is not approved for {customer_id}. "
                                          f"Approved: {', '.join(profile['allowed_regions'])}"}
        try:
            session = customer_session(profile, region)
        except Exception as err:
            return {"ok": False, "error": f"could not assume the read-only role for {customer_id}: "
                                          f"{type(err).__name__}"}
    elif os.environ.get("ALLOW_OWN_ACCOUNT", "true").lower() == "true":
        session = boto3.session.Session(region_name=region)   # sandbox mode: this account
    else:
        return {"ok": False, "error": "customer_id is required"}

    include = [x for x in (event.get("include_services") or []) if isinstance(x, str)][:20]
    graph = collect(session, tag, vpc_ids, group, include, event.get("include_connected") is True)
    graph["region"] = region
    graph["customer"] = {"id": customer_id or "own-account",
                         "name": (profile or {}).get("name") or customer_id or "This account"}
    if not bucket:  # local runs and tests: return everything inline
        return {"ok": True, **graph}
    # Full graph goes to S3; the agent gets a compact view to reason about.
    key = (f"inventory/{customer_id or 'own-account'}/"
           f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.json")
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=json.dumps(graph, default=str),
                                  ContentType="application/json")
    compact = [{"id": n["id"], "type": n["type"], "name": n["name"],
                **({"vpc": n["vpc"]} if n.get("vpc") else {}),
                **({"public": n["public"]} if "public" in n else {}),
                **({"group": n["group"]} if n.get("group") else {}),
                **({"global": True} if n.get("is_global") else {}),
                **({"default_vpc": True} if n.get("is_default") else {}),
                **({"service": n["service"]} if n.get("service") else {})} for n in graph["nodes"]]
    return {"ok": True, "graph_s3_uri": f"s3://{bucket}/{key}", "customer": graph["customer"],
            "region": region, "summary": graph["summary"], "notes": graph["notes"], "nodes": compact}


def _optional(notes, what, call, default):
    """Run a call that only adds detail (tags, authorizers, subscriptions). If it fails,
    for example a missing permission, note it once and keep the rest of the inventory."""
    try:
        return call()
    except Exception as err:
        msg = f"{what} skipped: {type(err).__name__}"
        if msg not in notes:
            notes.append(msg)
        return default


def _tags(tag_list):
    return {t["Key"]: t["Value"] for t in tag_list or []}


def _name(tags, fallback):
    return tags.get("Name") or fallback


def collect(session, tag_filter=None, vpc_filter=None, group_filter=None, include_services=None,
            include_connected=False):
    ec2 = session.client("ec2", config=BOTO_CFG)
    region = session.region_name
    account = session.client("sts", config=BOTO_CFG).get_caller_identity()["Account"]
    nodes, edges, notes = {}, [], []

    def add(node_id, kind, name, **attrs):
        nodes[node_id] = {"id": node_id, "type": kind, "name": name, **attrs}

    def pages(client, op, key, **kw):
        for page in client.get_paginator(op).paginate(**kw):
            yield from page.get(key, [])

    # Network skeleton ----------------------------------------------------
    for vpc in pages(ec2, "describe_vpcs", "Vpcs"):
        tags = _tags(vpc.get("Tags"))
        add(vpc["VpcId"], "vpc", _name(tags, vpc["VpcId"]), cidr=vpc["CidrBlock"],
            arn=f"arn:aws:ec2:{region}:{account}:vpc/{vpc['VpcId']}",
            is_default=vpc.get("IsDefault", False), tags=tags)

    # A subnet is public when its route table sends 0.0.0.0/0 to an internet gateway.
    # Names lie ("public-1" with no IGW route is private), routes don't.
    route_tables = list(pages(ec2, "describe_route_tables", "RouteTables"))
    igw_route = {rt["RouteTableId"]: any(r.get("GatewayId", "").startswith("igw-")
                                         and r.get("DestinationCidrBlock") == "0.0.0.0/0"
                                         for r in rt.get("Routes", []))
                 for rt in route_tables}
    subnet_rt, main_rt = {}, {}
    for rt in route_tables:
        for assoc in rt.get("Associations", []):
            if assoc.get("SubnetId"):
                subnet_rt[assoc["SubnetId"]] = rt["RouteTableId"]
            if assoc.get("Main"):
                main_rt[rt["VpcId"]] = rt["RouteTableId"]

    for sn in pages(ec2, "describe_subnets", "Subnets"):
        tags = _tags(sn.get("Tags"))
        rt_id = subnet_rt.get(sn["SubnetId"]) or main_rt.get(sn["VpcId"])
        add(sn["SubnetId"], "subnet", _name(tags, sn["SubnetId"]), vpc=sn["VpcId"],
            arn=f"arn:aws:ec2:{region}:{account}:subnet/{sn['SubnetId']}",
            az=sn["AvailabilityZone"], cidr=sn["CidrBlock"],
            public=bool(igw_route.get(rt_id)), tags=tags)

    for igw in pages(ec2, "describe_internet_gateways", "InternetGateways"):
        for att in igw.get("Attachments", []):
            tags = _tags(igw.get("Tags"))
            add(igw["InternetGatewayId"], "internet_gateway",
                _name(tags, igw["InternetGatewayId"]), vpc=att["VpcId"], tags=tags)

    for nat in pages(ec2, "describe_nat_gateways", "NatGateways"):
        if nat.get("State") in ("deleted", "deleting", "failed"):
            continue
        tags = _tags(nat.get("Tags"))
        add(nat["NatGatewayId"], "nat_gateway", _name(tags, nat["NatGatewayId"]),
            vpc=nat["VpcId"], subnet=nat["SubnetId"], tags=tags)

    # Compute ---------------------------------------------------------------
    for res in pages(ec2, "describe_instances", "Reservations"):
        for inst in res["Instances"]:
            if inst["State"]["Name"] in ("terminated", "shutting-down"):
                continue
            tags = _tags(inst.get("Tags"))
            add(inst["InstanceId"], "ec2_instance", _name(tags, inst["InstanceId"]),
                arn=f"arn:aws:ec2:{region}:{account}:instance/{inst['InstanceId']}",
                vpc=inst.get("VpcId"), subnet=inst.get("SubnetId"),
                detail=inst["InstanceType"], state=inst["State"]["Name"], tags=tags)

    lam = session.client("lambda", config=BOTO_CFG)
    env_values = {}   # function ARN -> env var values; compared below, never stored or returned
    for fn in pages(lam, "list_functions", "Functions"):
        env_values[fn["FunctionArn"]] = {str(v).strip() for v in
                                         ((fn.get("Environment") or {}).get("Variables") or {}).values()}
        vpc_cfg = fn.get("VpcConfig") or {}
        subnets = vpc_cfg.get("SubnetIds") or []
        tags = {}
        if tag_filter:  # tags cost an extra call per function; only fetch when filtering
            tags = lam.list_tags(Resource=fn["FunctionArn"]).get("Tags", {})
        add(fn["FunctionArn"], "lambda_function", fn["FunctionName"],
            vpc=vpc_cfg.get("VpcId") or None, subnet=subnets[0] if subnets else None,
            detail=fn.get("Runtime") or "container", tags=tags)
    for esm in pages(lam, "list_event_source_mappings", "EventSourceMappings"):
        edges.append({"from": esm["EventSourceArn"], "to": esm["FunctionArn"],
                      "kind": "triggers"})

    # Load balancers: LB -> target group -> targets ----------------------------
    elb = session.client("elbv2", config=BOTO_CFG)
    lbs = list(pages(elb, "describe_load_balancers", "LoadBalancers"))
    lb_tags = {}
    if tag_filter:
        arns = [lb["LoadBalancerArn"] for lb in lbs]
        for i in range(0, len(arns), 20):  # describe_tags takes at most 20 ARNs
            for d in elb.describe_tags(ResourceArns=arns[i:i + 20]).get("TagDescriptions", []):
                lb_tags[d["ResourceArn"]] = _tags(d.get("Tags"))
    cert_links = []
    lb_dns = {lb["DNSName"].lower(): lb["LoadBalancerArn"] for lb in lbs if lb.get("DNSName")}
    for lb in lbs:
        subnets = [az["SubnetId"] for az in lb.get("AvailabilityZones", []) if az.get("SubnetId")]
        add(lb["LoadBalancerArn"], "load_balancer", lb["LoadBalancerName"],
            vpc=lb.get("VpcId"), subnets=subnets, detail=lb["Type"],
            scheme=lb.get("Scheme"), tags=lb_tags.get(lb["LoadBalancerArn"], {}))
        for tg in pages(elb, "describe_target_groups", "TargetGroups",
                        LoadBalancerArn=lb["LoadBalancerArn"]):
            health = elb.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
            for desc in health.get("TargetHealthDescriptions", []):
                edges.append({"from": lb["LoadBalancerArn"], "to": desc["Target"]["Id"],
                              "kind": "routes_to", "label": f"{tg.get('Protocol', '')}:{tg.get('Port', '')}".strip(":")})

    # Data -------------------------------------------------------------------
    rds = session.client("rds", config=BOTO_CFG)
    for db in pages(rds, "describe_db_instances", "DBInstances"):
        group = db.get("DBSubnetGroup") or {}
        az = db.get("AvailabilityZone")
        subnet = next((s["SubnetIdentifier"] for s in group.get("Subnets", [])
                       if (s.get("SubnetAvailabilityZone") or {}).get("Name") == az), None)
        add(db["DBInstanceArn"], "rds_instance", db["DBInstanceIdentifier"],
            vpc=group.get("VpcId"), subnet=subnet, az=az,
            detail=f"{db['Engine']} {db['DBInstanceClass']}",
            multi_az=db.get("MultiAZ", False), tags=_tags(db.get("TagList")))

    ddb = session.client("dynamodb", config=BOTO_CFG)
    for name in pages(ddb, "list_tables", "TableNames"):
        tags = {}
        if tag_filter:
            arn = ddb.describe_table(TableName=name)["Table"]["TableArn"]
            tags = _tags(ddb.list_tags_of_resource(ResourceArn=arn).get("Tags"))
        add(f"dynamodb:{name}", "dynamodb_table", name, tags=tags,
            arn=f"arn:aws:dynamodb:{region}:{account}:table/{name}")
    sqs = session.client("sqs", config=BOTO_CFG)
    for url in sqs.list_queues().get("QueueUrls", []):
        arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        tags = sqs.list_queue_tags(QueueUrl=url).get("Tags", {}) if tag_filter else {}
        add(arn, "sqs_queue", url.rsplit("/", 1)[-1], tags=tags, url=url)
    # S3: list is account-wide, so check each bucket's Region and keep only this one's.
    s3 = session.client("s3", config=BOTO_CFG)
    other_region_buckets = 0
    for bucket in s3.list_buckets().get("Buckets", []):
        name = bucket["Name"]
        try:
            loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint") or "us-east-1"
        except s3.exceptions.ClientError:
            loc = None  # no permission on this bucket: leave it out rather than guess
        if loc != region:
            other_region_buckets += 1
            continue
        tags = {}
        if tag_filter:
            try:
                tags = _tags(s3.get_bucket_tagging(Bucket=name).get("TagSet"))
            except s3.exceptions.ClientError:  # NoSuchTagSet
                tags = {}
        add(f"s3:{name}", "s3_bucket", name, arn=f"arn:aws:s3:::{name}", tags=tags)
    if other_region_buckets:
        notes.append(f"{other_region_buckets} S3 buckets are in other Regions (or unreadable) and are not drawn.")

    # API Gateway: HTTP/WebSocket APIs (v2) and REST APIs (v1), with their Lambda integrations.
    fn_arn = re.compile(r"(arn:aws[\w-]*:lambda:[\w-]+:\d+:function:[A-Za-z0-9_-]+)")
    api_hosts, authorizer_links = {}, []
    try:
        gw2 = session.client("apigatewayv2", config=BOTO_CFG)
        token = None
        while True:
            page = gw2.get_apis(**({"NextToken": token} if token else {}))
            for api in page.get("Items", []):
                aid = f"apigw:{api['ApiId']}"
                add(aid, "api_gateway", api["Name"], detail=f"{api.get('ProtocolType', 'HTTP')} API",
                    arn=f"arn:aws:apigateway:{region}::/apis/{api['ApiId']}", tags=api.get("Tags") or {})
                api_hosts[f"{api['ApiId']}.execute-api.{region}.amazonaws.com"] = aid
                for integ in gw2.get_integrations(ApiId=api["ApiId"]).get("Items", []):
                    m = fn_arn.search(integ.get("IntegrationUri") or "")
                    if m:
                        edges.append({"from": aid, "to": m[1], "kind": "invokes"})
                for auth in _optional(notes, "API Gateway authorizers",
                                      lambda: gw2.get_authorizers(ApiId=api["ApiId"]).get("Items", []), []):
                    issuer = ((auth.get("JwtConfiguration") or {}).get("Issuer") or "")
                    pool = re.search(r"cognito-idp\.[\w-]+\.amazonaws\.com/([\w-]+_[A-Za-z0-9]+)", issuer)
                    if pool:
                        authorizer_links.append((aid, pool[1]))
            token = page.get("NextToken")
            if not token:
                break
    except Exception as err:
        notes.append(f"API Gateway (HTTP APIs) skipped: {type(err).__name__}")
    try:
        gw1 = session.client("apigateway", config=BOTO_CFG)
        for api in pages(gw1, "get_rest_apis", "items"):
            aid = f"apigw:{api['id']}"
            add(aid, "api_gateway", api["name"], detail="REST API",
                arn=f"arn:aws:apigateway:{region}::/restapis/{api['id']}", tags=api.get("tags") or {})
            api_hosts[f"{api['id']}.execute-api.{region}.amazonaws.com"] = aid
            # embed=methods returns every method's integration in one call per API
            for auth in _optional(notes, "API Gateway authorizers",
                                  lambda: gw1.get_authorizers(restApiId=api["id"]).get("items", []), []):
                for prov in auth.get("providerARNs") or []:
                    pool = re.search(r":userpool/([\w-]+_[A-Za-z0-9]+)$", prov)
                    if pool:
                        authorizer_links.append((aid, pool[1]))
            for res in pages(gw1, "get_resources", "items", restApiId=api["id"], embed=["methods"]):
                for method in (res.get("resourceMethods") or {}).values():
                    m = fn_arn.search((method.get("methodIntegration") or {}).get("uri") or "")
                    if m:
                        edges.append({"from": aid, "to": m[1], "kind": "invokes"})
    except Exception as err:  # missing permission must not sink the whole inventory
        notes.append(f"API Gateway (REST APIs) skipped: {type(err).__name__}")

    # SNS topics and their subscriptions (topic -> Lambda / SQS).
    try:
        sns = session.client("sns", config=BOTO_CFG)
        for topic in pages(sns, "list_topics", "Topics"):
            arn = topic["TopicArn"]
            tags = _optional(notes, "SNS tags", lambda: _tags(sns.list_tags_for_resource(
                ResourceArn=arn).get("Tags")), {}) if tag_filter else {}
            add(arn, "sns_topic", arn.rsplit(":", 1)[-1], arn=arn, tags=tags)
            for sub in _optional(notes, "SNS subscriptions", lambda: list(pages(
                    sns, "list_subscriptions_by_topic", "Subscriptions", TopicArn=arn)), []):
                if sub.get("Protocol") in ("lambda", "sqs") and sub.get("Endpoint", "").startswith("arn:"):
                    edges.append({"from": arn, "to": sub["Endpoint"], "kind": "delivers"})
    except Exception as err:
        notes.append(f"SNS skipped: {type(err).__name__}")

    # Cognito user pools (linked from API Gateway authorizers above).
    try:
        idp = session.client("cognito-idp", config=BOTO_CFG)
        token = None
        while True:
            page = idp.list_user_pools(MaxResults=60, **({"NextToken": token} if token else {}))
            for pool in page.get("UserPools", []):
                parn = f"arn:aws:cognito-idp:{region}:{account}:userpool/{pool['Id']}"
                tags = _optional(notes, "Cognito tags", lambda: idp.list_tags_for_resource(
                    ResourceArn=parn).get("Tags", {}), {}) if tag_filter else {}
                add(parn, "cognito_user_pool", pool.get("Name") or pool["Id"], detail="User pool",
                    arn=parn, pool_id=pool["Id"], tags=tags)
            token = page.get("NextToken")
            if not token:
                break
        by_pool = {n["pool_id"]: n["id"] for n in nodes.values() if n["type"] == "cognito_user_pool"}
        for api_id, pool_id in authorizer_links:
            if pool_id in by_pool:
                edges.append({"from": api_id, "to": by_pool[pool_id], "kind": "authorizes"})
    except Exception as err:
        notes.append(f"Cognito skipped: {type(err).__name__}")

    # Global services: CloudFront and Route 53 are not tied to this Region.
    try:
        cf = session.client("cloudfront", config=BOTO_CFG)
        marker = None
        while True:
            dl = cf.list_distributions(**({"Marker": marker} if marker else {})).get("DistributionList", {})
            for dist in dl.get("Items", []) or []:
                aliases = (dist.get("Aliases") or {}).get("Items") or []
                cf_tags = {}
                if tag_filter:
                    try:
                        cf_tags = _tags(cf.list_tags_for_resource(Resource=dist["ARN"])
                                        .get("Tags", {}).get("Items"))
                    except Exception:
                        cf_tags = {}
                add(dist["ARN"], "cloudfront_distribution", aliases[0] if aliases else dist["DomainName"],
                    detail=dist["DomainName"] if aliases else "CloudFront", is_global=True, arn=dist["ARN"],
                    tags=cf_tags)
                cert = (dist.get("ViewerCertificate") or {}).get("ACMCertificateArn")
                if cert:
                    cert_links.append((dist["ARN"], cert))
                for origin in (dist.get("Origins") or {}).get("Items") or []:
                    host = origin["DomainName"].lower()
                    bucket = re.match(r"^(.+?)\.s3[.-]", host)
                    target = (f"s3:{bucket[1]}" if bucket else None) or lb_dns.get(host) or api_hosts.get(host)
                    if target:
                        edges.append({"from": dist["ARN"], "to": target, "kind": "origin"})
            if not dl.get("IsTruncated"):
                break
            marker = dl.get("NextMarker")
    except Exception as err:
        notes.append(f"CloudFront skipped: {type(err).__name__}")
    # ACM certificates in this Region (CloudFront certificates live in us-east-1).
    try:
        acm = session.client("acm", config=BOTO_CFG)
        for cert in pages(acm, "list_certificates", "CertificateSummaryList"):
            carn = cert["CertificateArn"]
            tags = _optional(notes, "ACM tags", lambda: _tags(acm.list_tags_for_certificate(
                CertificateArn=carn).get("Tags")), {}) if tag_filter else {}
            add(carn, "acm_certificate", cert.get("DomainName") or carn.rsplit("/", 1)[-1],
                detail=(cert.get("Status") or "certificate").lower().replace("_", " "), arn=carn, tags=tags)
        for dist_arn, carn in cert_links:
            edges.append({"from": dist_arn, "to": carn, "kind": "uses certificate"})
    except Exception as err:
        notes.append(f"ACM skipped: {type(err).__name__}")

    try:
        r53 = session.client("route53", config=BOTO_CFG)
        for zone in pages(r53, "list_hosted_zones", "HostedZones"):
            zid = zone["Id"].rsplit("/", 1)[-1]
            private = (zone.get("Config") or {}).get("PrivateZone")
            add(f"route53:{zid}", "route53_zone", zone["Name"].rstrip("."), is_global=True,
                detail=f"{'private' if private else 'public'}, {zone.get('ResourceRecordSetCount', '?')} records",
                arn=f"arn:aws:route53:::hostedzone/{zid}", tags={})
    except Exception as err:
        notes.append(f"Route 53 skipped: {type(err).__name__}")

    discover(session, region, nodes, notes, include_services)

    # AWS Resource Groups: remember which group each resource belongs to.
    for n in nodes.values():
        n.setdefault("arn", n["id"] if n["id"].startswith("arn:") else None)
    try:
        rg = session.client("resource-groups", config=BOTO_CFG)
        members = {}
        for name in sorted(g["GroupName"] for g in pages(rg, "list_groups", "GroupIdentifiers")):
            members[name] = []
            for page in rg.get_paginator("list_group_resources").paginate(Group=name):
                idents = [r["Identifier"] for r in page.get("Resources", [])] or page.get("ResourceIdentifiers", [])
                members[name] += [i["ResourceArn"] for i in idents]
        multi = assign_groups(nodes, members)
        if multi:
            notes.append(f"{multi} resources are in more than one resource group; drawn in the first by name.")
    except Exception as err:
        notes.append(f"Resource groups skipped: {type(err).__name__}")

    # Lambda -> data store: an environment variable whose value is exactly a table, bucket
    # or queue name (or ARN/URL) means the function uses it. Exact matches only.
    refs = {}
    for n in nodes.values():
        if n["type"] == "dynamodb_table":
            refs[n["name"]] = n["id"]
            if n.get("arn"):
                refs[n["arn"]] = n["id"]
        elif n["type"] == "s3_bucket":
            refs[n["name"]] = n["id"]
            refs[f"arn:aws:s3:::{n['name']}"] = n["id"]
        elif n["type"] == "sns_topic":
            refs[n["id"]] = n["id"]
        elif n["type"] == "cognito_user_pool":
            refs[n["id"]] = n["id"]
            refs[n["pool_id"]] = n["id"]
        elif n["type"] == "sqs_queue":
            refs[n["name"]] = n["id"]
            refs[n["id"]] = n["id"]
            if n.get("url"):
                refs[n["url"]] = n["id"]
    for fn_arn, values in env_values.items():
        for target in sorted({refs[v] for v in values if v in refs}):
            edges.append({"from": fn_arn, "to": target, "kind": "uses"})
    env_values.clear()

    # Event source ARNs for DynamoDB streams point at the stream, not the table.
    for e in edges:
        m = re.match(r"^arn:aws:dynamodb:[^:]+:\d+:table/([^/]+)/stream/", e["from"])
        if m:
            e["from"] = f"dynamodb:{m[1]}"

    return _finish(nodes, edges, notes, tag_filter, vpc_filter, group_filter, include_connected)


def assign_groups(nodes, members):
    """members: {group name: [resource ARNs]}. First group by name wins. Returns the
    number of resources that were in more than one group."""
    by_arn = {n["arn"]: n for n in nodes.values() if n.get("arn")}
    multi = 0
    for name in sorted(members):
        for arn in members[name]:
            node = by_arn.get(arn)
            if node is None:
                continue        # a resource type this tool doesn't draw
            if "group" in node:
                multi += 1
            else:
                node["group"] = name
    return multi


def _tag_value(tags, key):
    """Tag keys and values are case-sensitive in AWS, but people type them casually."""
    want = key.strip().lower()
    for k, v in (tags or {}).items():
        if k.lower() == want:
            return v
    return None


# ---- Discovery: everything that exists, from Resource Explorer (or the tagging API) ----
# Services whose resources the dedicated collectors above already handle, with placement
# and connections. Discovery skips them so nothing is drawn twice.
COVERED_SERVICES = {"lambda", "s3", "dynamodb", "sqs", "sns", "cognito-idp", "acm", "cloudfront",
                    "route53", "apigateway", "elasticloadbalancing", "rds"}
COVERED_EC2 = {"instance", "vpc", "subnet", "internet-gateway", "natgateway"}
# Discovery without any account setup: Cloud Control lists these types generically.
# CloudFormation type -> (service prefix, resource type, extra list parameters or None).
CLOUD_CONTROL_TYPES = {
    "AWS::SecretsManager::Secret": ("secretsmanager", "secret", None),
    "AWS::StepFunctions::StateMachine": ("states", "stateMachine", None),
    "AWS::ECS::Cluster": ("ecs", "cluster", None),
    "AWS::EKS::Cluster": ("eks", "cluster", None),
    "AWS::ECR::Repository": ("ecr", "repository", None),
    "AWS::Events::EventBus": ("events", "event-bus", None),
    "AWS::Events::Rule": ("events", "rule", None),
    "AWS::Scheduler::Schedule": ("scheduler", "schedule", None),
    "AWS::Kinesis::Stream": ("kinesis", "stream", None),
    "AWS::KinesisFirehose::DeliveryStream": ("firehose", "deliverystream", None),
    "AWS::ElastiCache::ServerlessCache": ("elasticache", "serverlesscache", None),
    "AWS::Redshift::Cluster": ("redshift", "cluster", None),
    "AWS::OpenSearchService::Domain": ("es", "domain", None),
    "AWS::EFS::FileSystem": ("elasticfilesystem", "file-system", None),
    "AWS::Backup::BackupVault": ("backup", "backup-vault", None),
    "AWS::AppSync::GraphQLApi": ("appsync", "apis", None),
    "AWS::Amplify::App": ("amplify", "apps", None),
    "AWS::AppRunner::Service": ("apprunner", "service", None),
    "AWS::Batch::ComputeEnvironment": ("batch", "compute-environment", None),
    "AWS::WAFv2::WebACL": ("wafv2", "regional/webacl", {"Scope": "REGIONAL"}),
    "AWS::SageMaker::Endpoint": ("sagemaker", "endpoint", None),
    "AWS::Bedrock::Agent": ("bedrock", "agent", None),
    "AWS::Bedrock::KnowledgeBase": ("bedrock", "knowledge-base", None),
    "AWS::CodeBuild::Project": ("codebuild", "project", None),
    "AWS::EC2::TransitGateway": ("ec2", "transit-gateway", None),
    "AWS::EC2::VPCEndpoint": ("ec2", "vpc-endpoint", None),
    "AWS::EC2::VPCPeeringConnection": ("ec2", "vpc-peering-connection", None),
    "AWS::RDS::DBCluster": ("rds", "cluster", None),
    "AWS::MQ::Broker": ("mq", "broker", None),
}
ALLOWED_IN_COVERED = {("rds", "cluster")}   # Aurora clusters aren't drawn by the RDS collector

# Types that exist in every account and would bury the architecture.
NOISE_SERVICES = {"iam", "logs", "ssm", "resource-groups", "xray", "cloudtrail", "config", "kms",
                  "organizations", "tag", "athena", "access-analyzer", "ram", "sso", "support"}
NOISE_SERVICES |= {"resiliencehub", "servicecatalog", "appconfig", "resource-explorer-2",
                   "codestar-connections", "codeconnections", "cloudformation", "ce", "budgets",
                   "notifications", "health", "trustedadvisor"}
# Configuration and history objects, not architecture.
NOISE_TYPES = {("ecs", "task-definition"), ("apprunner", "autoscalingconfiguration"),
               ("apprunner", "observabilityconfiguration"), ("backup", "backup-plan"),
               ("events", "api-destination"), ("events", "connection"), ("lambda", "layer"),
               ("sagemaker", "endpoint-config"), ("glue", "table"), ("glue", "database")}
# Names AWS gives the resources it creates for you.
AWS_MANAGED_NAME = re.compile(r"^(AWS|aws-|Aws|AutoScalingManagedRule|DefaultConfiguration|"
                              r"default$|StepFunctionsGetEventsForStepFunctionsExecutionRule)")

NOISE_EC2 = {"network-interface", "security-group", "security-group-rule", "route-table",
             "network-acl", "dhcp-options", "volume", "snapshot", "image", "key-pair",
             "launch-template", "elastic-ip", "prefix-list", "ipam", "ipam-pool", "ipam-scope"}


def _arn_parts(arn):
    """arn:aws:<service>:<region>:<account>:<resource> -> (service, type, name).
    The type ends at whichever separator comes first: "task-definition/worker:1" is type
    task-definition, name worker:1; "stateMachine:lms-grading" is type stateMachine."""
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return None, None, arn
    service, res = parts[2], parts[5]
    cuts = [i for i in (res.find("/"), res.find(":")) if i >= 0]
    if not cuts:
        return service, "", res
    i = min(cuts)
    rtype, rest = res[:i], res[i + 1:]
    if res[i] == ":":
        name = rest.split(":")[0].rstrip("/").rsplit("/", 1)[-1] or rest
    else:
        name = rest.rsplit("/", 1)[-1] or rest
    return service, rtype, name


def _is_subresource(arn, rtype):
    """Branches, domains, versions... live inside another resource: draw the parent only."""
    if rtype in ("regional/webacl", "global/webacl"):   # WAF ARNs always have extra slashes
        return False
    if "/" in (rtype or ""):
        return True
    res = arn.split(":", 5)[-1]
    return res.count("/") >= 3


def _skip(service, rtype):
    if (service, rtype) in ALLOWED_IN_COVERED:
        return False
    if (service, rtype) in NOISE_TYPES:
        return True
    if service in NOISE_SERVICES or service in COVERED_SERVICES:
        return True
    if service == "ec2":
        return rtype in COVERED_EC2 or rtype in NOISE_EC2 or not rtype
    return False


def _cc_details(props):
    """Pull ARN, a readable name and tags out of a Cloud Control properties document.
    Many list handlers return only identifiers, so every field is optional."""
    arn = None
    for k, v in props.items():
        if isinstance(v, str) and v.startswith("arn:aws") and (k == "Arn" or k.endswith("Arn") or k == "Id"):
            arn = v
            break
    name = props.get("Name") or next((v for k, v in props.items()
                                      if k.endswith("Name") and isinstance(v, str)), None)
    raw = props.get("Tags") or []
    tags = ({t.get("Key"): t.get("Value") for t in raw if isinstance(t, dict)} if isinstance(raw, list)
            else dict(raw) if isinstance(raw, dict) else {})
    return arn, name, tags


def _cloud_control(session, notes):
    """List the curated types in parallel. Returns [(arn_or_id, service, rtype, name, tags)]."""
    from concurrent.futures import ThreadPoolExecutor
    cc = session.client("cloudcontrol", config=BOTO_CFG)

    def one(type_name):
        service, rtype, model = CLOUD_CONTROL_TYPES[type_name]
        out = []
        kw = {"TypeName": type_name, "PaginationConfig": {"PageSize": 100}}
        if model:
            kw["ResourceModel"] = json.dumps(model)
        for page in cc.get_paginator("list_resources").paginate(**kw):
            for d in page.get("ResourceDescriptions", []):
                try:
                    props = json.loads(d.get("Properties") or "{}")
                except ValueError:
                    props = {}
                arn, name, tags = _cc_details(props)
                ident = d.get("Identifier") or ""
                key = arn or (ident if ident.startswith("arn:") else f"cc:{type_name}:{ident}")
                if not name and not ident.startswith("arn:"):
                    name = ident.rsplit("/", 1)[-1]
                out.append((key, service, rtype, name, tags))   # None: named from the ARN later
        return out

    found, failed = [], {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {t: pool.submit(one, t) for t in CLOUD_CONTROL_TYPES}
        for t, fut in futures.items():
            try:
                found += fut.result()
            except Exception as err:
                code = getattr(err, "response", {}).get("Error", {}).get("Code") or type(err).__name__
                failed.setdefault(code, []).append(t.split("::", 1)[-1])
    for code, types in failed.items():
        notes.append(f"Cloud Control could not list {len(types)} types ({code}): {', '.join(types[:6])}"
                     f"{'...' if len(types) > 6 else ''}")
    return found, len(CLOUD_CONTROL_TYPES) - sum(len(v) for v in failed.values())


def discover(session, region, nodes, notes, include_services=None):
    """Add every remaining resource as a generic node, so nothing silently disappears.
    Returns how many were added. Never raises."""
    known = {n.get("arn") for n in nodes.values() if n.get("arn")} | set(nodes)
    allow = {x.lower() for x in (include_services or [])}
    added, skipped, source, found = 0, 0, None, []
    try:
        rex = session.client("resource-explorer-2", config=BOTO_CFG)
        for page in rex.get_paginator("list_resources").paginate(
                Filters={"FilterString": f"region:{region}"}, PaginationConfig={"PageSize": 1000}):
            for r in page.get("Resources", []):
                tags = {}
                for prop in r.get("Properties") or []:
                    if (prop.get("Name") or "").lower() == "tags":
                        data = prop.get("Data")
                        if isinstance(data, list):
                            tags = {d.get("Key"): d.get("Value") for d in data if isinstance(d, dict)}
                        elif isinstance(data, dict):
                            tags = dict(data)
                rtype = (r.get("ResourceType") or "").split(":", 1)[-1]
                found.append((r["Arn"], r.get("Service") or _arn_parts(r["Arn"])[0], rtype, tags, None))
        source = "Resource Explorer"
    except Exception as err:
        found = []
        # No Resource Explorer (the usual case): Cloud Control for the curated types, then the
        # tagging API to add tags and anything tagged of other types. Neither needs any setup.
        cc_found, cc_ok = _cloud_control(session, notes)
        tagged = {}
        try:
            tagging = session.client("resourcegroupstaggingapi", config=BOTO_CFG)
            for page in tagging.get_paginator("get_resources").paginate(ResourcesPerPage=100):
                for m in page.get("ResourceTagMappingList", []):
                    tagged[m["ResourceARN"]] = _tags(m.get("Tags"))
        except Exception as err2:
            notes.append(f"Tagging API skipped: {type(err2).__name__}")
        seen = set()
        for key, service, rtype, name, tags in cc_found:
            found.append((key, service, rtype, {**tagged.get(key, {}), **tags}, name))
            seen.add(key)
        for arn, tags in tagged.items():
            if arn not in seen:
                service, rtype, _ = _arn_parts(arn)
                found.append((arn, service, rtype, tags, None))
        source = (f"Cloud Control ({cc_ok} of {len(CLOUD_CONTROL_TYPES)} types) and the tagging API "
                  f"(Resource Explorer is off: {type(err).__name__})")
    for arn, service, rtype, tags, cc_name in found:
        service = (service or "").lower()
        if arn in known:
            continue
        if service not in allow and (_skip(service, rtype) or _is_subresource(arn, rtype)):
            skipped += 1
            continue
        name = cc_name or _arn_parts(arn)[2]
        if service not in allow and AWS_MANAGED_NAME.match(name or ""):
            skipped += 1        # created by AWS for you, not part of your design
            continue
        if service == "secretsmanager" and not cc_name:   # ARNs end in a random "-AbC123" suffix
            name = re.sub(r"-[A-Za-z0-9]{6}$", "", name)
        if service == "wafv2" and not cc_name:            # .../webacl/<name>/<id>
            segs = arn.split(":", 5)[-1].split("/")
            name = segs[-2] if len(segs) >= 4 else name
        parts = arn.split(":", 5)
        is_global = len(parts) > 3 and parts[3] == ""      # no Region in the ARN
        nodes[arn] = {"id": arn, "type": "other", "name": tags.get("Name") or name or arn,
                      "service": service, "resource_type": rtype, "arn": arn,
                      "detail": f"{service} {rtype}".strip(), "tags": tags,
                      **({"is_global": True} if is_global else {})}
        known.add(arn)
        added += 1
    notes.append(f"Discovery via {source}: {added} more resources drawn without connections; "
                 f"{skipped} routine resources (IAM, logs, task-definition revisions, AWS-managed "
                 f"rules, sub-resources, ...) left out.")
    return added


def _finish(nodes, edges, notes, tag_filter, vpc_filter, group_filter=None, include_connected=False):
    containers = {"vpc", "subnet"}

    def keep_by_filters(n):
        if vpc_filter and n.get("vpc") not in vpc_filter and n["id"] not in vpc_filter:
            return False
        if n["type"] in containers:
            return True
        if group_filter and (n.get("group") or "").lower() != group_filter.strip().lower():
            return False
        if tag_filter:
            val = _tag_value(n.get("tags"), tag_filter["key"])
            want = (tag_filter.get("value") or "").strip().lower()
            return val is not None and (not want or val.strip().lower() == want)
        return True

    kept = {i: n for i, n in nodes.items() if keep_by_filters(n)}
    if (tag_filter or group_filter) and not include_connected:
        # Strict by default: only resources that carry the tag (or are in the group), plus
        # the VPC and subnet boxes they sit in. Say what was left out, never add it silently.
        matched = set(kept)
        neighbours = {b for e in edges for a, b in ((e["from"], e["to"]), (e["to"], e["from"]))
                      if a in matched and b in nodes and b not in matched
                      and nodes[b]["type"] not in containers}
        if neighbours:
            notes.append(f"{len(neighbours)} untagged resources are connected to the ones shown "
                         f"and were left out. Ask to include connected resources to add them.")
    if (tag_filter or group_filter) and include_connected:
        # Untagged neighbours of a match belong in the picture (the queue that
        # triggers a tagged function, the ALB in front of tagged instances).
        for e in edges:
            for a, b in ((e["from"], e["to"]), (e["to"], e["from"])):
                if a in kept and kept[a]["type"] not in containers and b in nodes and b not in kept:
                    if not vpc_filter or nodes[b].get("vpc") in vpc_filter or not nodes[b].get("vpc"):
                        kept[b] = {**nodes[b], "included_because": "connected to a match"}
        # Gateways have no app tag but are part of every kept VPC's network.
        kept_vpcs = {n.get("vpc") for n in kept.values() if n["type"] not in containers}
        for i, n in nodes.items():
            if n["type"] in ("internet_gateway", "nat_gateway") and n.get("vpc") in kept_vpcs:
                kept.setdefault(i, {**n, "included_because": "network of a kept VPC"})
    # Keep only the network that kept resources actually live in.
    used_subnets = {n.get("subnet") for n in kept.values()} | {
        s for n in kept.values() for s in n.get("subnets", [])}
    used_vpcs = {n.get("vpc") for n in kept.values() if n["type"] not in containers}
    kept = {i: n for i, n in kept.items()
            if (n["type"] == "subnet" and i in used_subnets)
            or (n["type"] == "vpc" and i in used_vpcs)
            or n["type"] not in containers}

    truncated = len(kept) > MAX_NODES
    if truncated:
        notes.append(f"More than {MAX_NODES} resources: narrow with tag_filter or vpc_ids.")
        order = ["vpc", "subnet", "internet_gateway", "nat_gateway", "load_balancer"]
        ranked = sorted(kept.values(), key=lambda n: order.index(n["type"]) if n["type"] in order else 99)
        kept = {n["id"]: n for n in ranked[:MAX_NODES]}

    kept_edges = [e for e in edges if e["from"] in kept and e["to"] in kept]
    by_type = {}
    for n in kept.values():
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
    return {"nodes": list(kept.values()), "edges": kept_edges, "notes": notes,
            "summary": {"nodes": len(kept), "edges": len(kept_edges),
                        "truncated": truncated, "by_type": dict(sorted(by_type.items()))}}


if __name__ == "__main__":
    import sys
    print(json.dumps(lambda_handler({"region": sys.argv[1] if len(sys.argv) > 1 else None}, None),
                     indent=2, default=str))
