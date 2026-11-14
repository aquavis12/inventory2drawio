# Manual setup checklist

Everything you create by hand, in order. Use one Region throughout (below: us-east-1)
and a sandbox account. Tick each box; steps 1 to 3 are also in README.md.

## 1. S3 work bucket
- [ ] Bucket `inv2drawio-work-<ACCOUNT_ID>`, Block Public Access on
- [ ] Lifecycle rule: expire all objects after 7 days (inventory describes your network)

Ready-made Lambda zips are in the project root: `lambda-collect-inventory.zip` and
`lambda-render-drawio.zip` (upload with "Upload from .zip file").

## 1b. Discovery: nothing to turn on

Besides the ~20 types it maps in detail, the collector finds other resources in three ways,
in this order, and says in its notes which one it used:

1. **AWS Resource Explorer**, if the account already has it on (most complete).
2. Otherwise **Cloud Control**, which lists 30 common types (Secrets Manager, Step Functions,
   ECS, EKS, EventBridge, Kinesis, Aurora clusters, OpenSearch, EFS, Bedrock agents, ...)
   with no setup in the account.
3. Plus the **tagging API**, which adds tags and any other tagged resource.

A type the role may not list is skipped and named in the notes; the rest still works.

## 2. Lambda `inv2d-collect-inventory`
- [ ] Python 3.12, handler `handler.lambda_handler`, timeout 120 s, memory 512 MB
- [ ] Code: `tools/collect_inventory/handler.py`
- [ ] Environment variable `WORK_BUCKET` = the bucket name
- [ ] Role: `AWSLambdaBasicExecutionRole` + `tools/collect_inventory/iam-policy.json`
      + `s3:PutObject` on `arn:aws:s3:::<bucket>/inventory/*` (customer access: see step 9)

## 3. Lambda `inv2d-render-drawio`
- [ ] Python 3.12, handler `handler.lambda_handler`, timeout 30 s, memory 256 MB
- [ ] Code: `tools/render_drawio/handler.py` **and** `generator.py` in the same zip
- [ ] Environment variable `WORK_BUCKET` = the bucket name
- [ ] Role: `AWSLambdaBasicExecutionRole` + `s3:GetObject` on `<bucket>/inventory/*`
      + `s3:PutObject` and `s3:GetObject` on `<bucket>/diagrams/*`
      (GetObject on diagrams is what makes the download link work)

**Test both now** with the invoke commands in README.md section 3 before going further.

## 4. Gateway role `inv2d-gateway-role`
- [ ] Trust policy principal: `bedrock-agentcore.amazonaws.com`
- [ ] Permission: `lambda:InvokeFunction` on the two function ARNs only

## 5. AgentCore Gateway (console: Bedrock AgentCore > Gateways > Create)
- [ ] Name `inv2drawio-gateway`
- [ ] Inbound auth: **IAM** (not JWT). The agent signs requests with its own role
- [ ] Service role: `inv2d-gateway-role`
- [ ] Target 1: type Lambda, name `collect-inventory`, the collect function ARN,
      inline schema = contents of `tools/collect_inventory/tool_schema.json`,
      outbound auth = Gateway IAM role
- [ ] Target 2: type Lambda, name `render-drawio`, the render function ARN,
      inline schema = contents of `tools/render_drawio/tool_schema.json`
- [ ] Copy the **Gateway URL** (ends in `/mcp`)

Target names matter: the agent sees tools as `collect-inventory___collect_inventory`.

## 6. Model access
- [ ] In Bedrock, confirm access to the Claude model you'll use
- [ ] Copy its **inference profile ID** from Bedrock > Inference profiles. This is `MODEL_ID`

## 7. Deploy the agent to AgentCore Runtime
```bash
pip install bedrock-agentcore-starter-toolkit
cd agent
agentcore configure -e agent.py -n inv2drawio -rf requirements.txt -r us-east-1 -dm
agentcore deploy --env GATEWAY_URL=<gateway-url> --env MODEL_ID=<inference-profile-id>
```
`-dm` turns Memory off: this agent doesn't need it. The toolkit creates the Runtime's
execution role, an ECR repo and builds the image with CodeBuild (no local Docker needed).

