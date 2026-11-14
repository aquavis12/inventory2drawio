# inventory2drawio

Draw the architecture that is actually running in an AWS account, as an editable draw.io
file. A read-only agent on Amazon Bedrock AgentCore reads one Region, scopes it by tag or
resource group, adds the request flow you describe in Markdown, and hands back a `.drawio`
file. Nothing is created, changed or invented in the account.

In a real run: about 12 seconds per diagram, under a fifth of a cent in model cost.

## How it works

| Piece | What it does |
| --- | --- |
| `ui/` | Streamlit app: pick customer, Region and scope, upload a flow file, use prompt templates, download the diagram |
| `agent/` | Strands agent on AgentCore Runtime. Decides scope and writes the explanation. Customer and Region are injected by code, never chosen by the model |
| `tools/collect_inventory/` | Lambda behind AgentCore Gateway. Read-only. Finds resources (Resource Explorer if on, otherwise Cloud Control and the tagging API), adds connections, leaves out plumbing, scopes strictly |
| `tools/render_drawio/` | Lambda behind AgentCore Gateway. Lays out Region, VPC, AZ, subnets and a left-to-right flow with official AWS icons. Draws links from the flow file only between resources that exist |
| `customers/` | Per-customer `profile.json` (access, read by the Lambda only) and `skill.md` (guidance for the agent), plus a CloudFormation template for the read-only role in each customer account |

The rule behind the design: **code decides, the model explains.** Inventory, layout,
customer and Region, download links and flow links are all handled in code; the model
picks the scope and explains the result.

## Key behaviours

- **Public subnets come from route tables, not names.** A subnet is public only if its route
  table sends `0.0.0.0/0` to an internet gateway.
- **Strict scope.** With a tag or resource group, only matching resources are drawn, plus the
  VPC and subnet boxes they sit in. Untagged neighbours are counted in the notes and can be
  added on request.
- **Connections** come from APIs (API Gateway integrations and authorizers, CloudFront origins
  and certificates, event sources, SNS subscriptions, load balancer targets) and from Lambda
  environment variables that exactly match a table, bucket, queue or topic. Values are
  compared in memory, never stored or returned.
- **Plumbing is left out by default**: IAM, logs, task-definition revisions, sub-resources and
  resources AWS created for you. Ask for a service to include it.
- **Small agent context.** The full graph stays in S3; the agent sees a compact summary.
- **Read-only everywhere.** The only writes go to your own work bucket.

## Get started

1. Run the tests (no AWS account needed):

   ```bash
   pip install "moto[ec2,elbv2,rds,awslambda,s3,dynamodb,sqs,iam,apigateway,apigatewayv2,cloudfront,route53,resourcegroups,sts,secretsmanager,resourcegroupstaggingapi,sns,cognitoidp,acm]==5.0.28" boto3
   for t in tests/test_*.py; do python "$t"; done
   ```

   `tests/test_flow.py` and `test_fixed_args.py` also need the packages in
   `agent/requirements.txt`. On Windows, install into a short path (for example
   `python -m venv C:\v`) so moto's deep file paths don't hit the path-length limit.

2. Open `samples/lms_shaped.drawio` at [app.diagrams.net](https://app.diagrams.net) to see
   the output format.

3. Deploy by following **[SETUP.md](SETUP.md)**: a work bucket, two Lambda functions, an
   AgentCore Gateway, the agent on AgentCore Runtime (from a code zip; build it with
   `scripts/build_agent_zip.sh`), optional AgentCore Memory, and the Streamlit UI.

4. To draw another AWS account, deploy `customers/customer-role.yaml` there and add the
   customer's `profile.json` and `skill.md` to the work bucket. Nothing else is installed in
   that account.

## Getting good diagrams

- Tag your workloads (`app=lms`) or put them in a resource group.
- Describe the request flow once in Markdown (for example "1. The site sends results to the API.") and reuse it.
- Expect a small touch-up in draw.io: it's a real, editable file.
- Ask precisely, or start from a template.

## Limits

- One Region per run, plus global CloudFront and Route 53.
- Resources found through discovery but without a dedicated connection reader are drawn
  without lines; the legend says how many.
- Security groups, IAM policies and encryption settings aren't read.
- Tests run against moto, a mock of AWS; real accounts can surface cases it doesn't model.

## License

MIT
