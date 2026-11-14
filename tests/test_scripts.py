"""Check the setup scripts build requests the AWS API model accepts (no AWS calls)."""
import os, sys
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import boto3
from botocore.stub import Stubber
from botocore.validate import validate_parameters
import add_web_search, add_policy

client = boto3.client("bedrock-agentcore-control")
model = client.meta.service_model

def check(op, params):
    validate_parameters(params, model.operation_model(op).input_shape)

check("CreateGatewayTarget", add_web_search.target_request("gw-123"))

arn = "arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/gw-123"
pols = add_policy.render_policies(arn, "111122223333", "AgentRuntimeRole", ["us-east-1", "me-central-1"])
assert list(pols) == ["collect_inventory", "render_drawio", "web_search"], list(pols)
for name, text in pols.items():
    assert "{{" not in text and arn in text
    assert text.count("(") == text.count(")") and text.count("{") == text.count("}"), name
    assert "arn:aws:sts::111122223333:assumed-role/AgentRuntimeRole/*" in text
    check("CreatePolicy", dict(policyEngineId="inv2drawio_policies-a1b2c3d4e5", name=name,
                               definition={"cedar": {"statement": text}}, validationMode="FAIL_ON_ANY_FINDINGS"))
assert '["us-east-1", "me-central-1"].contains(context.input.region)' in pols["collect_inventory"]
check("CreatePolicyEngine", dict(name="inv2drawio_policies", description="x"))
check("UpdateGateway", dict(gatewayIdentifier="gw-123", name="g", roleArn="arn:aws:iam::111122223333:role/r",
                            authorizerType="AWS_IAM", protocolType="MCP",
                            policyEngineConfiguration={"arn": "arn:aws:bedrock-agentcore:us-east-1:111122223333:policy-engine/inv2drawio_policies-a1b2c3d4e5", "mode": "LOG_ONLY"}))
try:
    add_policy.render_policies(arn, "1", "r", ['us-east-1"] || true || ["x'])
    raise SystemExit("bad Region accepted")
except ValueError:
    pass
print("setup scripts: all tests passed")