- [ ] Add to the **Runtime execution role** (the toolkit prints its name):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "bedrock-agentcore:InvokeGateway",
     "Resource": "arn:aws:bedrock-agentcore:us-east-1:<ACCOUNT_ID>:gateway/<gateway-id>"}
  ]
}
```
Without this the agent answers but never calls a tool. This is the most common failure.

- [ ] Smoke test: `agentcore invoke '{"prompt": "Draw everything in us-east-1"}'`
- [ ] Copy the **agent runtime ARN** from `agentcore status`

### 7b. Alternative: deploy as a zip (no CLI, no Docker)

- [ ] Upload `inv2drawio-agent.zip` (or build it: `bash scripts/build_agent_zip.sh`) to
      `s3://<bucket>/agent-code/inv2drawio-agent.zip`
- [ ] Create the execution role yourself (trust: `bedrock-agentcore.amazonaws.com`) with
      Bedrock model invoke, CloudWatch Logs, `s3:GetObject` on the code zip and skills,
      and `bedrock-agentcore:InvokeGateway`
- [ ] AgentCore > Runtime > Host agent: code from S3, Python 3.12, entry point `agent.py`,
      environment variables `GATEWAY_URL`, `MODEL_ID`, `WORK_BUCKET`, network mode Public

## 8. Streamlit UI (runs on your laptop)
```bash
pip install streamlit boto3
export AGENT_RUNTIME_ARN=<agent-runtime-arn>
streamlit run ui/app.py
```
- [ ] Your local AWS identity needs `bedrock-agentcore:InvokeAgentRuntime` on the runtime ARN
      and `s3:GetObject` on `<bucket>/diagrams/*` (the UI downloads the diagram itself,
      so no link ever passes through the model)

### Prompt templates

Above the chat there are ready-made prompts (whole Region, app with request flow, use my
flow description, network only, serverless only, review risks, include routine services).
Pick one, check the filled-in text, click **Send this prompt**. They fill in the resource
group, tag or VPC from the sidebar. Edit or add templates in `ui/templates.py`.

## 9. Customers (profile + skill)

Each customer is a folder in the work bucket:

```
customers/<customer-id>/profile.json   access config: read ONLY by the collector Lambda
customers/<customer-id>/skill.md       guidance for the agent: tags, workloads, what to hide
```

The agent never sees `profile.json`. It passes only the customer ID; the Lambda looks up
the role itself and refuses Regions that aren't in `allowed_regions`. Templates are in
`customers/example-acme/`.

**One-time changes on your side**
- [ ] Collector role: add `s3:GetObject` on `arn:aws:s3:::<bucket>/customers/*/profile.json`
      and `sts:AssumeRole` on `arn:aws:iam::*:role/inv2drawio-readonly`
- [ ] Runtime execution role: add `s3:GetObject` on `arn:aws:s3:::<bucket>/customers/*/skill.md`
- [ ] Redeploy the agent with the bucket: `agentcore deploy --env GATEWAY_URL=... --env MODEL_ID=... --env WORK_BUCKET=<bucket>`
- [ ] Turn on S3 versioning for the bucket so every profile and skill change is tracked
- [ ] For production, set `ALLOW_OWN_ACCOUNT=false` on the collector so a customer is always required

**Per customer**
- [ ] Pick an ID: lowercase letters, digits, hyphens (for example `acme-retail`)
- [ ] Generate a long random external ID (for example `python -c "import secrets;print(secrets.token_urlsafe(32))"`)
- [ ] In the **customer's account**, deploy `customers/customer-role.yaml` with your collector
      role ARN and that external ID. It creates the read-only role `inv2drawio-readonly`
- [ ] Fill in `profile.json` (account ID, role ARN, external ID, allowed Regions) and upload it
- [ ] Write `skill.md` (how they tag, which workloads exist, what to hide) and upload it
- [ ] In the UI, set **Work bucket**; the customer appears in the dropdown within 5 minutes

Your local identity needs `s3:ListBucket` and `s3:GetObject` on `customers/*` for the dropdown.

## 10. AgentCore Memory (optional, works with any model)

Remembers conversations across sessions and learns per-customer preferences and facts.
Memory is kept separately per customer: the customer ID is the memory's actor.

- [ ] AgentCore > Memory > Create memory, name `inv2drawio_memory`
- [ ] Add two long-term strategies with **exactly** these namespaces:
  - User preferences: `/customers/{actorId}/preferences`
  - Semantic (facts): `/customers/{actorId}/facts`
- [ ] Copy the **Memory ID**
- [ ] Runtime role: allow `bedrock-agentcore:CreateEvent`, `bedrock-agentcore:ListEvents`,
      `bedrock-agentcore:GetEvent` and `bedrock-agentcore:RetrieveMemoryRecords` on
      `arn:aws:bedrock-agentcore:us-east-1:<account>:memory/<memory-id>`
