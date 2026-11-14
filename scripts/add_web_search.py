"""Add the built-in Web Search connector to the Gateway, limited to AWS's own sites.

  python scripts/add_web_search.py --gateway-id <id> --region us-east-1

Before running, add this to the GATEWAY service role (not the Runtime role):
  {"Effect": "Allow", "Action": "bedrock-agentcore:InvokeWebSearch",
   "Resource": "arn:aws:bedrock-agentcore:<region>:aws:tool/web-search.v1"}
"""
import argparse

import boto3

ALLOWED_DOMAINS = ["docs.aws.amazon.com", "aws.amazon.com"]


def target_request(gateway_id, domains=ALLOWED_DOMAINS):
    return dict(
        gatewayIdentifier=gateway_id,
        name="web-search",   # the agent sees the tool as web-search___WebSearch
        description="Web search restricted to AWS documentation and aws.amazon.com",
        targetConfiguration={"mcp": {"connector": {
            # 1.2.0 is the first version with a target-level include list.
            "source": {"connectorId": "web-search", "version": "1.2.0"},
            "configurations": [{
                "name": "WebSearch",
                "parameterValues": {"domainFilter": {"include": domains}},
            }],
        }}},
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway-id", required=True)
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()
    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    out = client.create_gateway_target(**target_request(args.gateway_id))
    print("created target", out["targetId"], "status", out.get("status"))
    print("searches are limited to:", ", ".join(ALLOWED_DOMAINS))


if __name__ == "__main__":
    main()
