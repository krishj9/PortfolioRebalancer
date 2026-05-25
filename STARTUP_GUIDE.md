# Startup Guide

AI-powered portfolio rebalancing with LangGraph orchestration, AWS Bedrock LLMs, A2A and MCP agent protocols, and human-in-the-loop approval workflows.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Running Locally](#running-locally)
3. [LLM Features](#llm-features)
4. [Application Walkthrough](#application-walkthrough)
5. [Preferences & Allocation Validation](#preferences--allocation-validation)
6. [Deploying to AWS](#deploying-to-aws)
7. [Security](#security)
8. [AWS Infrastructure](#aws-infrastructure)
9. [Backend Development (LLM)](#backend-development-llm)
10. [Troubleshooting](#troubleshooting)
11. [Project Structure](#project-structure)
12. [Further Reading](#further-reading)

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Docker + Docker Compose | Latest | Local backend services |
| Node.js + npm | 18+ | Frontend dev server |
| Python | 3.12+ | Backend (via uv) |
| AWS CLI | Latest | Deployment |
| Terraform | 1.5+ | Deployment |

AWS credentials with **Bedrock** (Claude models) and **DynamoDB** are required for LLM features and AWS deployment.

---

## Running Locally

### 1. Start backend services

```bash
docker compose up --build
```

| Service | URL | Purpose |
|---------|-----|---------|
| Backend API | http://localhost:8000 | FastAPI + LangGraph orchestrator |
| Research Agent | http://localhost:8101 | A2A remote research agent |
| Sentiment Agent | http://localhost:8201 | MCP sentiment tool server |
| DynamoDB Local | http://localhost:55000 | Local persistence |

Verify health:

```bash
curl http://localhost:8000/health
curl http://localhost:8101/health
curl http://localhost:8201/health
```

### 2. Start frontend

```bash
cd frontend
npm install
npm start
```

Open **http://localhost:4200**. The dev server proxies `/api/*` to `http://localhost:8000`. No API token is required locally when `API_TOKEN` is unset.

### 3. Stop services

```bash
docker compose down          # stop containers
docker compose down -v       # stop and remove volumes
```

---

## LLM Features

All LLM features are **enabled by default** in `docker-compose.yml`.

### Requirements

- AWS credentials mounted at `~/.aws` (read-only in compose)
- Region `us-east-2` in compose (match your AWS config)
- Bedrock model access for Claude inference profiles in that region

### Feature flags (enabled in local compose)

```bash
FEATURE_MEMORY_AGENT_LLM_ENABLED=true
FEATURE_RESEARCH_AGENT_LLM_ENABLED=true
FEATURE_SENTIMENT_AGENT_LLM_ENABLED=true
FEATURE_REBALANCING_AGENT_LLM_ENABLED=true
FEATURE_RISK_AGENT_LLM_ENABLED=true
FEATURE_TRADE_PROPOSAL_AGENT_LLM_ENABLED=true
FEATURE_FALLBACK_ON_LLM_FAILURE=true
```

With LLMs on, agents use Bedrock for memory retrieval, research, sentiment, rebalancing rationale, risk/policy explanations, and trade proposals. `FEATURE_FALLBACK_ON_LLM_FAILURE=true` keeps the workflow running on deterministic logic if Bedrock fails.

### Models used

Configured in `backend/app/core/config.py`:

| Agent | Model | Notes |
|-------|-------|-------|
| Memory | `us.anthropic.claude-3-5-haiku-20241022-v1:0` | Fast semantic queries |
| Research | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Market analysis |
| Sentiment | `us.anthropic.claude-3-5-haiku-20241022-v1:0` | Sentiment classification |
| Risk | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Policy decisions |
| Trade Proposal | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Trade rationale |

Use cross-region inference profile IDs (prefix `us.`). Direct `anthropic.*` IDs without `us.` often fail for on-demand invocation.

### Disabling LLMs locally

Set all `FEATURE_*_LLM_ENABLED` to `false` in `docker-compose.yml`, then:

```bash
docker compose down && docker compose up --build
```

### Test LLM behavior locally

1. Submit a rebalance from the UI.
2. Tail logs for Bedrock activity:

```bash
docker compose logs -f backend | grep -i bedrock
# or all services
docker compose logs -f
```

3. Check recommendation output for richer policy explanations and agent reasoning.

### Cost (local / dev usage)

- Roughly 5–10 LLM calls per rebalance request
- About **$0.01–0.05** per request depending on models and payload size
- Monitor in **AWS Cost Explorer**

### LLM stack (for developers)

| Component | Location |
|-----------|----------|
| Bedrock adapter (retry, timeout, streaming) | `backend/app/adapters/bedrock.py` |
| Prompt templates | `backend/app/adapters/prompts.py` |
| Response validation | `backend/app/adapters/validation.py` |
| LangGraph orchestrator | `backend/app/services/langgraph_graph.py` |
| Feature flags & model config | `backend/app/core/config.py` |

Per-agent rollout uses feature flags in code:

```python
from app.core.config import get_settings

settings = get_settings()
if settings.feature_flags.memory_agent_llm_enabled:
    # LLM path
    ...
else:
    # deterministic fallback
    ...
```

Design specs: `.kiro/specs/llm-langgraph-integration/` (`design.md`, `requirements.md`, `tasks.md`).

---

## Application Walkthrough

### What the app does

1. **Portfolio Monitor** — simulated market stream showing allocation drift
2. **Auto-recommendations** — drift over 5% triggers LangGraph: Memory → Research → Sentiment → Rebalancing → Risk → Trade Proposal → Approval
3. **Recommendation Review** — sentiment and research before decision
4. **Human approval** — Approve applies trades; Reject dismisses
5. **System Intelligence** — workflow trace, memory timeline, audit trail

### Key interactions

| Action | What happens |
|--------|-------------|
| Page loads | Portfolio starts drifted (67.5% equity vs 60% target) → auto-generates recommendation |
| Approve | Portfolio rebalanced |
| Reject | Recommendation dismissed |
| ↺ Simulate Market Drift | Forces drift, new recommendation |
| ⚙ Preferences | Risk profile and allocation targets (validated) |
| ⬡ View Agent Workflow | System Intelligence page |

---

## Preferences & Allocation Validation

The preferences wizard blocks target allocations that violate the risk profile **max single position** limit (frontend matches backend policy in `backend/app/services/policy.py`).

### Presets

**Aggressive** preset: **85% / 10% / 5%** (equity / fixed income / cash), not 90/10/0, so defaults stay within the usual **85%** concentration cap.

### Validation rules

| Check | Behavior |
|-------|----------|
| Any asset class > `max_single_position_pct` | **Blocking** — red errors, cannot advance |
| Cash target = 0% | **Warning only** — orange message, can still proceed |
| Sum of targets = 100% | Required (existing form validation) |

### UI behavior

- **Concentration errors** — red box listing which class exceeds the limit
- **Cash warning** — orange informational box
- Files: `frontend/src/app/preferences/preferences.component.ts|html|scss`

### Test scenarios

1. **Concentration violation** — Aggressive risk (85% max), set equity to 90% → error, Next blocked.
2. **Zero cash** — e.g. 85/15/0 → warning shown, can proceed.
3. **Valid** — 85/10/5 → no errors, preferences save, rebalance not blocked for concentration.

### Why recommendations get blocked (backend)

- **NON_COMPLIANT** — allocation exceeds `max_single_position_pct`
- **UNRESOLVED** — missing risk profile
- **BLOCKED** — Bedrock Guardrails (rare)

Use **Acknowledge Policy Block** when the UI offers it for blocked recommendations.

---

## Deploying to AWS

Full steps, Bedrock IAM, verification, rollback, and costs: **[AWS_DEPLOYMENT_GUIDE.md](AWS_DEPLOYMENT_GUIDE.md)**.

### Quick deploy

```bash
# Package Lambdas
./infra/scripts/package_lambda.sh

# Infrastructure
cd infra/terraform/environments/dev
terraform init
terraform apply -var-file=dev.tfvars

# Frontend (use terraform outputs for URL and bucket)
cd ../../../..
./infra/scripts/publish_frontend.sh \
  "$(terraform -chdir=infra/terraform/environments/dev output -raw api_base_url)" \
  "$(terraform -chdir=infra/terraform/environments/dev output -raw frontend_bucket_name)" \
  "$(grep '^api_token' infra/terraform/environments/dev/dev.tfvars | cut -d'"' -f2)"
```

### Verify

```bash
API=$(terraform -chdir=infra/terraform/environments/dev output -raw api_base_url)
curl "${API}/health"
# Token required for /api/* — see dev.tfvars api_token
```

Keep `dev.tfvars` out of git (`.gitignore`); it contains `api_token`.

---

## Security

### API token gate

All `/api/*` routes require `x-api-token`. The Angular interceptor sends it from `app-config.js` (set at frontend publish time).

- Missing/wrong token → `401`
- `/health` → no token

### Rate limiting (AWS)

API Gateway stage: burst **20**, sustained **10 req/s** → `429` when exceeded.

### Local development

`API_TOKEN` is not set in `docker-compose.yml`, so the middleware skips validation for local UI + `npm start`.

---

## AWS Infrastructure

Lambdas, API Gateway, and DynamoDB are in **us-east-1**; the dev frontend S3 bucket may be in **us-east-2** (see `dev.tfvars`).

| Resource | Name pattern |
|----------|----------------|
| Backend Lambda | `asset-management-dev-backend` |
| Research Agent | `asset-management-dev-research-agent` |
| Sentiment MCP | `asset-management-dev-sentiment-mcp` |
| API Gateway | `asset-management-dev-api` |
| DynamoDB | `asset-management-dev-{approvals,portfolios,preferences,...}` |
| S3 frontend | from `frontend_bucket_name` in tfvars |

### Monitor logs

```bash
aws logs tail /aws/lambda/asset-management-dev-backend --follow --region us-east-1
aws logs tail /aws/lambda/asset-management-dev-backend --region us-east-1 | grep -i error
aws logs tail /aws/lambda/asset-management-dev-backend --region us-east-1 | grep -i bedrock
```

### Teardown

```bash
cd infra/terraform/environments/dev
terraform destroy -var-file=dev.tfvars
```

---

## Backend Development (LLM)

For running/tests outside Docker:

```bash
cd backend
pip install -e ".[dev]"
# or: uv sync
```

Optional `backend/.env` (override compose defaults). Model IDs must match `config.py` (`us.` profiles):

```bash
AWS_REGION=us-east-2

FEATURE_MEMORY_AGENT_LLM_ENABLED=true
FEATURE_RESEARCH_AGENT_LLM_ENABLED=true
FEATURE_SENTIMENT_AGENT_LLM_ENABLED=true
FEATURE_REBALANCING_AGENT_LLM_ENABLED=true
FEATURE_RISK_AGENT_LLM_ENABLED=true
FEATURE_TRADE_PROPOSAL_AGENT_LLM_ENABLED=true
FEATURE_FALLBACK_ON_LLM_FAILURE=true

LLM_BEDROCK_REGION=us-east-2
LLM_MAX_RETRIES=4
LLM_STANDARD_TIMEOUT=60
```

### Run property tests

```bash
cd backend
pytest tests/property/ -v
pytest tests/property/test_bedrock_adapter_properties.py -v
pytest tests/property/ --cov=app.adapters --cov=app.services --cov-report=term-missing
```

### Verify credentials

```bash
aws sts get-caller-identity
```

---

## Troubleshooting

### Frontend can't reach backend (ECONNREFUSED)

```bash
docker compose up --build
```

### LLM: ResourceNotFoundException

Deprecated model ID — use `us.anthropic.*` profiles in `backend/app/core/config.py`.

### LLM: ValidationException (on-demand throughput not supported)

Same fix: use `us.` inference profile IDs, not bare `anthropic.*`.

### LLM: Bedrock access denied

1. `aws sts get-caller-identity`
2. Bedrock **Model access** in your region (e.g. us-east-2 locally)
3. Confirm `~/.aws` is mounted in compose
4. Fallback should still allow workflows if `FEATURE_FALLBACK_ON_LLM_FAILURE=true`

### Preferences not saving (400)

Financial fields must be `Decimal` on the backend; send numeric strings in JSON, not raw floats.

### Frontend validation not updating after deploy

Hard-refresh or clear browser cache.

### CORS errors on AWS

API Gateway CORS and FastAPI middleware must agree (`GET, POST, PUT, DELETE, OPTIONS`). Redeploy frontend with correct `api_base_url` in `app-config.js`.

### Docker build fails

```bash
docker compose build --no-cache
docker compose up
```

### Port already in use

```bash
lsof -i :8000
kill -9 <PID>
```

### Backend tests: ModuleNotFoundError

```bash
cd backend && pip install -e ".[dev]"
```

---

## Project Structure

```
/
├── frontend/              # Angular SPA (npm start → :4200)
├── backend/               # FastAPI + LangGraph (:8000)
│   ├── app/adapters/      # bedrock, prompts, validation
│   ├── app/agents/        # memory, research, sentiment, …
│   └── app/services/      # langgraph_graph, orchestrator
├── remote-agents/         # A2A research agent (:8101)
├── mcp-servers/           # MCP sentiment (:8201)
├── infra/                 # Terraform + deploy scripts
├── docker-compose.yml     # Local stack (LLM flags, AWS mount)
└── AGENT_CONTEXT.md       # Deep technical context for AI agents
```

---

## Further Reading

| Document | Purpose |
|----------|---------|
| [AGENT_CONTEXT.md](AGENT_CONTEXT.md) | Architecture, contracts, agents, known issues |
| [AWS_DEPLOYMENT_GUIDE.md](AWS_DEPLOYMENT_GUIDE.md) | AWS deploy, Bedrock, command reference |
| `.kiro/specs/llm-langgraph-integration/` | LLM integration design and tasks |
