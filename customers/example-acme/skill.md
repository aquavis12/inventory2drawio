# Acme Retail: how to read this account

## Tagging
- Workloads are tagged `App` (orders, catalogue, cfp). Scope by this tag when the user names an app.
- Environments are tagged `Env` (prod, staging, sandbox).

## What to draw
- Default to `Env=prod` unless the user asks for another environment.
- Hide anything tagged `Env=sandbox` and the default VPC if it is empty.
- The orders application spans the `orders-vpc` VPC plus the `orders-queue` and `orders` table.

## Naming
- Title diagrams "<App> (<Env>), <Region>", for example "Orders (prod), me-central-1".

## Known context
- Production runs in me-central-1. us-east-1 only holds CloudFront-related resources.