- [ ] Runtime: add environment variable `MEMORY_ID=<memory-id>` and save
- [ ] Check CloudWatch for `memory: <memory-id>` at startup

Test: in one session tell the agent a preference ("for this account, always hide the
default VPC"). Wait a minute or two (long-term memory is extracted in the background),
click **New conversation** in the UI and ask for a diagram: it should follow the preference.

Memory stores conversation text, which here includes resource names. Treat the Memory like
the work bucket: same account, same access controls. Tool results (inventory, links) are
not restored into later conversations.

## Verify it works

1. Collector: invoke it directly (README section 3). Expect `"ok": true`, a `summary.nodes`
   count and a `findings` list.
2. Scoping: in the UI ask "draw only my S3 buckets". The diagram must contain buckets and
   nothing else. Ask "hi": the agent must answer in text with no "Reading the account" step.
3. Tag scope: set tag `app` = your value. Only matching resources appear; untagged resources
   wired to them appear only if you ask for connected ones, in a dashed "Connected, not in
   scope" box.
4. Download the `.drawio` and open it at app.diagrams.net. Names should wrap, not end in "...".
5. Logs: CloudWatch shows `collect_inventory` then `render_drawio` for a drawing request,
   and no tool calls for a greeting.

## Clean up

Delete in this order (replace the names and IDs with yours). Nothing was created in customer
accounts except the read-only role, which `customers/customer-role.yaml` owns.

```bash
# AgentCore: runtime, gateway targets then gateway, memory, policy engine
aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id <runtime-id>
aws bedrock-agentcore-control delete-gateway-target --gateway-identifier <gateway-id> --target-id <target-id>
aws bedrock-agentcore-control delete-gateway --gateway-identifier <gateway-id>
aws bedrock-agentcore-control delete-memory --memory-id <memory-id>
# Lambda functions
aws lambda delete-function --function-name inv2d-collect-inventory
aws lambda delete-function --function-name inv2d-render-drawio
# Buckets (this deletes the inventories, diagrams and customer profiles in them)
aws s3 rb s3://<work-bucket> --force
aws s3 rb s3://<agent-code-bucket> --force
# Roles: inv2d-gateway-role, the Lambda execution roles and the runtime role
# Customer accounts: delete the CloudFormation stack that created inv2drawio-readonly
```

## If something fails

| Symptom | Likely cause |
|---|---|
| Agent replies but no "Reading the account" step | Runtime role lacks `bedrock-agentcore:InvokeGateway` (step 7) |
| `AccessDenied` in collect Lambda logs | An action missing from the read-only policy |
| Download link returns 403 | Render role lacks `s3:GetObject` on `diagrams/*`, or the link is over an hour old |
| Tool error mentioning WORK_BUCKET | Environment variable missing on a Lambda |
| `ValidationException` on session ID | Session IDs must be 33+ characters; the UI handles this |
| Model access or throttling error | Wrong `MODEL_ID`, or no access to that model in this Region |
| "unknown customer" | No `customers/<id>/profile.json`, or the collector role can't read it |
| "could not assume the read-only role" | Role not deployed in the customer account, wrong external ID, or the trust policy names a different collector role |
| "not approved for <customer>" | Region missing from `allowed_regions` in that customer's profile |
| Customer missing from the UI dropdown | Work bucket not set in the sidebar, or your identity can't list `customers/` |
| Notes say "API Gateway skipped" or "Resource groups skipped" | New actions missing from the read-only policy; re-apply `iam-policy.json` |
| Log says "memory unavailable, continuing without it" | Runtime role lacks the Memory actions, or `MEMORY_ID` is wrong. Diagrams still work |
| Preferences not remembered in a new conversation | Strategy namespaces don't match `/customers/{actorId}/...` exactly, or extraction hasn't finished yet |
| Note says "Resource Explorer is off" | Normal: Cloud Control and the tagging API are used instead |
| Note says "Cloud Control could not list N types (AccessDeniedException)" | The role lacks that service's list permission; re-apply `iam-policy.json` (or the customer role template) |
| A service you expected is missing | It may be in the routine list (IAM, logs, KMS, ...): ask the agent to include it, for example "include kms" |
| Grey boxes instead of icons in draw.io | Send me a screenshot; an icon style name needs correcting |

## Numbers to record for the article
- Collector duration on your real account (CloudWatch: Duration) and resource count
- End-to-end time from prompt to download link
- Token usage and cost per diagram (Bedrock invocation metrics)
- One real diagram screenshot, and the agent's "Worth a look" observations for it
