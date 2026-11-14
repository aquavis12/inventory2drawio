# Your AWS account, drawn: a read-only agent that turns live inventory into editable draw.io diagrams

## Why I built this

I work across 30 to 40 customer AWS accounts, and their workloads keep changing. Hand-drawn diagrams go stale within weeks, and AWS archived Workload Discovery on AWS in August 2026.

So I built a tool that reads an account and draws it. You pick a customer and a Region, optionally scope to a tag or resource group, optionally describe the request flow in Markdown, and you get an editable `.drawio` file in about 12 seconds for under a fifth of a cent. Everything in the diagram exists in the account. The tool cannot create, change or delete anything.

![The LMS study site, drawn from live inventory and opened in draw.io](lms-diagram.png)

## What it has to do

These are the requirements. Each feature below exists because of one of them.

1. **Read many accounts without write access anywhere.** One read-only role per customer.
2. **Give the same diagram for the same inputs.** Inventory and layout are code, not model output.
3. **Let a non-expert ask in plain words.** "The orders app", "just my S3 buckets".
4. **Produce a file people can edit.** A real `.drawio`, not an image.
5. **Show connections AWS doesn't record.** A website calling an API has no resource behind it.

## How it works

![Architecture](architecture.png)

*Source: `docs/architecture.drawio`.*

A Streamlit UI runs on your machine. It sends the request, the customer, the Region and the scope to an agent on Amazon Bedrock AgentCore Runtime. The agent calls two Lambda functions through AgentCore Gateway (IAM auth, tools over MCP):

- **`collect_inventory`** assumes a read-only role in the customer's account, reads the Region, and writes the inventory graph to an S3 work bucket.
- **`render_drawio`** lays the graph out and writes the `.drawio` file. The UI downloads it directly.

The model is Nemotron 3 Super on Bedrock. AgentCore Memory keeps per-customer preferences, so "leave out the ACM certificate" sticks across conversations.

### Why an agent at all

Reading and drawing are deterministic. The agent handles the parts that start as free text:

| Job | Why code alone can't do it |
|---|---|
| Turn "just my S3 buckets" or "the orders app" into a scope (`only_services`, tag, resource group) | The request is free text. The tool schema is the contract. |
| Map each step of a flow description to real resources | Fuzzy name matching |
| Explain the result in the order a request travels | Prose |

Everything else is code. That split is the main design rule: **code decides, the model explains.**

| Job | Done by |
|---|---|
| What exists and how it connects | Collector Lambda |
| Where boxes and lines go | Layout code |
| Whether this message is a drawing request at all | Code (`agent/intent.py`) |
| Which customer and Region to read | The app, injected into every tool call. The model can't change them. |
| The download link | Passed beside the answer, never through the model |
| Risks worth a look | Collector Lambda (`findings`). The model only rewords them. |
| Flow links | Only the Markdown file, checked against real resources |
| Scope, what to hide, the explanation | The model |

Both Lambdas exist because AgentCore Gateway targets can be Lambda functions. The tools stay stateless and IAM-authenticated, and the collector runs where it can assume a role in a customer account.

## How the inventory works

The collector reads one Region, plus global CloudFront and Route 53, using a read-only role.

1. **Dedicated collectors** for about 20 types: VPCs, subnets, route tables, gateways, EC2, load balancers and their targets, RDS, Lambda and its event sources, API Gateway, DynamoDB, SQS, SNS, Cognito, ACM, S3, CloudFront, Route 53. These give placement and connections. A subnet is public only if its route table sends `0.0.0.0/0` to an internet gateway. Names lie, routes don't.
2. **AWS Resource Explorer**, if the customer already has it on. It is the most complete source, but it has to be enabled per Region and many customers won't do that.
3. **Cloud Control and the Resource Groups Tagging API** when it is off. Cloud Control lists 30 common types with no setup in the account. The cost is that each service needs its own list permission. A missing permission skips that type and says so in the notes.
4. **A noise filter** drops IAM, logs, KMS, task-definition revisions and resources AWS created for you, and counts them in the notes. `include_services` brings them back when you ask.
5. **Strict scope.** With a tag or resource group, only matching resources are drawn. Untagged neighbours appear only if you ask for connected resources, and then in their own dashed "Connected, not in scope" box so they don't look like part of the application.

Connections come from configuration: API Gateway to Lambda and Cognito, CloudFront to its origins and certificate, event sources and SNS subscriptions to Lambda, and Lambda to the tables, buckets, queues and topics named in its environment variables (exact matches, compared in memory, never stored).

Use a custom read-only policy (`ViewOnlyAccess` or the one in the repository), not `ReadOnlyAccess`, which can also read data such as S3 objects.

## Where boxes and lines go

Layout is code. Resources are grouped by account, Region, VPC, Availability Zone and subnet, then by resource group, with services in columns from entry point to data. The same input gives the same file. The model chooses only scope and what to hide.

It is an AWS architecture diagram, not a C4 model. The closest C4 level is "container": it shows deployable pieces and how they connect, with AWS icons.

**Lines are routed in code too.** draw.io's automatic routing ran lines through icons. The renderer now finds an orthogonal path for each connection. Lines leave one side of an icon, run along free lanes between icons and rows, and enter the side facing the source. Bends cost extra, so lines stay straight where they can. A test checks every segment against every other icon. On a dense test layout of 60 icons and 80 random edges, 79 of 79 connections routed without crossing an icon, in 0.05 seconds. Diagrams over 70 icons fall back to draw.io's own routing, and a line can still cross a group border or another line.

## The flow AWS can't see

Some connections no API records, and the tool takes them from a short Markdown file:

```
1. The site sends activity and quiz results to vr-activity-api.
2. vr-activity-fn stores submissions in vr-submissions.
3. Every evening, vr-activity-fn publishes a summary to vr-daily-submissions.
```

AWS can't show these because relationships like "the browser calls this API" are a property of runtime traffic and application code, not of resource configuration. Flow logs or tracing could show some, but only if they were on before you asked. The agent maps each step to real resources. The renderer draws a dashed, labelled line only if both ends exist, and drops described links entirely when no flow file was sent. Steps naming people or outside systems are skipped and listed in the reply.

## Customers and `skill.md`

Each customer has two files in the work bucket:

- **`profile.json`**: account ID, role, external ID, allowed Regions. Only the Lambda reads it. The agent passes a customer ID and nothing else, and the Lambda refuses unapproved Regions.
- **`skill.md`**: plain-language guidance the agent reads. Which tag scopes an app, which environment is the default, what to hide, how to title diagrams. For example:

```markdown
## Tagging
- Workloads are tagged `App` (orders, catalogue). Scope by this tag when the user names an app.
## What to draw
- Default to `Env=prod`. Hide anything tagged `Env=sandbox` and an empty default VPC.
## Naming
- Title diagrams "<App> (<Env>), <Region>".
```

The customer dropdown in the UI lists `customers/` in the work bucket. There is no hard-coded list and no SSO, and the UI uses your own local AWS credentials.

"Install once" means: deploy the tool in one tooling account, then deploy `customers/customer-role.yaml` (a read-only role with an external ID) in each customer account. Nothing else is installed there. My live runs used my own account. The cross-account path is covered by tests against two mocked accounts.

**Memory.** I told the agent once to leave out the ACM certificate. In a new conversation, without repeating it, the next diagram left it out. The test recorded 11 events and 9 long-term memories with no errors, retrieval averaged about 230 ms, and the whole test cost $0.0095.

## What broke

Every fix moved a decision from the model into code.

**The model drew for any message.** I typed "HI" and got the LMS diagram. The UI attaches your sidebar scope (a tag, a flow description) to every message, and a small model read that as an instruction. A prompt rule didn't fix it. Now a check on your own words in `agent/intent.py` decides first. Messages with no drawing intent go to a chat agent that has no tools, scope or flow text. The trade-off: a keyword check is crude and can misroute an unusual phrasing, but it never draws by accident.

**"Only S3" came back with one bucket.** The account has 13. The model hid 12 as "outside the workload". Two fixes: an `only_services` option on the collector, and code that ignores the model's `hide_ids` whenever `only_services` was used. After that, every live run drew all 13.

**"Your diagram is ready", with no diagram.** In some runs the model skipped `render_drawio` and said it was done anyway. The app now holds back the reply text. If it says "ready" but nothing was rendered, the app asks the agent once more to render, and if that fails it says so plainly. The cost is that the reply text arrives all at once at the end, while the tool steps still stream.

**A 1,500-character link was corrupted.** The download link was a presigned S3 URL carrying a long security token. The model re-typed it with altered characters and S3 answered `InvalidToken`. The app now downloads the file itself and no link passes through the model. The general lesson for anything LLM-handled: never let the model re-type a signed URL. Hand it a reference and resolve it in code.

**Amazon Nova Pro produced malformed tool calls.** The error was `Model produced invalid sequence as part of ToolUse`. The code retries once on a clean conversation, and I switched to Nemotron 3 Super. <!-- ADD: your failure count out of N runs -->

**Other fixes:** the agent asked for a customer ID it already had (now injected by code), a whole Region was buried in plumbing (now a noise filter and strict scope), and lines made three tables look chained (now routed in code).

**Still imperfect.** In one of three live runs the explanation said the other buckets were hidden even though all 13 were drawn. The diagram was right and the prose was wrong. Prompt rules reduce this but don't remove it.

## Results

One request scoped to the LMS resource group (an AWS Resource Group defined by the tags `app=lms` and `createdby=vishnu`) took about 12 seconds: inventory 0.9 s, rendering 7.9 s, the answer at 11.1 s. It used 9,928 input and 262 output tokens, about $0.0017 at the Nemotron 3 Super rates listed by third-party trackers.

<!-- ADD: a table of 4 to 6 accounts: resources, nodes drawn, collector seconds, render seconds, tokens, cost. Include one tiny, one messy and one very large account, plus a tag-scoped run and an only_services run. -->

## Build it yourself

The code is at github.com/aquavis12/inventory2drawio. `SETUP.md` walks through it in three parts.

**Setup.** A work bucket, two Lambda functions, an AgentCore Gateway with IAM auth, the agent on AgentCore Runtime from a code zip, optional AgentCore Memory, and the Streamlit UI.

**Verify.**
1. Invoke the collector directly. Expect `"ok": true`, a node count and a `findings` list.
2. Ask for "only my S3 buckets". The diagram must contain buckets and nothing else.
3. Say "hi". The agent must answer in text with no tool step.
4. Download the `.drawio` and open it at app.diagrams.net.

**Clean up.** Delete the runtime, the gateway targets and gateway, the memory, the two Lambdas, both buckets and the roles. The customer role belongs to its CloudFormation stack in each customer account. The exact commands are in `SETUP.md`.

Fourteen test scripts run against a mocked AWS account.

## Limits

- One Region per run.
- Resources found only through discovery are drawn without lines.
- Security groups, IAM policies and encryption settings aren't read.
- Intent detection is keyword-based and can misroute unusual phrasing.
- The explanation can be wrong even when the diagram is right.
