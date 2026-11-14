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
      + `s3:PutObject` on `arn:aws:s3:::<bucket>/inventory/*` (customer access: see step 11)

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

## 9. Web Search tool (optional, about $7 per 1,000 searches)

Lets the agent back its "Worth a look" notes with links to AWS documentation.
Available in us-east-1, us-east-2, us-west-2, eu-west-1, ap-southeast-2, ap-northeast-1.

- [ ] Add to the **Gateway service role** (`inv2d-gateway-role`):
```json
{"Effect": "Allow", "Action": "bedrock-agentcore:InvokeWebSearch",
 "Resource": "arn:aws:bedrock-agentcore:us-east-1:aws:tool/web-search.v1"}
```
- [ ] `pip install -U boto3` then `python scripts/add_web_search.py --gateway-id <gateway-id>`
- [ ] Redeploy or start a new session so the agent lists the new tool

The target only searches `docs.aws.amazon.com` and `aws.amazon.com`. That list lives on
the target, hidden from the agent, so a prompt can't widen it. AWS's terms require you to
show the source links with any answer that uses search results; the agent does this.

## 10. Gateway Policy (Cedar)

Controls what the agent may call, enforced at the Gateway, outside the model.

| Policy | Allows |
|---|---|
| `collect_inventory` | Only your Runtime role, only in the Regions you approve |
| `render_drawio` | Only your Runtime role |
| `web_search` | Only your Runtime role, at most 5 results per search |

Anything else is denied. Create all targets (steps 5 and 9) first.

- [ ] Start in log-only mode:
```bash
python scripts/add_policy.py --gateway-id <gateway-id> \
  --runtime-role-name <runtime-role-name> --allowed-regions us-east-1 --mode LOG_ONLY
```
- [ ] Run the agent once. In the Gateway's CloudWatch logs, confirm your three tool calls
      show as allowed
- [ ] Enforce:
```bash
python scripts/add_policy.py --gateway-id <gateway-id> --runtime-role-name <runtime-role-name> \
  --engine-id <id-printed-by-the-first-run> --mode ENFORCE
```
- [ ] **Prove it works** (screenshot this for the article): ask the agent to draw a Region
      that is not on your list, for example `eu-west-1`. The Gateway should deny the call
      and the agent should say a policy blocked it.

If you skip step 9, delete `policies/03_web_search.cedar` before running the script:
policy creation fails on purpose when a policy names a tool that doesn't exist.

**If log-only shows your calls as denied:** the principal pattern is the usual cause.
Check the caller ARN in the log entry against
`arn:aws:sts::<account>:assumed-role/<runtime-role-name>/*` and adjust the role name.

## 11. Customers (profile + skill)

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

## 12. AgentCore Memory (optional, works with any model)

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

## If something fails

| Symptom | Likely cause |
|---|---|
| Agent replies but no "Reading the account" step | Runtime role lacks `bedrock-agentcore:InvokeGateway` (step 7) |
| `AccessDenied` in collect Lambda logs | An action missing from the read-only policy |
| Download link returns 403 | Render role lacks `s3:GetObject` on `diagrams/*`, or the link is over an hour old |
| Tool error mentioning WORK_BUCKET | Environment variable missing on a Lambda |
| `ValidationException` on session ID | Session IDs must be 33+ characters; the UI handles this |
| Model access or throttling error | Wrong `MODEL_ID`, or no access to that model in this Region |
| Policy creation fails with validation findings | A tool name in a `.cedar` file doesn't match `<target>___<tool>`; target names must be `collect-inventory`, `render-drawio`, `web-search` |
| Everything denied after ENFORCE | Role name in the principal pattern is wrong; switch back to LOG_ONLY and compare with the logged caller ARN |
| Web search target fails to create | Gateway role lacks `bedrock-agentcore:InvokeWebSearch`, or the Region doesn't offer Web Search |
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
