"""Resource graph -> draw.io XML with AWS icons and a deterministic layout.

Layout is code, not LLM: models are bad at coordinates and produce overlapping
boxes. Nesting: AWS Cloud > Region > VPC > Availability Zone > subnet > resources.
Regional services (S3, DynamoDB, SQS, Lambda outside a VPC) sit in a row below.
"""
import bisect
import heapq
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
GROUPS["connected"] = _BOX + "strokeColor=#B0B7C0;fontColor=#879196;dashed=1;dashPattern=2 4;"
GROUPS["global"] = _BOX + "strokeColor=#879196;fontColor=#232F3E;dashed=0;fontStyle=1;"
EDGE_DESCRIBED = ("edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;html=1;endArrow=block;"
                  "endFill=1;strokeColor=#147EBA;dashed=1;fontSize=10;fontColor=#147EBA;"
                  "labelBackgroundColor=default;")
LEGEND = ("text;html=1;align=left;verticalAlign=top;fontSize=11;fontColor=#545B64;"
          "whiteSpace=wrap;strokeColor=none;fillColor=none;")
EDGE = ("edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;html=1;endArrow=block;"
        "endFill=1;strokeColor=#545B64;fontSize=10;")


HALF_W = 70          # an icon plus its label blocks this far left and right of its centre
LABEL_H = 44         # and this far below the icon
LANE = 5             # lines run this far outside an icon's blocked area
BEND_COST = 40
MAX_ROUTED_ICONS = 70


def _route_edges(icons, pairs):
    """Orthogonal routes that go around icons and labels instead of through them.

    icons: {node id: (x, y)} top-left corners of the ICON x ICON squares, absolute.
    pairs: [(from id, to id)]. Returns {(from, to): [(x, y) corner points]} for the edges it
    could route; an edge missing from the result keeps draw.io's automatic routing. Lines
    leave one side of an icon, run along free lanes between icons and rows, and enter the
    side facing the source. Pure geometry: no AWS, no model."""
    if not pairs or len(icons) > MAX_ROUTED_ICONS:
        return {}
    c = ICON / 2
    obs = {k: (x + c - HALF_W, y - 2, x + c + HALF_W, y + ICON + LABEL_H) for k, (x, y) in icons.items()}
    cen = {k: (x + c, y + c) for k, (x, y) in icons.items()}
    xs_, ys_ = set(), set()
    for k, (x0, y0, x1, y1) in obs.items():
        cx, cy = cen[k]
        xs_.update((x0 - LANE, x1 + LANE, cx - c, cx + c))
        ys_.update((y0 - LANE, y1 + LANE, cy))
    xs_.update((min(v[0] for v in obs.values()) - 40, max(v[2] for v in obs.values()) + 40))
    ys_.update((min(v[1] for v in obs.values()) - 40, max(v[3] for v in obs.values()) + 40))
    xs, ys = sorted({round(v) for v in xs_}), sorted({round(v) for v in ys_})
    xi, yi = {v: i for i, v in enumerate(xs)}, {v: i for i, v in enumerate(ys)}

    hb, vb = {}, {}     # segment -> icons it would cross
    for k, (x0, y0, x1, y1) in obs.items():
        i0, i1 = max(0, bisect.bisect_right(xs, x0) - 1), bisect.bisect_left(xs, x1)
        j0, j1 = max(0, bisect.bisect_right(ys, y0) - 1), bisect.bisect_left(ys, y1)
        for j in range(bisect.bisect_right(ys, y0), bisect.bisect_left(ys, y1)):
            for i in range(i0, i1):
                hb.setdefault((j, i), set()).add(k)
        for i in range(bisect.bisect_right(xs, x0), bisect.bisect_left(xs, x1)):
            for j in range(j0, j1):
                vb.setdefault((i, j), set()).add(k)

    DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))     # right, left, down, up

    def route(a, b):
        (ax, ay), (bx, by) = cen[a], cen[b]
        if bx > ax + 1:
            out_d, in_d = 0, 0           # leave right, enter the target's left side
        elif bx < ax - 1:
            out_d, in_d = 1, 1
        else:
            out_d, in_d = 0, 1           # same column: leave right, come back into the right side
        sx = ax + (c if out_d == 0 else -c)
        gx = bx + (-c if in_d == 0 else c)
        start, goal = (xi[round(sx)], yi[round(ay)]), (xi[round(gx)], yi[round(by)])

        def blocked(seg_set, horizontal, lane_y):
            for k in seg_set:
                if k != a and k != b:
                    return True
                if not (horizontal and round(cen[k][1]) == lane_y):
                    return True       # own icon: only the straight exit/entry lane is free
            return False

        h = lambda i, j: abs(xs[i] - xs[goal[0]]) + abs(ys[j] - ys[goal[1]])
        best = {(start, out_d): 0}
        prev = {}
        heap = [(h(*start), 0, start, out_d, True)]
        end = None
        while heap:
            _, cost, (i, j), d, first = heapq.heappop(heap)
            if best.get(((i, j), d), 1e18) < cost:
                continue
            if (i, j) == goal and d == in_d:
                end = ((i, j), d)
                break
            for nd, (dx, dy) in enumerate(DIRS):
                if first and nd != out_d:
                    continue
                if (dx, dy) == (-DIRS[d][0], -DIRS[d][1]):
                    continue                      # no U-turns
                ni, nj = i + dx, j + dy
                if not (0 <= ni < len(xs) and 0 <= nj < len(ys)):
                    continue
                if (ni, nj) == goal and nd != in_d:
                    continue
                if dy == 0:
                    seg = hb.get((j, min(i, ni)), ())
                    if seg and blocked(seg, True, ys[j]):
                        continue
                    step = abs(xs[ni] - xs[i])
                else:
                    seg = vb.get((i, min(j, nj)), ())
                    if seg and blocked(seg, False, 0):
                        continue
                    step = abs(ys[nj] - ys[j])
                ncost = cost + step + (BEND_COST if (nd != d and not first) else 0)
                key = ((ni, nj), nd)
                if ncost < best.get(key, 1e18):
                    best[key] = ncost
                    prev[key] = ((i, j), d)
                    heapq.heappush(heap, (ncost + h(ni, nj), ncost, (ni, nj), nd, False))
        if end is None:
            return None
        path, key = [], end
        while key in prev or key == (start, out_d):
            path.append(key[0])
            if key not in prev:
                break
            key = prev[key]
        path.reverse()
        pts = [(xs[i], ys[j]) for i, j in path]
        corners = [p for n, p in enumerate(pts[1:-1], 1)
                   if (pts[n - 1][0] == p[0]) != (pts[n + 1][0] == p[0])
                   or (pts[n - 1][1] == p[1]) != (pts[n + 1][1] == p[1])]
        return corners

    out = {}
    for a, b in pairs:
        if a in icons and b in icons and a != b:
            r = route(a, b)
            if r is not None:
                out[(a, b)] = r
    return out


