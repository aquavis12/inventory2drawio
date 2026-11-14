"""Resource graph -> draw.io XML with AWS icons and a deterministic layout.

Layout is code, not LLM: models are bad at coordinates and produce overlapping
boxes. Nesting: AWS Cloud > Region > VPC > Availability Zone > subnet > resources.
Regional services (S3, DynamoDB, SQS, Lambda outside a VPC) sit in a row below.
"""
from xml.sax.saxutils import escape

CELL_W, CELL_H = 170, 120       # one icon plus its two-line label
ICON = 60
PAD, HEADER = 24, 44            # inner padding, space for a group's title
COLS = 3                        # icons per row inside a subnet

# AWS Architecture Icons category colours used by draw.io's aws4 library.
COMPUTE, DATABASE, STORAGE, NETWORK, INTEGRATION = "#ED7100", "#C925D1", "#7AA116", "#8C4FFF", "#E7157B"
SECURITY = "#DD344C"

_ICON_BASE = ("sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;strokeColor=none;"
              "dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
              "fontSize=11;fontStyle=0;aspect=fixed;pointerEvents=1;")


def _res_icon(name, color):   # service icons drawn on a coloured square
    return f"{_ICON_BASE}fillColor={color};shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{name};"


def _shape(name, color):      # resource-level icons (instance, ALB, NAT ...)
    return f"{_ICON_BASE}fillColor={color};shape=mxgraph.aws4.{name};"


STYLES = {
    "ec2_instance": _shape("instance2", COMPUTE),
    "lambda_function": _res_icon("lambda", COMPUTE),
    "load_balancer": _shape("application_load_balancer", NETWORK),
    "nat_gateway": _shape("nat_gateway", NETWORK),
    "internet_gateway": _shape("internet_gateway", NETWORK),
    "rds_instance": _res_icon("rds", DATABASE),
    "dynamodb_table": _res_icon("dynamodb", DATABASE),
    "s3_bucket": _res_icon("s3", STORAGE),
    "sqs_queue": _res_icon("sqs", INTEGRATION),
    "api_gateway": _res_icon("api_gateway", INTEGRATION),
    "cloudfront_distribution": _res_icon("cloudfront", NETWORK),
    "route53_zone": _res_icon("route_53", NETWORK),
    "sns_topic": _res_icon("sns", INTEGRATION),
    "cognito_user_pool": _res_icon("cognito", SECURITY),
    "acm_certificate": _res_icon("certificate_manager_3", SECURITY),
}
# Discovered resources ("other"): service prefix -> (draw.io aws4 icon, colour, flow column).
SERVICE_ICONS = {
    "secretsmanager": ("secrets_manager", SECURITY, 0), "wafv2": ("waf", SECURITY, 0),
    "waf": ("waf", SECURITY, 0), "guardduty": ("guardduty", SECURITY, 0),
    "appsync": ("appsync", INTEGRATION, 1), "amplify": ("amplify", NETWORK, 0),
    "ecs": ("ecs", COMPUTE, 2), "eks": ("eks", COMPUTE, 2), "batch": ("batch", COMPUTE, 2),
    "apprunner": ("app_runner", COMPUTE, 2), "elasticbeanstalk": ("elastic_beanstalk", COMPUTE, 2),
    "states": ("step_functions", INTEGRATION, 3), "events": ("eventbridge", INTEGRATION, 3),
    "scheduler": ("eventbridge", INTEGRATION, 3), "mq": ("mq", INTEGRATION, 3),
    "kinesis": ("kinesis", "#8C4FFF", 3), "firehose": ("kinesis_data_firehose", "#8C4FFF", 3),
    "ses": ("simple_email_service", "#DD344C", 3),
    "elasticache": ("elasticache", DATABASE, 4), "redshift": ("redshift", "#8C4FFF", 4),
    "es": ("elasticsearch_service", "#8C4FFF", 4), "aoss": ("elasticsearch_service", "#8C4FFF", 4),
    "elasticfilesystem": ("elastic_file_system", STORAGE, 4), "backup": ("backup", STORAGE, 4),
    "glue": ("glue", "#8C4FFF", 4), "ecr": ("ecr", COMPUTE, 4),
    "sagemaker": ("sagemaker", "#01A88D", 2), "bedrock": ("bedrock", "#01A88D", 2),
    "cloudformation": ("cloudformation", "#E7157B", 5), "codebuild": ("codebuild", "#C925D1", 5),
    "codepipeline": ("codepipeline", "#C925D1", 5), "cloudwatch": ("cloudwatch_2", "#E7157B", 5),
}
UNKNOWN = ("rounded=1;whiteSpace=wrap;html=1;fillColor=#F2F3F3;strokeColor=#879196;"
           "fontColor=#232F3E;fontSize=10;verticalLabelPosition=bottom;verticalAlign=top;")
