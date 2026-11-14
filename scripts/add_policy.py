"""Create a Cedar policy engine for the Gateway and attach it.

  python scripts/add_policy.py --gateway-id <id> --runtime-role-name <role> \
      --allowed-regions us-east-1,me-central-1 --mode LOG_ONLY

Start with LOG_ONLY: decisions are logged but nothing is blocked. Check the logs,
then run again with --mode ENFORCE. Once enforced, anything without a matching
permit is denied, so create all three targets before you enforce.
"""
import argparse
import pathlib
import re
import time

import boto3

POLICY_DIR = pathlib.Path(__file__).resolve().parent.parent / "policies"


def render_policies(gateway_arn, account_id, runtime_role_name, regions):
    if not all(re.match(r"^[a-z]{2}(-[a-z]+)+-\d$", r) for r in regions):
        raise ValueError(f"bad Region in {regions}")
    values = {
        "{{GATEWAY_ARN}}": gateway_arn,
        # An assumed role shows up as arn:aws:sts::<acct>:assumed-role/<role>/<session>
        "{{RUNTIME_ROLE_PATTERN}}": f"arn:aws:sts::{account_id}:assumed-role/{runtime_role_name}/*",
        "{{ALLOWED_REGIONS}}": ", ".join(f'"{r}"' for r in regions),
    }
    out = {}
    for path in sorted(POLICY_DIR.glob("*.cedar")):
        text = path.read_text()
        for key, val in values.items():
            text = text.replace(key, val)
        if "{{" in text:
            raise ValueError(f"unfilled placeholder in {path.name}")
        # Policy names: letters, digits, underscore.
        out[re.sub(r"^\d+_", "", path.stem)] = text
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway-id", required=True)
    ap.add_argument("--runtime-role-name", required=True, help="the Runtime execution role's name, not ARN")
    ap.add_argument("--allowed-regions", default="us-east-1")
    ap.add_argument("--mode", choices=["LOG_ONLY", "ENFORCE"], default="LOG_ONLY")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--engine-id", help="reuse an existing policy engine (to switch mode)")
    args = ap.parse_args()

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    account = boto3.client("sts", region_name=args.region).get_caller_identity()["Account"]
    gw = client.get_gateway(gatewayIdentifier=args.gateway_id)

    if args.engine_id:
        engine = client.get_policy_engine(policyEngineId=args.engine_id)
    else:
        engine = client.create_policy_engine(
            name="inv2drawio_policies",
            description="What the inventory-to-diagram agent may call through the Gateway")
        while client.get_policy_engine(policyEngineId=engine["policyEngineId"])["status"] == "CREATING":
            time.sleep(3)
        policies = render_policies(gw["gatewayArn"], account, args.runtime_role_name,
                                   args.allowed_regions.split(","))
        for name, statement in policies.items():
            client.create_policy(
                policyEngineId=engine["policyEngineId"], name=name,
                definition={"cedar": {"statement": statement}},
                # Fail now on a typo'd tool name or field rather than deny silently later.
                validationMode="FAIL_ON_ANY_FINDINGS")
            print("created policy", name)

    # update_gateway replaces the config, so pass the existing settings through.
    keep = {k: gw[k] for k in ("name", "roleArn", "authorizerType", "protocolType", "description",
                               "authorizerConfiguration", "protocolConfiguration", "kmsKeyArn")
            if gw.get(k)}
    client.update_gateway(
        gatewayIdentifier=args.gateway_id, **keep,
        policyEngineConfiguration={"arn": engine["policyEngineArn"], "mode": args.mode})
    print(f"policy engine {engine['policyEngineId']} attached in {args.mode} mode")
    if args.mode == "LOG_ONLY":
        print("next: run the agent, check the decisions in the Gateway's logs, then re-run with")
        print(f"  --engine-id {engine['policyEngineId']} --mode ENFORCE")


if __name__ == "__main__":
    main()
