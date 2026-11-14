# inventory2drawio: live AWS account to editable draw.io diagram

AWS retired Workload Discovery on AWS (archived Aug 2026). This rebuilds its most
useful part as agent tools: read a live account, draw an editable draw.io diagram.

| Tool | Does | Notes |
|---|---|---|
| `collect_inventory` | Region inventory as a graph: VPC > AZ > subnet placement, ALB to targets, SQS/DynamoDB triggers to Lambda | Read-only. Saves the full graph to S3, returns a compact view to the agent |
| `render_drawio` | Graph to draw.io XML with official AWS icons, deterministic layout | Writes to S3, returns a 1-hour download link |

## Design decisions (good article material)

- **Public vs private comes from route tables, not names.** A subnet is public only if its
  route table sends 0.0.0.0/0 to an internet gateway. The test includes a subnet named
  `public-trap` with no such route; it is correctly drawn as private.
- **Layout is code.** Models are bad at coordinates. The generator measures bottom-up,
  draws top-down, and a test checks that nothing overlaps or spills out of its parent.
- **Tag filters keep context.** Scoping to `App=orders` keeps untagged resources that are
  directly connected (the queue triggering a tagged function, the ALB in front of tagged
  instances) and each kept VPC's internet and NAT gateways. Without this, the first
  version returned the right resources with zero connections.
- **The agent gets 820 bytes, not 30K tokens.** The full graph stays in S3.
- **Round trip with November's parser.** The generated diagram parses back to the same
  resource types, which is the shared piece for the diagram to Terraform round-trip check.

## 1. Run the tests

```bash
pip install "moto[ec2,elbv2,rds,awslambda,s3,dynamodb,sqs,iam,apigateway,apigatewayv2,cloudfront,route53,resourcegroups,sts]==5.0.28" boto3
python tests/test_end_to_end.py   # mock account, collect, layout checks, round trip
python tests/test_s3_flow.py      # collector -> S3 -> renderer
```

Open `samples/orders_scoped.drawio` in https://app.diagrams.net to see the real output.
**Check the icons render** (not grey boxes): this is the one thing the tests can't verify.

## 2. Deploy

```bash
export AWS_REGION=us-east-1
BUCKET=inv2drawio-work-<ACCOUNT_ID>
aws s3api create-bucket --bucket $BUCKET
aws s3api put-public-access-block --bucket $BUCKET --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
# Inventory data describes your network: expire it after 7 days.
aws s3api put-bucket-lifecycle-configuration --bucket $BUCKET --lifecycle-configuration \
  '{"Rules":[{"ID":"expire","Status":"Enabled","Filter":{"Prefix":""},"Expiration":{"Days":7}}]}'

(cd tools/collect_inventory && zip -j ../../collect.zip handler.py)
(cd tools/render_drawio && zip -j ../../render.zip handler.py generator.py)

aws lambda create-function --function-name inv2d-collect-inventory --runtime python3.12 \
  --handler handler.lambda_handler --zip-file fileb://collect.zip --timeout 120 --memory-size 512 \
  --environment Variables={WORK_BUCKET=$BUCKET} --role <collect-role-arn>
aws lambda create-function --function-name inv2d-render-drawio --runtime python3.12 \
  --handler handler.lambda_handler --zip-file fileb://render.zip --timeout 30 --memory-size 256 \
  --environment Variables={WORK_BUCKET=$BUCKET} --role <render-role-arn>
```

Roles (both also need `AWSLambdaBasicExecutionRole`):
- **collect:** `tools/collect_inventory/iam-policy.json` (read-only) plus `s3:PutObject` on `$BUCKET/inventory/*`
- **render:** `s3:GetObject` on `$BUCKET/inventory/*` and `s3:PutObject`, `s3:GetObject` on `$BUCKET/diagrams/*`

## 3. Try it on a real account

```bash
aws lambda invoke --function-name inv2d-collect-inventory --cli-binary-format raw-in-base64-out \
  --payload '{"region":"us-east-1","tag_filter":{"key":"App","value":"orders"}}' out.json && cat out.json
# copy graph_s3_uri from out.json:
aws lambda invoke --function-name inv2d-render-drawio --cli-binary-format raw-in-base64-out \
  --payload '{"graph_s3_uri":"s3://...","title":"Orders"}' out2.json && cat out2.json
```

Open `download_url` in a browser, then open the file in draw.io.

## Known limits (state them in the article)

- One Region per call, plus global CloudFront and Route 53. S3 buckets are drawn only if
  they are in that Region.
- Covered: VPC, subnets, IGW, NAT, EC2, ALB/NLB + targets, RDS instances, Lambda + event
  sources, API Gateway (HTTP and REST) + Lambda integrations, DynamoDB, SQS, S3,
  CloudFront + origins, Route 53 zones. Not yet: ECS/EKS, Aurora clusters, security
  group rules, VPC peering, Transit Gateway, Route 53 record targets.
- Resource groups are drawn as boxes for resources outside a VPC. Resources inside a VPC
  stay in their subnet. A resource in several groups is drawn in the first by name.
- moto has not implemented `ListGroupResources`, so resource-group reading is untested
  against a mock; the matching logic is unit-tested.
- Tests run on moto, a mock of AWS. Real accounts will surface cases it doesn't model.

## The agent and UI

- `agent/agent.py`: Strands agent for AgentCore Runtime. Reaches the tools through
  AgentCore Gateway with IAM (SigV4) auth, streams tool activity and the answer.
- `ui/app.py`: Streamlit chat UI that calls the Runtime and shows a download button.
- `scripts/add_web_search.py`: adds AgentCore Web Search, limited to AWS's own sites.
- `scripts/add_policy.py` + `policies/*.cedar`: Gateway Policy. Only the Runtime role may
  call the tools, inventory only in approved Regions, web search capped at 5 results.
- `customers/`: per-customer `profile.json` (access, Lambda only) and `skill.md` (guidance
  for the agent), plus the CloudFormation template for the customer-side read-only role.
- **`SETUP.md`: the manual setup checklist, start there.**

```bash
python tests/test_ui_stream.py    # UI stream parser
python tests/test_scripts.py      # setup scripts vs the AWS API model (needs boto3 >= 1.44)
python tests/test_customers.py    # two customers, two accounts: isolation + new services
```