def _points_xml(points):
    if not points:
        return ""
    return ('<Array as="points">'
            + "".join(f'<mxPoint x="{round(x)}" y="{round(y)}"/>' for x, y in points) + "</Array>")


def _straight(style):
    """Routed lines are drawn exactly through their points, so drop draw.io's own routing."""
    return style.replace("edgeStyle=orthogonalEdgeStyle;", "").replace("orthogonalLoop=1;", "")


class _Doc:
    def __init__(self):
        self.cells, self.ids = [], {}
        self.abs, self.icons = {}, {}   # absolute positions, for routing lines around icons

    def cid(self, key):
        """Short, stable draw.io ids (ARNs are long and contain ':' and '/')."""
        if key not in self.ids:
            self.ids[key] = f"n{len(self.ids) + 1}"
        return self.ids[key]

    def vertex(self, key, label, style, parent, x, y, w, h):
        bx, by = self.abs.get(parent, (0, 0))
        self.abs[self.cid(key)] = (bx + x, by + y)
        if w == ICON and h == ICON:
            self.icons[key] = (bx + x, by + y)
        self.cells.append(
            f'<mxCell id="{self.cid(key)}" value="{escape(label, {chr(34): "&quot;"})}" '
            f'style="{style}" vertex="1" parent="{parent}">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return self.cid(key)

    def edge(self, src, dst, label="", style=None, points=None):
        n = len(self.cells)
        self.cells.append(
            f'<mxCell id="e{n}" value="{escape(label or "", {chr(34): "&quot;"})}" style="{style or EDGE}" '
            f'edge="1" parent="1" source="{src}" target="{dst}">'
            f'<mxGeometry relative="1" as="geometry">{_points_xml(points)}</mxGeometry></mxCell>')


def _wrap_name(name, width=24, limit=56):
    """Long names break after a hyphen, dot or dash onto a second line instead of being cut
    to '...'. Only names past `limit` characters are shortened."""
    if len(name) > limit:
        name = name[:limit - 3] + "..."
    if len(name) <= width:
        return name
    cut = max((name.rfind(c, 0, width + 1) for c in "-._/"), default=-1)
    cut = cut + 1 if cut >= width // 2 else width
    return f"{name[:cut]}<br>{name[cut:]}"


def _label(node):
    detail = node.get("detail")
    name = _wrap_name(node["name"])
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


CONNECTED = "\0connected"   # cluster key for resources outside the scope but wired to it


def _clusters(items):
    """Resource-group members get a titled box each; the rest share an untitled grid."""
    grouped, loose, connected = {}, [], []
    for n in items:
        if n.get("group"):
            grouped.setdefault(n["group"], []).append(n)
        elif n.get("included_because"):
            connected.append(n)     # pulled in by a connection, not matched by the scope
        else:
            loose.append(n)
    out = [(name, _sorted(grouped[name])) for name in sorted(grouped)]
    if connected:
        out.append((CONNECTED, _sorted(connected)))
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
            if title == CONNECTED:
                box = doc.vertex(f"__rg:{scope}:connected", "Connected, not in scope (untagged)",
                                 GROUPS["connected"], parent, x0 + x, y0 + y, w, h)
            else:
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

    drawn = [e for e in graph.get("edges", []) if e["from"] in doc.ids and e["to"] in doc.ids]
    routes = _route_edges(doc.icons, [(e["from"], e["to"]) for e in drawn])
    described = 0
    for e in drawn:
        base = EDGE_DESCRIBED if e.get("kind") == "described" else EDGE
        described += e.get("kind") == "described"
        route = routes.get((e["from"], e["to"]))
        if route is not None:
            doc.edge(doc.ids[e["from"]], doc.ids[e["to"]], e.get("label", ""), _straight(base), route)
        else:                     # no free route found: let draw.io route it
            doc.edge(doc.ids[e["from"]], doc.ids[e["to"]], e.get("label", ""),
                     base + ports(e["from"], e["to"]))
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