TYPE_ORDER = ["route53_zone", "cloudfront_distribution", "acm_certificate", "cognito_user_pool",
              "api_gateway", "load_balancer", "ec2_instance", "lambda_function", "sns_topic",
              "sqs_queue", "rds_instance", "dynamodb_table", "s3_bucket"]

_GROUP = ("fontSize=12;fontStyle=0;container=1;pointerEvents=0;collapsible=0;recursiveResize=0;"
          "html=1;whiteSpace=wrap;outlineConnect=0;gradientColor=none;verticalAlign=top;"
          "align=left;spacingLeft=30;dashed=0;shape=mxgraph.aws4.group;")
GROUPS = {
    "cloud": _GROUP + "grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;fontColor=#232F3E;",
    "region": _GROUP + "grIcon=mxgraph.aws4.group_region;strokeColor=#00A4A6;fillColor=none;fontColor=#147EBA;dashed=1;",
    "vpc": _GROUP + "grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;fillColor=none;fontColor=#AAB7B8;",
    "public": _GROUP + "grIcon=mxgraph.aws4.group_public_subnet;strokeColor=#7AA116;fillColor=#F2F6E8;fontColor=#248814;",
    "private": _GROUP + "grIcon=mxgraph.aws4.group_private_subnet;strokeColor=#00A4A6;fillColor=#E6F6F7;fontColor=#147EBA;",
    "az": ("fillColor=none;strokeColor=#147EBA;dashed=1;verticalAlign=top;fontStyle=0;"
           "fontColor=#147EBA;whiteSpace=wrap;html=1;container=1;collapsible=0;"
           "recursiveResize=0;align=center;"),
}
_BOX = ("container=1;collapsible=0;recursiveResize=0;html=1;whiteSpace=wrap;verticalAlign=top;"
        "align=left;spacingLeft=10;spacingTop=4;fontSize=12;fillColor=none;")
GROUPS["resource_group"] = _BOX + "strokeColor=#5A6C86;fontColor=#5A6C86;dashed=1;"
GROUPS["global"] = _BOX + "strokeColor=#879196;fontColor=#232F3E;dashed=0;fontStyle=1;"
EDGE_DESCRIBED = ("edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;html=1;endArrow=block;"
                  "endFill=1;strokeColor=#147EBA;dashed=1;fontSize=10;fontColor=#147EBA;"
                  "labelBackgroundColor=default;")
LEGEND = ("text;html=1;align=left;verticalAlign=top;fontSize=11;fontColor=#545B64;"
          "whiteSpace=wrap;strokeColor=none;fillColor=none;")
EDGE = ("edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;html=1;endArrow=block;"
        "endFill=1;strokeColor=#545B64;fontSize=10;")


