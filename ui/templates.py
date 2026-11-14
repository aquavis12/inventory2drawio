"""Ready-made prompts. Each fills in from the sidebar, so people don't have to know
how to phrase a request. `needs` names the sidebar field a template depends on."""

TEMPLATES = [
    {"label": "Whole Region",
     "prompt": "Draw everything in this Region.",
     "needs": None},
    {"label": "App with request flow",
     "prompt": "Draw the application{scope} and explain how a request travels through it, "
               "from the entry point to where data is stored.",
     "needs": None},
    {"label": "Use my flow description",
     "prompt": "Draw the application{scope}. Add the steps from my flow description as "
               "connections, and tell me which steps you couldn't match.",
     "needs": "flow"},
    {"label": "Network only",
     "prompt": "Draw the network{scope}: VPCs, subnets, gateways, load balancers and what "
               "runs in each subnet. Leave out serverless resources that sit outside a VPC.",
     "needs": None},
    {"label": "Serverless only",
     "prompt": "Draw the serverless parts{scope}: CloudFront, API Gateway, Lambda, queues, "
               "topics, tables and buckets, with how they connect.",
     "needs": None},
    {"label": "Review risks",
     "prompt": "Draw the application{scope}, then give me up to four things worth a look, "
               "with an AWS documentation link for each where you can find one.",
     "needs": None},
    {"label": "Tagged + connected",
     "prompt": "Draw the application{scope}, and include the untagged resources directly "
               "connected to it.",
     "needs": "scope"},
    {"label": "Only some services",
     "prompt": "Draw only the S3 buckets{scope}. Nothing else.",
     "needs": None},
    {"label": "Only some services",
     "prompt": "Draw only the S3 buckets{scope}. Nothing else.",
     "needs": None},
    {"label": "Include routine services",
     "prompt": "Draw the application{scope}, and include KMS keys, log groups and SSM "
               "parameters this time.",
     "needs": None},
]


def scope_phrase(tag_key="", tag_value="", resource_group="", vpc_ids=""):
    """Short phrase like ' in resource group lms-study-site'. Empty when no scope."""
    if resource_group.strip():
        return f" in resource group {resource_group.strip()}"
    if tag_key.strip():
        value = tag_value.strip()
        return f" tagged {tag_key.strip()}={value}" if value else f" tagged {tag_key.strip()}"
    if vpc_ids.strip():
        return f" in {vpc_ids.strip()}"
    return " in this Region"


def fill(template, has_flow, **scope):
    """Returns (prompt, None) or (None, reason it can't be used yet)."""
    if template["needs"] == "flow" and not has_flow:
        return None, "Upload or paste a flow description in the sidebar first."
    if template["needs"] == "scope" and not (scope.get("tag_key", "").strip()
                                             or scope.get("resource_group", "").strip()):
        return None, "Set a tag or a resource group in the sidebar first."
    return template["prompt"].format(scope=scope_phrase(**scope)), None
