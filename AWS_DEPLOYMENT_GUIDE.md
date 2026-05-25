# AWS Deployment Guide

Complete guide for deploying the Asset Management Multi-Agent Platform to AWS.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Architecture Overview](#architecture-overview)
3. [Quick Start](#quick-start)
4. [Detailed Deployment Steps](#detailed-deployment-steps)
5. [Post-Deployment Configuration](#post-deployment-configuration)
6. [LLM Features (AWS Bedrock)](#llm-features-aws-bedrock)
7. [Verification](#verification)
8. [Monitoring](#monitoring)
9. [Rollback](#rollback)
10. [Troubleshooting](#troubleshooting)
11. [Cost Estimation](#cost-estimation)
12. [Cleanup](#cleanup)
13. [Command Reference](#command-reference)
14. [Advanced Topics](#advanced-topics)

---

## Prerequisites

### Required Tools

- **AWS CLI** (v2.x or later)
  ```bash
  aws --version
  # aws-cli/2.x.x Python/3.x.x
  ```

- **Terraform** (v1.5.0 or later)
  ```bash
  terraform version
  # Terraform v1.5.0 or later
  ```

- **Node.js & npm** (v18.x or later)
  ```bash
  node --version  # v18.x or later
  npm --version   # v9.x or later
  ```

- **Python** (3.12 or 3.13)
  ```bash
  python3 --version  # Python 3.12 or 3.13
  ```

- **uv** (Python package installer - optional but recommended)
  ```bash
  uv --version
  # Or install: curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- **Docker** (optional, for Lambda packaging)
  ```bash
  docker --version
  ```

### AWS Configuration

✅ **AWS credentials configured** (you mentioned this is done)

Verify your configuration:
```bash
aws sts get-caller-identity
```

Expected output:
```json
{
    "UserId": "AIDAXXXXXXXXXXXXXXXXX",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/your-username"
}
```

### AWS Permissions Required

Your IAM user/role needs permissions for:
- **DynamoDB**: CreateTable, DescribeTable, TagResource
- **Lambda**: CreateFunction, UpdateFunctionCode, UpdateFunctionConfiguration
- **IAM**: CreateRole, AttachRolePolicy, PassRole
- **API Gateway**: CreateApi, CreateRoute, CreateIntegration
- **S3**: CreateBucket, PutObject, PutBucketPolicy, PutBucketWebsite
- **CloudWatch Logs**: CreateLogGroup, PutRetentionPolicy

---

## Architecture Overview

### AWS Services Used

```
┌─────────────────────────────────────────────────────────────┐
│                         AWS Cloud                            │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐         ┌──────────────────────────┐      │
│  │   S3 Bucket  │         │   API Gateway (HTTP)     │      │
│  │   (Frontend) │         │   /api/* → Backend       │      │
│  │   Static     │         │   /a2a/research → Agent  │      │
│  │   Website    │         │   /mcp → Sentiment       │      │
│  └──────────────┘         └──────────────────────────┘      │
│         │                            │                        │
│         │                            ▼                        │
│         │                  ┌──────────────────┐              │
│         │                  │  Lambda Functions │              │
│         │                  ├──────────────────┤              │
│         │                  │ • Backend API    │              │
│         │                  │ • Research Agent │              │
│         │                  │ • Sentiment MCP  │              │
│         │                  └──────────────────┘              │
│         │                            │                        │
│         │                            ▼                        │
│         │                  ┌──────────────────┐              │
│         │                  │   DynamoDB       │              │
│         │                  ├──────────────────┤              │
│         │                  │ • Approvals      │              │
│         │                  │ • Audit Events   │              │
│         │                  │ • Portfolios     │              │
│         │                  │ • Sessions       │              │
│         │                  │ • Memory Queue   │              │
│         │                  │ • Preferences    │              │
│         │                  └──────────────────┘              │
│         │                            │                        │
│         │                            ▼                        │
│         │                  ┌──────────────────┐              │
│         │                  │  CloudWatch Logs │              │
│         │                  │  (Monitoring)    │              │
│         │                  └──────────────────┘              │
│         │                            │                        │
│         │                            ▼                        │
│         │                  ┌──────────────────┐              │
│         │                  │  Amazon Bedrock  │              │
│         │                  │  (optional LLM)  │              │
│         │                  └──────────────────┘              │
│         │                                                     │
└─────────┴─────────────────────────────────────────────────────┘
          │
          ▼
    ┌──────────┐
    │  Users   │
    │ (Browser)│
    └──────────┘
```

### Resource Naming Convention

All resources are prefixed with: `{project_name}-{environment}-`

Example for dev environment:
- DynamoDB: `asset-management-dev-approvals`
- Lambda: `asset-management-dev-backend`
- S3: `asset-management-dev-frontend`
- API Gateway: `asset-management-dev-api`

### Lambda Functions (dev defaults)

| Function | Memory | Timeout | Bedrock IAM |
|----------|--------|---------|-------------|
| `{prefix}-backend` | 512 MB | 30s | Yes |
| `{prefix}-research-agent` | 256 MB | 15s | Yes |
| `{prefix}-sentiment-mcp` | 256 MB | 15s | Yes |

### DynamoDB Tables

- `{prefix}-approvals`
- `{prefix}-audit-events`
- `{prefix}-portfolios`
- `{prefix}-sessions`
- `{prefix}-memory-queue`
- `{prefix}-preferences`

---

## Quick Start

For experienced users who want to deploy immediately.

### Option A: One-command deploy

```bash
./infra/scripts/deploy.sh dev
```

Requires `infra/terraform/environments/dev/dev.tfvars` (copy from `dev.tfvars.example` if needed).

### Option B: Manual steps

```bash
# 1. Package Lambda functions
./infra/scripts/package_lambda.sh

# 2. Create tfvars file
cd infra/terraform/environments/dev
cp dev.tfvars.example dev.tfvars
# Edit dev.tfvars with your settings

# 3. Deploy infrastructure
terraform init
terraform plan -var-file=dev.tfvars
terraform apply -var-file=dev.tfvars

# 4. Deploy frontend (include api_token from dev.tfvars for /api/* access)
cd ../../../..
API_TOKEN=$(grep '^api_token' infra/terraform/environments/dev/dev.tfvars | sed -n 's/.*= *"\(.*\)".*/\1/p')
./infra/scripts/publish_frontend.sh \
  "$(terraform -chdir=infra/terraform/environments/dev output -raw api_base_url)" \
  "$(terraform -chdir=infra/terraform/environments/dev output -raw frontend_bucket_name)" \
  "${API_TOKEN}"

# 5. Get frontend URL
terraform -chdir=infra/terraform/environments/dev output -raw frontend_website_url
```

Day-2 operations (redeploy Lambda, tail logs, DynamoDB scans): [Command Reference](#command-reference).

---

## Detailed Deployment Steps

### Step 1: Package Lambda Functions

Lambda functions need to be packaged with their dependencies.

```bash
# From project root
./infra/scripts/package_lambda.sh
```

**What this does:**
- Installs Python dependencies for each service
- Creates deployment packages in `infra/build/`
- Generates three zip files:
  - `backend.zip` (~50MB)
  - `research-agent.zip` (~20MB)
  - `sentiment-mcp.zip` (~20MB)

**Options:**
```bash
# Use Docker for consistent builds (recommended for production)
USE_DOCKER=true ./infra/scripts/package_lambda.sh

# Use local Python (faster for development)
USE_DOCKER=false ./infra/scripts/package_lambda.sh

# Specify Python version
PYTHON_VERSION=3.13 ./infra/scripts/package_lambda.sh
```

**Verify packages:**
```bash
ls -lh infra/build/*.zip
# Should show three zip files
```

### Step 2: Configure Terraform Variables

Create your environment-specific configuration:

```bash
cd infra/terraform/environments/dev
cp dev.tfvars.example dev.tfvars
```

Edit `dev.tfvars`:

```hcl
# AWS Region
aws_region = "us-east-1"  # Change to your preferred region

# Project Configuration
project_name = "asset-management"
environment  = "dev"

# Lambda Configuration
lambda_runtime = "python3.13"

# CORS Configuration
cors_allow_origins = ["*"]  # For production, specify exact origins

# Optional: Custom S3 bucket name
# frontend_bucket_name = "my-custom-frontend-bucket-name"
```

**Important Notes:**
- S3 bucket names must be globally unique
- If you don't specify `frontend_bucket_name`, it will be auto-generated
- For production, restrict CORS to your domain only

### Step 3: Initialize Terraform

```bash
# Still in infra/terraform/environments/dev
terraform init
```

**Expected output:**
```
Initializing modules...
Initializing the backend...
Initializing provider plugins...
- Finding latest version of hashicorp/aws...
- Installing hashicorp/aws v5.x.x...

Terraform has been successfully initialized!
```

### Step 4: Review Deployment Plan

```bash
terraform plan -var-file=dev.tfvars
```

**What to look for:**
- ✅ 6 DynamoDB tables to be created
- ✅ 3 Lambda functions to be created
- ✅ 1 API Gateway to be created
- ✅ 1 S3 bucket to be created
- ✅ IAM roles and policies
- ✅ CloudWatch log groups

**Expected resource count:**
```
Plan: 30+ to add, 0 to change, 0 to destroy.
```

### Step 5: Deploy Infrastructure

```bash
terraform apply -var-file=dev.tfvars
```

Type `yes` when prompted.

**Deployment time:** ~2-3 minutes

**Expected output:**
```
Apply complete! Resources: 30+ added, 0 changed, 0 destroyed.

Outputs:

api_base_url = "https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com"
dynamodb_table_names = {
  "approvals" = "asset-management-dev-approvals"
  "audit_events" = "asset-management-dev-audit-events"
  "memory_queue" = "asset-management-dev-memory-queue"
  "portfolios" = "asset-management-dev-portfolios"
  "preferences" = "asset-management-dev-preferences"
  "sessions" = "asset-management-dev-sessions"
}
frontend_bucket_name = "asset-management-dev-frontend"
frontend_website_url = "http://asset-management-dev-frontend.s3-website-us-east-1.amazonaws.com"
lambda_function_names = {
  "backend" = "asset-management-dev-backend"
  "research_agent" = "asset-management-dev-research-agent"
  "sentiment_mcp" = "asset-management-dev-sentiment-mcp"
}
```

**Save these outputs!** You'll need them for the next steps.

### Step 6: Deploy Frontend

Build and upload the Angular frontend:

```bash
# From project root
cd ../../../../  # Back to project root

# Option A: Use the automated script (recommended)
./infra/scripts/publish_frontend.sh \
  "$(terraform -chdir=infra/terraform/environments/dev output -raw api_base_url)" \
  "$(terraform -chdir=infra/terraform/environments/dev output -raw frontend_bucket_name)"

# Option B: Manual steps
cd frontend
npm ci
npm run build

# Create app-config.js with API URL
API_BASE=$(terraform -chdir=../infra/terraform/environments/dev output -raw api_base_url)
printf "window.assetManagementConfig = {\n  apiBaseUrl: '%s'\n};\n" "$API_BASE" \
  > dist/frontend/browser/app-config.js

# Upload to S3
BUCKET=$(terraform -chdir=../infra/terraform/environments/dev output -raw frontend_bucket_name)
aws s3 sync dist/frontend/browser/ "s3://${BUCKET}/" --delete
```

**Expected output:**
```
> frontend@0.0.0 build
> ng build

✔ Building...
Application bundle generation complete.

upload: dist/frontend/browser/index.html to s3://asset-management-dev-frontend/index.html
upload: dist/frontend/browser/main.js to s3://asset-management-dev-frontend/main.js
...
Uploaded frontend to s3://asset-management-dev-frontend/
```

---

## Post-Deployment Configuration

### Configure Custom Domain (Optional)

To use a custom domain like `app.yourdomain.com`:

1. **Create CloudFront distribution** (for HTTPS)
2. **Configure Route53** (for DNS)
3. **Update CORS settings** in `dev.tfvars`

See [Custom Domain Setup](#custom-domain-setup) for details.

### Application URLs

After deploy, read URLs from Terraform outputs (do not hardcode account-specific URLs):

```bash
# Frontend (S3 static website)
terraform -chdir=infra/terraform/environments/dev output -raw frontend_website_url

# Backend API
terraform -chdir=infra/terraform/environments/dev output -raw api_base_url
```

---

## LLM Features (AWS Bedrock)

The dev Terraform configuration enables LLM-enhanced agents by default. Bedrock permissions and feature flags are defined in `infra/terraform/environments/dev/main.tf`.

### 1. Request Bedrock model access

1. Open **AWS Console → Bedrock → Model access**
2. Request access to the Claude models you plan to use
3. Wait for approval (often a few minutes)

### 2. IAM permissions (Terraform)

Terraform attaches Bedrock invoke permissions to all three Lambda execution roles:

- `bedrock:InvokeModel`
- `bedrock:InvokeModelWithResponseStream`
- Resource: `*` (all Bedrock models in the account/region)

Policy documents: `backend_bedrock` (backend) and `agent_bedrock` (research agent and sentiment MCP).

### 3. Feature flags (backend Lambda)

Default dev environment variables:

```hcl
FEATURE_MEMORY_AGENT_LLM_ENABLED         = "true"
FEATURE_RESEARCH_AGENT_LLM_ENABLED       = "true"
FEATURE_SENTIMENT_AGENT_LLM_ENABLED      = "true"
FEATURE_REBALANCING_AGENT_LLM_ENABLED    = "true"
FEATURE_RISK_AGENT_LLM_ENABLED           = "true"
FEATURE_TRADE_PROPOSAL_AGENT_LLM_ENABLED = "true"
FEATURE_FALLBACK_ON_LLM_FAILURE          = "true"
```

`FEATURE_FALLBACK_ON_LLM_FAILURE` keeps the app working if Bedrock calls fail.

Agent Lambdas set agent-specific flags (`FEATURE_RESEARCH_AGENT_LLM_ENABLED`, `FEATURE_SENTIMENT_AGENT_LLM_ENABLED`).

### 4. Disable or tune LLMs

Set any `FEATURE_*_LLM_ENABLED` variable to `"false"` in `main.tf`, then:

```bash
cd infra/terraform/environments/dev
terraform plan -var-file=dev.tfvars
terraform apply -var-file=dev.tfvars
```

Or redeploy with `./infra/scripts/deploy.sh dev` if you use the project deploy script.

### Related docs

- [STARTUP_GUIDE.md](STARTUP_GUIDE.md) — local run, LLMs, allocation validation

---

## Verification

### 1. Test Backend API

```bash
API_URL=$(terraform -chdir=infra/terraform/environments/dev output -raw api_base_url)

# Health check
curl "${API_URL}/health"
```

**Expected response:**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "environment": "dev"
}
```

### 2. Test Portfolios Endpoint

```bash
curl "${API_URL}/api/portfolios"
```

**Expected response:**
```json
[
  {
    "client_profile": {
      "client_id": "client_demo",
      "display_label": "Demo Investor",
      ...
    },
    ...
  }
]
```

### 3. Test Frontend

Open the frontend URL in your browser:

```bash
# Get URL
terraform -chdir=infra/terraform/environments/dev output -raw frontend_website_url

# Or open directly (macOS)
open "$(terraform -chdir=infra/terraform/environments/dev output -raw frontend_website_url)"
```

**What to verify:**
- Page loads without errors
- "Asset Management" title visible
- Portfolio data loads
- Market simulation stream works
- Can generate rebalance recommendations
- Can access preferences page

### 4. Test allocation validation (frontend)

1. Open the frontend URL from Terraform output
2. Click **Preferences**, choose **Aggressive** (max concentration 85%)
3. On the allocation screen, set equity to 90%
4. **Expected:** red error; you cannot proceed
5. Set equity to 85% or less
6. **Expected:** error clears; you can proceed

A 0% cash allocation shows an orange informational warning (not blocking). The **Aggressive** preset uses 85/10/5 (equity/bonds/cash) to stay within the default 85% concentration limit.

### 5. Test LLM-enhanced recommendations

1. Submit a rebalance request from the main page
2. Tail backend logs and look for Bedrock activity (see [Monitoring](#monitoring))
3. Confirm recommendation output reflects LLM-enhanced agents when Bedrock is available

### 6. Test policy-block acknowledgment

1. Trigger a scenario that produces a policy-blocked recommendation
2. Click **Acknowledge Policy Block**
3. **Expected:** block cleared; recommendation dismissed (REJECT is allowed on blocked items via `backend/app/api/routes/approvals.py`)

### 7. Verify LLM configuration on Lambda

```bash
aws lambda get-function-configuration \
  --function-name asset-management-dev-backend \
  --query 'Environment.Variables' \
  --region us-east-1 | grep FEATURE_
```

Confirm `FEATURE_*_LLM_ENABLED` values and that Bedrock policies are attached to Lambda roles in IAM.

### 8. Check DynamoDB Tables

See [Command Reference → DynamoDB](#dynamodb).

### Verification checklist

- Backend Lambda deployed with expected environment variables
- Research agent and sentiment MCP Lambdas have Bedrock IAM policies
- `curl "${API_URL}/health"` returns healthy
- Frontend loads from S3 website URL
- DynamoDB tables exist and are readable by Lambdas
- Allocation validation and policy-block flows behave as above (if testing UI)

---

## Monitoring

- **CloudWatch Logs** — tail Lambda logs, filter errors, grep Bedrock: [Command Reference → Logs](#logs)
- **CloudWatch Metrics** — invocations, errors, DynamoDB capacity: [Command Reference → Metrics](#metrics)
- **Cost Explorer CLI** — monthly spend by service: [Command Reference → Cost monitoring](#cost-monitoring)
- **Console** — Cost Explorer for Bedrock line items after enabling LLMs

---

## Rollback

### Option 1: Disable LLM features only

In `infra/terraform/environments/dev/main.tf`, set all `FEATURE_*_LLM_ENABLED` (and agent-specific flags) to `"false"`, then:

```bash
cd infra/terraform/environments/dev
terraform plan -var-file=dev.tfvars
terraform apply -var-file=dev.tfvars
```

Redeploy frontend if needed: `./infra/scripts/publish_frontend.sh ...`

### Option 2: Full application rollback

```bash
git log --oneline   # find the last good commit
git revert <commit-hash>
./infra/scripts/deploy.sh dev   # or your usual deploy flow
```

---

## Troubleshooting

For log filtering, IAM inspection, CORS tests, and API Gateway checks, see [Command Reference → Operations troubleshooting](#operations-troubleshooting).

### Issue: Lambda Package Too Large

**Error:**
```
Error: error creating Lambda Function: InvalidParameterValueException: 
Unzipped size must be smaller than 262144000 bytes
```

**Solution:**
```bash
# Use Docker for smaller packages
USE_DOCKER=true ./infra/scripts/package_lambda.sh

# Or increase Lambda memory (allows larger packages)
# In main.tf, change memory_mb to 1024 or higher
```

### Issue: S3 Bucket Name Already Exists

**Error:**
```
Error: error creating S3 Bucket: BucketAlreadyExists: 
The requested bucket name is not available
```

**Solution:**
```bash
# In dev.tfvars, specify a unique bucket name:
frontend_bucket_name = "asset-mgmt-dev-frontend-YOUR-UNIQUE-SUFFIX"
```

### Issue: Frontend Shows "Failed to Load"

**Symptoms:**
- Frontend loads but shows errors
- Console shows CORS errors
- API requests fail

**Solution:**
```bash
# 1. Verify API URL in app-config.js
aws s3 cp s3://YOUR-BUCKET/app-config.js -

# 2. Check CORS configuration
# In dev.tfvars, ensure cors_allow_origins includes "*" or your domain

# 3. Redeploy frontend (see Command Reference → Deploy and update frontend)
```

### Issue: Lambda Timeout

**Error in logs:**
```
Task timed out after 30.00 seconds
```

**Solution:**
```hcl
# In main.tf, increase timeout_seconds:
module "backend_lambda" {
  ...
  timeout_seconds = 60  # Increase from 30
  ...
}
```

### Issue: DynamoDB Access Denied

**Error:**
```
AccessDeniedException: User is not authorized to perform: dynamodb:GetItem
```

**Solution:**
```bash
# Verify IAM policy includes all tables
terraform plan -var-file=dev.tfvars
# Look for aws_iam_policy_document.backend_dynamodb

# If preferences table is missing, it was added in this guide
terraform apply -var-file=dev.tfvars
```

### Issue: Cannot Access Bedrock Models

**Error:**
```
AccessDeniedException: Could not access model
```

**Solution:**
1. Request model access in AWS Console → Bedrock → Model access
2. Confirm Terraform applied `backend_bedrock` / `agent_bedrock` policies to Lambda roles
3. Wait 5–10 minutes for permissions to propagate
4. Verify feature flags on the Lambda environment (see [LLM Features](#llm-features-aws-bedrock))

### Issue: Bedrock Access Denied (IAM)

**Solution:** Re-run `terraform apply` and confirm the Lambda execution role includes `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`.

### Issue: High Bedrock costs

**Solution:** Set unused `FEATURE_*_LLM_ENABLED` flags to `"false"`, rely on `FEATURE_FALLBACK_ON_LLM_FAILURE`, and monitor usage in Cost Explorer.

### Issue: Frontend validation not updating

**Solution:** Hard-refresh or clear browser cache after redeploying the frontend to S3.

---

## Cost Estimation

### Monthly Costs (Development Environment)

**Assumptions:**
- 1,000 API requests/day
- 100 MB data transfer/day
- Minimal usage (dev/testing)

| Service | Usage | Cost |
|---------|-------|------|
| **Lambda** | 30,000 requests/month, 512MB, 5s avg | ~$0.50 |
| **API Gateway** | 30,000 requests/month | ~$0.03 |
| **DynamoDB** | On-demand, minimal reads/writes | ~$1.00 |
| **S3** | 1 GB storage, 1,000 requests | ~$0.05 |
| **CloudWatch Logs** | 1 GB logs/month | ~$0.50 |
| **Data Transfer** | 3 GB/month | ~$0.27 |
| **Total (no LLM)** | | **~$2.35/month** |

### Development with LLMs enabled

**Typical range:** ~$5–15/month for light dev usage.

| Component | Estimate |
|-----------|----------|
| Lambda, API Gateway, DynamoDB, S3, logs | ~$2–4/month (as above) |
| **Bedrock** | ~$2–10/month (usage-dependent) |

**Bedrock usage notes:**
- A single rebalance flow may trigger roughly 5–10 LLM calls
- Rough cost per rebalance request: ~$0.01–0.05
- `FEATURE_FALLBACK_ON_LLM_FAILURE=true` avoids hard failures when Bedrock is unavailable

### Production Costs (Estimated)

**Assumptions:**
- 100,000 API requests/day
- 10 GB data transfer/day
- Active usage

| Service | Usage | Cost |
|---------|-------|------|
| **Lambda** | 3M requests/month, 512MB, 5s avg | ~$50 |
| **API Gateway** | 3M requests/month | ~$3 |
| **DynamoDB** | On-demand, moderate reads/writes | ~$25 |
| **S3** | 10 GB storage, 100K requests | ~$1 |
| **CloudWatch Logs** | 50 GB logs/month | ~$25 |
| **Data Transfer** | 300 GB/month | ~$27 |
| **CloudFront** (optional) | 300 GB/month | ~$25 |
| **Total** | | **~$156/month** |

**Cost Optimization Tips:**
- Use CloudWatch Logs retention (7-30 days)
- Enable DynamoDB auto-scaling for predictable workloads
- Use S3 lifecycle policies for old data
- Consider Reserved Capacity for Lambda in production

---

## Cleanup

Destroying infrastructure **deletes all data** (DynamoDB tables, S3 objects, Lambdas, API Gateway, IAM roles, log groups).

Full teardown and targeted destroys: [Command Reference → Cleanup](#cleanup-commands).

---

## Command Reference

Copy-paste commands for day-2 operations. Defaults assume **dev** in `us-east-1` and naming prefix `asset-management-dev-`.

Set helpers once per shell (from project root):

```bash
export AWS_REGION=us-east-1
export TF_DIR=infra/terraform/environments/dev
export API_URL=$(terraform -chdir="${TF_DIR}" output -raw api_base_url)
export BUCKET=$(terraform -chdir="${TF_DIR}" output -raw frontend_bucket_name)
export API_TOKEN=$(grep '^api_token' "${TF_DIR}/dev.tfvars" 2>/dev/null | sed -n 's/.*= *"\(.*\)".*/\1/p')
```

Use `--profile NAME` on any `aws` or `terraform` command for another account.

### One-command deploy

```bash
./infra/scripts/deploy.sh dev
```

### Deploy infrastructure

```bash
./infra/scripts/package_lambda.sh

cd "${TF_DIR}"
terraform init
terraform apply -var-file=dev.tfvars
```

### Deploy and update frontend

```bash
# From project root (uses API_URL, BUCKET, API_TOKEN from helpers above)
./infra/scripts/publish_frontend.sh "${API_URL}" "${BUCKET}" "${API_TOKEN}"

# Or manual build + sync
cd frontend && npm run build && cd ..
printf 'window.assetManagementConfig = {\n  apiBaseUrl: "%s",\n  apiToken: "%s"\n};\n' "${API_URL}" "${API_TOKEN}" \
  > frontend/dist/frontend/browser/app-config.js
aws s3 sync frontend/dist/frontend/browser/ "s3://${BUCKET}/" --delete --region "${AWS_REGION}"
```

### Get URLs and outputs

```bash
terraform -chdir="${TF_DIR}" output -raw api_base_url
terraform -chdir="${TF_DIR}" output -raw frontend_website_url
terraform -chdir="${TF_DIR}" output
```

### Update Lambda code

```bash
./infra/scripts/package_lambda.sh
cd "${TF_DIR}"
terraform apply -var-file=dev.tfvars -target=module.backend_lambda
# Other functions: -target=module.research_agent_lambda or module.sentiment_mcp_lambda
```

### Logs

```bash
# Live tail
aws logs tail /aws/lambda/asset-management-dev-backend --follow --region "${AWS_REGION}"
aws logs tail /aws/lambda/asset-management-dev-research-agent --follow --region "${AWS_REGION}"
aws logs tail /aws/lambda/asset-management-dev-sentiment-mcp --follow --region "${AWS_REGION}"

# Last hour
aws logs tail /aws/lambda/asset-management-dev-backend --since 1h --region "${AWS_REGION}"

# Bedrock traces
aws logs tail /aws/lambda/asset-management-dev-backend --follow --region "${AWS_REGION}" | grep -i bedrock
```

### Test API endpoints

```bash
# Health (no token)
curl "${API_URL}/health"

# Authenticated /api/* (token from dev.tfvars)
curl -H "x-api-token: ${API_TOKEN}" "${API_URL}/api/portfolios"
curl -H "x-api-token: ${API_TOKEN}" "${API_URL}/api/preferences/client_demo"

# Market stream (SSE)
curl -N -H "x-api-token: ${API_TOKEN}" "${API_URL}/api/market/stream"
```

### DynamoDB

```bash
aws dynamodb list-tables --region "${AWS_REGION}" \
  --query 'TableNames[?contains(@, `asset-management-dev`)]'

aws dynamodb scan --table-name asset-management-dev-portfolios --max-items 5 --region "${AWS_REGION}"
aws dynamodb scan --table-name asset-management-dev-preferences --max-items 5 --region "${AWS_REGION}"

aws dynamodb get-item \
  --table-name asset-management-dev-portfolios \
  --key '{"account_id": {"S": "acct_demo"}}' \
  --region "${AWS_REGION}"

aws dynamodb delete-item \
  --table-name asset-management-dev-preferences \
  --key '{"client_id": {"S": "client_demo"}}' \
  --region "${AWS_REGION}"
```

### Lambda

```bash
aws lambda list-functions --region "${AWS_REGION}" \
  --query 'Functions[?contains(FunctionName, `asset-management-dev`)].FunctionName'

aws lambda get-function-configuration \
  --function-name asset-management-dev-backend \
  --region "${AWS_REGION}"

aws lambda get-function-configuration \
  --function-name asset-management-dev-backend \
  --query 'Environment.Variables' \
  --region "${AWS_REGION}" | grep FEATURE_

aws lambda update-function-configuration \
  --function-name asset-management-dev-backend \
  --environment "Variables={SEED_DEFAULT_PORTFOLIOS=false}" \
  --region "${AWS_REGION}"

aws lambda invoke \
  --function-name asset-management-dev-backend \
  --region "${AWS_REGION}" \
  --payload '{"rawPath": "/health", "requestContext": {"http": {"method": "GET"}}}' \
  response.json
cat response.json
```

Prefer Terraform for durable env changes (see [LLM Features](#llm-features-aws-bedrock)).

### S3

```bash
aws s3 ls "s3://${BUCKET}/" --region "${AWS_REGION}"
aws s3 cp "s3://${BUCKET}/app-config.js" - --region "${AWS_REGION}"
aws s3 sync frontend/dist/frontend/browser/ "s3://${BUCKET}/" --delete --region "${AWS_REGION}"
aws s3 rm "s3://${BUCKET}" --recursive --region "${AWS_REGION}"
```

### Cleanup commands

```bash
# Full teardown
aws s3 rm "s3://${BUCKET}" --recursive --region "${AWS_REGION}"
cd "${TF_DIR}"
terraform destroy -var-file=dev.tfvars

# Verify nothing left
aws dynamodb list-tables --region "${AWS_REGION}" \
  --query 'TableNames[?contains(@, `asset-management-dev`)]'
aws lambda list-functions --region "${AWS_REGION}" \
  --query 'Functions[?contains(FunctionName, `asset-management-dev`)]'

# Targeted destroy
cd "${TF_DIR}"
terraform destroy -var-file=dev.tfvars -target=module.backend_lambda
terraform destroy -var-file=dev.tfvars -target=aws_dynamodb_table.preferences
```

### Operations troubleshooting

```bash
# Recent ERROR lines
aws logs filter-log-events \
  --log-group-name /aws/lambda/asset-management-dev-backend \
  --filter-pattern "ERROR" \
  --max-items 10 \
  --region "${AWS_REGION}"

# API Gateway stages
API_ID=$(aws apigatewayv2 get-apis --region "${AWS_REGION}" \
  --query 'Items[?Name==`asset-management-dev-api`].ApiId' --output text)
aws apigatewayv2 get-stages --api-id "${API_ID}" --region "${AWS_REGION}"

# Lambda execution role policies
aws iam get-role --role-name asset-management-dev-backend-role
aws iam list-attached-role-policies --role-name asset-management-dev-backend-role

# CORS preflight
curl -X OPTIONS "${API_URL}/api/portfolios" \
  -H "Origin: http://localhost:4200" \
  -H "Access-Control-Request-Method: GET" \
  -v
```

### Metrics

Time range for `get-metric-statistics` (portable):

```bash
END=$(date -u +%Y-%m-%dT%H:%M:%S)
START=$(python3 -c "from datetime import datetime, timedelta; print((datetime.utcnow()-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S'))")
```

```bash
# Lambda invocations (last hour)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=asset-management-dev-backend \
  --start-time "${START}" --end-time "${END}" \
  --period 300 --statistics Sum --region "${AWS_REGION}"

# Lambda errors
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=asset-management-dev-backend \
  --start-time "${START}" --end-time "${END}" \
  --period 300 --statistics Sum --region "${AWS_REGION}"

# DynamoDB read capacity
aws cloudwatch get-metric-statistics \
  --namespace AWS/DynamoDB \
  --metric-name ConsumedReadCapacityUnits \
  --dimensions Name=TableName,Value=asset-management-dev-portfolios \
  --start-time "${START}" --end-time "${END}" \
  --period 300 --statistics Sum --region "${AWS_REGION}"
```

### Cost monitoring

```bash
# Current month by service (requires Cost Explorer API permissions)
aws ce get-cost-and-usage \
  --time-period Start=$(date +%Y-%m-01),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --group-by Type=SERVICE
```

See [Cost Estimation](#cost-estimation) for typical dev/prod ranges.

### AWS CLI tips

- `--profile prod` — alternate account
- `--region us-west-2` — override region
- `--output json | jq` — parse JSON output
- `--query 'Items[0].Name'` — JMESPath filter
- `--dry-run` — validate without executing (where supported)

---

## Advanced Topics

### Custom Domain Setup

1. **Create ACM certificate** (in us-east-1 for CloudFront):
   ```bash
   aws acm request-certificate \
     --domain-name app.yourdomain.com \
     --validation-method DNS \
     --region us-east-1
   ```

2. **Create CloudFront distribution:**
   ```hcl
   # Add to main.tf
   resource "aws_cloudfront_distribution" "frontend" {
     # ... configuration
   }
   ```

3. **Update Route53:**
   ```hcl
   resource "aws_route53_record" "frontend" {
     zone_id = var.route53_zone_id
     name    = "app.yourdomain.com"
     type    = "A"
     alias {
       name    = aws_cloudfront_distribution.frontend.domain_name
       zone_id = aws_cloudfront_distribution.frontend.hosted_zone_id
     }
   }
   ```

### CI/CD Pipeline

Example GitHub Actions workflow:

```yaml
name: Deploy to AWS

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v2
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-1
      
      - name: Package Lambda functions
        run: ./infra/scripts/package_lambda.sh
      
      - name: Deploy infrastructure
        run: |
          cd infra/terraform/environments/dev
          terraform init
          terraform apply -auto-approve -var-file=dev.tfvars
      
      - name: Deploy frontend
        run: ./infra/scripts/publish_frontend.sh ...
```

### Multi-Environment Setup

Create separate environments:

```bash
# Staging environment
cp -r infra/terraform/environments/dev infra/terraform/environments/staging
# Edit staging/main.tf and staging.tfvars

# Production environment
cp -r infra/terraform/environments/dev infra/terraform/environments/prod
# Edit prod/main.tf and prod.tfvars
```

---

## Support

### Project documentation
- [STARTUP_GUIDE.md](STARTUP_GUIDE.md) — local setup, LLMs, validation, troubleshooting

### AWS documentation
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [AWS Lambda Developer Guide](https://docs.aws.amazon.com/lambda/)
- [API Gateway Developer Guide](https://docs.aws.amazon.com/apigateway/)

### Logs and monitoring
- [Command Reference](#command-reference) — CLI for logs, metrics, and costs
- CloudWatch Logs: AWS Console → CloudWatch → Log groups
- Lambda metrics: AWS Console → Lambda → Functions → Monitoring

### Getting Help
- Check CloudWatch Logs for errors
- Review Terraform plan output
- Verify IAM permissions
- Check AWS service quotas

---

## Summary

You've successfully deployed the Asset Management Multi-Agent Platform to AWS! 🎉

**What you deployed:**
- 3 Lambda functions (Backend, Research Agent, Sentiment MCP) with optional Bedrock IAM
- 6 DynamoDB tables (Approvals, Audit Events, Portfolios, Sessions, Memory Queue, Preferences)
- 1 API Gateway (HTTP API)
- 1 S3 bucket (static website hosting)
- CloudWatch Logs (monitoring)
- Optional: LLM agents via Amazon Bedrock (see [LLM Features](#llm-features-aws-bedrock))

**Next steps:**
1. Open your frontend URL (`terraform output frontend_website_url`)
2. Run health check and UI verification steps above
3. Bookmark [Command Reference](#command-reference) for redeploys and ops
4. Monitor CloudWatch Logs and Bedrock usage if LLMs are enabled
5. Set up custom domain (optional)

**Estimated monthly cost:** ~$2–5 without Bedrock; ~$5–15 with LLMs in dev; ~$150–200+ for production (higher if Bedrock volume grows)