class _Doc:
    def __init__(self):
        self.cells, self.ids = [], {}

    def cid(self, key):
        """Short, stable draw.io ids (ARNs are long and contain ':' and '/')."""
        if key not in self.ids:
            self.ids[key] = f"n{len(self.ids) + 1}"
        return self.ids[key]

    def vertex(self, key, label, style, parent, x, y, w, h):
        self.cells.append(
            f'<mxCell id="{self.cid(key)}" value="{escape(label, {chr(34): "&quot;"})}" '
            f'style="{style}" vertex="1" parent="{parent}">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return self.cid(key)

    def edge(self, src, dst, label="", style=None):
        n = len(self.cells)
        self.cells.append(
            f'<mxCell id="e{n}" value="{escape(label or "", {chr(34): "&quot;"})}" style="{style or EDGE}" '
            f'edge="1" parent="1" source="{src}" target="{dst}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>')


def _label(node):
    detail = node.get("detail")
    name = node["name"] if len(node["name"]) <= 28 else node["name"][:25] + "..."
    return f"{name}<br><font color=\"#545B64\">{detail}</font>" if detail else name


def _grid_size(count, cols=COLS):
    cols = max(1, min(count, cols))
    rows = max(1, -(-count // cols))
    return cols * CELL_W, rows * CELL_H


def _place_icons(doc, parent_id, items, x0, y0, cols=COLS):
    for i, node in enumerate(items):
        col, row = i % cols, i // cols
        x = x0 + col * CELL_W + (CELL_W - ICON) // 2
        y = y0 + row * CELL_H + 8
        doc.vertex(node["id"], _label(node), _style_for(node),
                   parent_id, x, y, ICON, ICON)


# Resources outside a VPC are laid out as a left-to-right flow, one column per role.
TIERS = {
    "route53_zone": 0, "cloudfront_distribution": 0, "acm_certificate": 0, "cognito_user_pool": 0,
    "api_gateway": 1, "load_balancer": 1,
    "lambda_function": 2, "ec2_instance": 2,
    "sns_topic": 3, "sqs_queue": 3,
    "dynamodb_table": 4, "rds_instance": 4, "s3_bucket": 4,
}
TIER_GAP = 60   # extra space between columns so arrows stay readable
# Messaging sits between compute and data. Shifting that column down half a row keeps
# straight compute -> data lines from running through topic and queue icons.
TIER_OFFSET = {3: CELL_H // 2}


TYPE_ICONS = {   # discovered types inside a service that need their own icon
    "ec2:transit-gateway": ("transit_gateway", NETWORK, 1),
    "ec2:vpc-endpoint": ("endpoints", NETWORK, 1),
    "ec2:vpc-peering-connection": ("peering", NETWORK, 1),
    "ec2:vpn-connection": ("site_to_site_vpn", NETWORK, 1),
}


def _icon_entry(node):
    return (TYPE_ICONS.get(f"{node.get('service', '')}:{node.get('resource_type', '')}")
            or SERVICE_ICONS.get(node.get("service", "")))


def _style_for(node):
    if node["type"] == "other":
        icon = _icon_entry(node)
        return _res_icon(icon[0], icon[1]) if icon else UNKNOWN
    return STYLES.get(node["type"], UNKNOWN)


def _tier(node):
    if node["type"] == "other":
        return (_icon_entry(node) or (None, None, 5))[2]
    return TIERS.get(node["type"], 5)


def _columns(items):
    cols = {}
    for n in items:
        cols.setdefault(_tier(n), []).append(n)
    return [_sorted(cols[t]) for t in sorted(cols)]


MAX_ROWS = 8   # a role column wraps into side-by-side sub-columns after this many items


def _columns_tiered(items):
    cols = {}
    for n in items:
        cols.setdefault(_tier(n), []).append(n)
    out = []
    for t in sorted(cols):
        col = _sorted(cols[t])
        for i in range(0, len(col), MAX_ROWS):
            out.append((t, col[i:i + MAX_ROWS]))
    return out


def _flow_size(items):
    cols = _columns_tiered(items)
    if not cols:
        return 0, 0
    return (len(cols) * CELL_W + (len(cols) - 1) * TIER_GAP,
            max(len(c) * CELL_H + TIER_OFFSET.get(t, 0) for t, c in cols))


def _place_flow(doc, parent_id, items, x0, y0):
    for ci, (tier, col) in enumerate(_columns_tiered(items)):
        x = x0 + ci * (CELL_W + TIER_GAP) + (CELL_W - ICON) // 2
        dy = TIER_OFFSET.get(tier, 0)
        for ri, node in enumerate(col):
            doc.vertex(node["id"], _label(node), _style_for(node),
                       parent_id, x, y0 + dy + ri * CELL_H + 8, ICON, ICON)


def _sorted(items):
    rank = {t: i for i, t in enumerate(TYPE_ORDER)}
    return sorted(items, key=lambda n: (rank.get(n["type"], 99), n["name"].lower()))


def _clusters(items):
    """Resource-group members get a titled box each; the rest share an untitled grid."""
    grouped, loose = {}, []
    for n in items:
        (grouped.setdefault(n["group"], []) if n.get("group") else loose).append(n)
    out = [(name, _sorted(grouped[name])) for name in sorted(grouped)]
    if loose:
        out.append((None, _sorted(loose)))
    return out


def _flow(clusters, max_w):
    """Place clusters left to right, wrapping at max_w. Returns placements, width, height."""
    placed, x, y, row_h, total_w = [], 0, 0, 0, 0
    for title, items in clusters:
        cols = None
        gw, gh = _flow_size(items)
        w, h = (gw + 2 * PAD, gh + HEADER + PAD // 2) if title else (gw, gh)
        if x and x + w > max_w:
            x, y, row_h = 0, y + row_h + PAD, 0
        placed.append((title, items, x, y, w, h, cols))
        x, row_h, total_w = x + w + PAD, max(row_h, h), max(total_w, x + w)
    return placed, total_w, (y + row_h if placed else 0)


def _draw_flow(doc, parent, placed, x0, y0, scope):
    for title, items, x, y, w, h, cols in placed:
        if title:
            box = doc.vertex(f"__rg:{scope}:{title}", f"Resource group: {title}",
                             GROUPS["resource_group"], parent, x0 + x, y0 + y, w, h)
            _place_flow(doc, box, items, PAD, HEADER)
        else:
            _place_flow(doc, parent, items, x0 + x, y0 + y)


def build_drawio(graph, title="AWS architecture", region="Region"):
    nodes = {n["id"]: n for n in graph["nodes"]}
    vpcs = [n for n in nodes.values() if n["type"] == "vpc"]
    subnets = [n for n in nodes.values() if n["type"] == "subnet"]
    resources = [n for n in nodes.values() if n["type"] not in ("vpc", "subnet")]

    def subnet_of(n):
        if n.get("subnet") in nodes:
            return n["subnet"]
        for s in n.get("subnets", []):  # load balancers span subnets: first public one wins
            if s in nodes and nodes[s].get("public"):
                return s
        return next((s for s in n.get("subnets", []) if s in nodes), None)

    in_subnet, vpc_level, regional, global_items = {}, {}, [], []
    for r in resources:
        sid = subnet_of(r)
        if r.get("is_global"):
            global_items.append(r)
        elif r["type"] in ("internet_gateway", "load_balancer") and r.get("vpc") in nodes:
            vpc_level.setdefault(r["vpc"], []).append(r)   # drawn on the VPC's top row
        elif sid:
            in_subnet.setdefault(sid, []).append(r)
        elif r.get("vpc") in nodes:
            vpc_level.setdefault(r["vpc"], []).append(r)
        else:
            regional.append(r)

    doc = _Doc()
    # ---- measure bottom-up, then draw top-down with relative coordinates ----
    def subnet_size(sn):
        w, h = _grid_size(len(in_subnet.get(sn["id"], [])) or 1)
        return w + 2 * PAD, h + HEADER + PAD // 2

    vpc_layout = []
    for vpc in sorted(vpcs, key=lambda v: v["name"]):
        azs = {}
        for sn in subnets:
            if sn["vpc"] == vpc["id"]:
                azs.setdefault(sn["az"], []).append(sn)
        columns = []
        for az in sorted(azs):
            stack = sorted(azs[az], key=lambda s: (not s["public"], s["name"]))
            sizes = [subnet_size(s) for s in stack]
            col_w = max(w for w, _ in sizes) + 2 * PAD
            col_h = HEADER + sum(h for _, h in sizes) + PAD * len(sizes)
            columns.append((az, stack, sizes, col_w, col_h))
        top = vpc_level.get(vpc["id"], [])
        top_h = CELL_H + PAD if top else 0
        cols_w = sum(c[3] for c in columns) + PAD * max(0, len(columns) - 1)
        top_w = _grid_size(len(top), cols=8)[0] if top else 0
        vpc_w = max(cols_w, top_w, 2 * CELL_W) + 2 * PAD
        vpc_h = HEADER + top_h + (max((c[4] for c in columns), default=0)) + PAD
        vpc_layout.append((vpc, top, columns, vpc_w, vpc_h, top_h))

    vpcs_w = sum(v[3] for v in vpc_layout) + PAD * max(0, len(vpc_layout) - 1)
    vpcs_h = max((v[4] for v in vpc_layout), default=0)
    flow_max = max(vpcs_w, 5 * CELL_W + 4 * TIER_GAP)
    reg_placed, reg_flow_w, reg_flow_h = _flow(_clusters(regional), flow_max)
    region_w = max(vpcs_w, reg_flow_w, 3 * CELL_W) + 2 * PAD
    gap = PAD if (vpcs_h and reg_flow_h) else 0
    region_h = HEADER + vpcs_h + gap + reg_flow_h + PAD
    glob_placed, _, glob_flow_h = _flow(_clusters(global_items), region_w - 2 * PAD)
    global_h = HEADER + glob_flow_h + PAD if global_items else 0

    cloud_h = HEADER + region_h + (PAD + global_h if global_items else 0) + PAD
    cloud = doc.vertex("__cloud", "AWS Cloud", GROUPS["cloud"], "1", 20, 20,
                       region_w + 2 * PAD, cloud_h)
    reg = doc.vertex("__region", region, GROUPS["region"], cloud, PAD, HEADER, region_w, region_h)
    if global_items:
        glob = doc.vertex("__global", "Global services", GROUPS["global"], cloud,
                          PAD, HEADER + region_h + PAD, region_w, global_h)
        _draw_flow(doc, glob, glob_placed, PAD, HEADER, "global")

    x = PAD
    for vpc, top, columns, vpc_w, vpc_h, top_h in vpc_layout:
        label = f"{vpc['name']}<br><font color=\"#545B64\">{vpc.get('cidr', '')}</font>"
        vid = doc.vertex(vpc["id"], label, GROUPS["vpc"], reg, x, HEADER, vpc_w, vpc_h)
        if top:
            _place_icons(doc, vid, top, PAD, HEADER, cols=8)
        cx = PAD
        for az, stack, sizes, col_w, col_h in columns:
            aid = doc.vertex(f"__az:{vpc['id']}:{az}", az, GROUPS["az"], vid,
                             cx, HEADER + top_h, col_w, col_h)
            sy = HEADER
            for sn, (sw, sh) in zip(stack, sizes):
                kind = "public" if sn["public"] else "private"
                slabel = f"{sn['name']}<br><font color=\"#545B64\">{sn.get('cidr', '')}</font>"
                sid = doc.vertex(sn["id"], slabel, GROUPS[kind], aid, PAD, sy, sw, sh)
                _place_icons(doc, sid, in_subnet.get(sn["id"], []), PAD, HEADER)
                sy += sh + PAD
            cx += col_w + PAD
        x += vpc_w + PAD

    _draw_flow(doc, reg, reg_placed, PAD, HEADER + vpcs_h + gap, "region")

    def ports(src, dst):
        """Lines that move along the flow leave from the side and turn in the gap between
        columns, instead of running through the icons stacked in the target column."""
        a, b = nodes.get(src), nodes.get(dst)
        if not a or not b or a.get("is_global") != b.get("is_global"):
            return ""
        ta, tb = _tier(a), _tier(b)
        if tb > ta:
            return "exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
        if tb < ta:
            return "exitX=0;exitY=0.5;exitDx=0;exitDy=0;entryX=1;entryY=0.5;entryDx=0;entryDy=0;"
        return ""

    described = 0
    for e in graph.get("edges", []):
        if e["from"] in doc.ids and e["to"] in doc.ids:
            side = ports(e["from"], e["to"])
            if e.get("kind") == "described":
                described += 1
                doc.edge(doc.ids[e["from"]], doc.ids[e["to"]], e.get("label", ""), EDGE_DESCRIBED + side)
            else:
                doc.edge(doc.ids[e["from"]], doc.ids[e["to"]], e.get("label", ""), EDGE + side)
    others = sum(1 for n in resources if n["type"] == "other")
    notes = []
    if described:
        notes.append("Solid lines: found in the AWS account. "
                     "Dashed blue lines: from the customer's flow description.")
    if others:
        notes.append(f"{others} resource{'s' if others != 1 else ''} "
                     "were discovered without connection details (no lines drawn).")
    if notes:
        doc.vertex("__legend", " ".join(notes), LEGEND, "1", 20, 20 + cloud_h + 10,
                   region_w + 2 * PAD, 30)

    body = "".join(doc.cells)
    return (f'<mxfile host="inventory2drawio"><diagram id="arch" name="{escape(title)}">'
            f'<mxGraphModel grid="1" gridSize="10" page="0"><root>'
            f'<mxCell id="0"/><mxCell id="1" parent="0"/>{body}'
            f'</root></mxGraphModel></diagram></mxfile>')
