# Agent Context: Asset Management Platform

> **Purpose**: This document gives a new coding agent complete context to understand, navigate, and enhance this codebase. Read it fully before making any changes.

---

## 1. What This Project Is

An **AI-powered portfolio rebalancing decision-support platform** for a personal investor. It monitors a portfolio for allocation drift, runs a multi-agent AI workflow to generate rebalancing recommendations, and requires explicit human approval before any trades are applied.

**Key design constraint**: The system never executes trades autonomously. It produces recommendations and approval artifacts. A human always approves or rejects.

**Live deployment**: AWS (API Gateway + Lambda + S3 + DynamoDB), region `us-east-1`.

---

## 2. Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Angular 19, TypeScript, SCSS, Angular Signals |
| Backend | Python 3.14+, FastAPI, Pydantic v2, LangGraph |
| AI Orchestration | LangGraph (state machine), AWS Bedrock (Claude models) |
| Remote Agents | A2A protocol (Research Agent), MCP protocol (Sentiment Agent) |
| Persistence | DynamoDB (AWS hosted in prod, DynamoDB Local in dev) |
| Infrastructure | Terraform, AWS Lambda (Mangum adapter), API Gateway v2, S3 |
| Packaging | uv (Python), npm (Node) |
| Local Dev | Docker Compose |

---

## 3. Repository Structure

```
/
├── frontend/                        # Angular 19 SPA
│   └── src/app/
│       ├── app.ts                   # Root component - all main page logic
│       ├── app.html                 # Main page template
│       ├── app.scss                 # Global styles (professional dark theme)
│       ├── preferences/             # Preferences wizard (4-step)
│       │   ├── preferences.component.ts
│       │   ├── preferences.component.html
│       │   └── preferences.component.scss
│       └── core/api/                # HTTP service layer
│           ├── health.service.ts
│           ├── market-stream.service.ts
│           ├── portfolio.service.ts
│           ├── preference.service.ts
│           ├── preference.models.ts
│           ├── rebalance.service.ts
│           └── rebalance.models.ts
│
├── backend/
│   └── app/
│       ├── main.py                  # FastAPI app factory, CORS, router registration
│       ├── lambda_handler.py        # Mangum Lambda entrypoint
│       ├── core/config.py           # All settings, feature flags, LLM config
│       ├── api/routes/              # FastAPI route handlers
│       │   ├── rebalance.py         # POST /api/rebalance
│       │   ├── approvals.py         # POST /api/approvals/{id}/actions
│       │   ├── portfolios.py        # GET /api/portfolios
│       │   ├── preferences.py       # GET/PUT /api/preferences/{client_id}
│       │   ├── market.py            # GET /api/market/stream (SSE)
│       │   └── health.py            # GET /health
│       ├── services/
│       │   ├── langgraph_graph.py   # Graph construction, node wiring
│       │   ├── langgraph_nodes.py   # Node implementations
│       │   ├── langgraph_routing.py # Conditional routing logic
│       │   ├── langgraph_state.py   # WorkflowGraphState TypedDict
│       │   ├── orchestrator.py      # Orchestrator class (wraps LangGraph)
│       │   ├── policy.py            # Deterministic policy evaluation
│       │   ├── portfolio.py         # Drift calculation utilities
│       │   └── proposal.py          # Trade proposal utilities
│       ├── agents/                  # Specialist agent implementations
│       │   ├── base.py              # Stage result helpers
│       │   ├── memory.py            # Memory/personalization agent
│       │   ├── risk_compliance.py   # Risk & compliance agent
│       │   ├── rebalancing.py       # Portfolio rebalancing agent
│       │   ├── trade_execution.py   # Trade proposal agent
│       │   ├── research.py          # Research agent (calls A2A)
│       │   ├── sentiment.py         # Sentiment agent (calls MCP)
│       │   ├── market_monitoring.py # Market monitoring agent
│       │   ├── market_simulation.py # Market simulation agent
│       │   └── rebalance_trigger.py # Rebalance trigger agent
│       ├── adapters/
│       │   ├── bedrock.py           # Bedrock model adapter (retry, timeout)
│       │   ├── prompts.py           # YAML prompt template loader
│       │   ├── validation.py        # LLM response validator
│       │   ├── memory.py            # AgentCore memory adapter
│       │   ├── guardrails.py        # Bedrock guardrails adapter
│       │   └── tracing.py           # Tracing provider adapter
│       ├── contracts/               # Pydantic data contracts (schema-first)
│       │   ├── common.py            # ContractModel, WorkflowState, CorrelationMetadata
│       │   ├── domain.py            # PortfolioRecord, RiskProfile, AllocationTarget
│       │   ├── analysis.py          # RecommendationPackage, ApprovalArtifact, DriftItem
│       │   ├── workflow.py          # PortfolioRebalanceRequest, OrchestrationResponse
│       │   ├── market.py            # Market stream event contracts
│       │   └── seed.py              # Seed data contracts
│       └── persistence/
│           ├── memory_store.py      # InMemoryWorkflowStore + WorkflowStore protocol
│           ├── dynamodb_store.py    # DynamoDB implementation
│           └── dependencies.py      # FastAPI dependency injection
│
├── remote-agents/research-agent/   # Standalone A2A research agent service
├── mcp-servers/sentiment/           # Standalone MCP sentiment tool server
│
├── infra/
│   ├── scripts/
│   │   ├── deploy.sh                # Full deploy script
│   │   ├── package_lambda.sh        # Lambda zip packaging
│   │   └── publish_frontend.sh      # S3 frontend deploy
│   ├── terraform/
│   │   ├── environments/dev/
│   │   │   ├── main.tf              # All AWS resources
│   │   │   ├── variables.tf
│   │   │   ├── outputs.tf
│   │   │   └── dev.tfvars
│   │   └── modules/
│   │       ├── python_lambda/       # Lambda module
│   │       └── http_api/            # API Gateway module
│   └── build/                       # Lambda zip artifacts (gitignored)
│
├── docs/
│   ├── 00-product/                  # Product vision and scope
│   ├── 01-architecture/             # System and LangGraph architecture
│   ├── 02-requirements/             # Detailed requirements
│   ├── 03-data-contracts/           # Contract catalog
│   ├── 04-implementation/           # Implementation guide and tasks
│   └── 06-deployment/               # AWS deployment model
│
├── docker-compose.yml               # Local dev: backend + research-agent + sentiment-mcp + dynamodb
└── prompts/                         # YAML prompt templates for LLM agents
```

---

## 4. Core Data Flow

```
User (Browser)
    │
    │  POST /api/rebalance  (PortfolioRebalanceRequest)
    ▼
FastAPI (Lambda via Mangum)
    │
    ▼
LangGraph Orchestrator
    │
    ├── validate_request
    ├── initialize_context_and_trace
    ├── log_request_audit_event
    │
    ├── hydrate_memory ──────────────────┐  (parallel)
    ├── run_research ────────────────────┘
    │
    ├── run_sentiment_analysis
    ├── run_portfolio_rebalancing
    ├── run_risk_policy
    │       │
    │       ├── [COMPLIANT] ──► generate_execution_proposal
    │       └── [NON_COMPLIANT/UNRESOLVED] ──► assemble_recommendation (BLOCKED)
    │
    ├── assemble_recommendation
    ├── apply_output_guardrails
    │       │
    │       ├── [NONE] ──► create_approval_artifact
    │       └── [BLOCKED] ──► emit_workflow_audit_event
    │
    ├── persist_workflow_artifacts
    ├── emit_workflow_audit_event
    └── return_response
            │
            ▼
    OrchestrationResponse (recommendation_package + approval_artifact)
            │
            ▼
    Angular UI (shows recommendation, approve/reject controls)
            │
    User clicks Approve/Reject
            │
    POST /api/approvals/{id}/actions
            │
    Portfolio updated in store (if approved)
```

---

## 5. Key Business Rules

### Policy Evaluation (deterministic, always runs)
- **Concentration check**: If any single asset class exceeds `max_single_position_pct` from the risk profile → `NON_COMPLIANT` → workflow `BLOCKED`
- **Missing risk profile**: → `UNRESOLVED` → workflow `BLOCKED`
- **Allocation tolerance**: Drift items outside tolerance bands are flagged but don't block by themselves

### Workflow States
- `NORMAL` — all checks passed, trades ready for review
- `DEGRADED` — some quality issues but recommendation still usable
- `BLOCKED` — critical violation, no approval possible

### Approval Rules
- `APPROVE` is forbidden when `workflow_state == BLOCKED`
- `REJECT` is always allowed (used for "Acknowledge Policy Block")
- `REJECT` and `REQUEST_REVISION` require a `note`
- Approval hash must match the artifact's `recommendation_hash` (stale artifact protection)

### Allocation Validation (frontend)
- Target allocations must sum to 100%
- No single asset class target may exceed `max_single_position_pct`
- 0% cash triggers an informational warning (not blocking)

---

## 6. LLM Integration

### Feature Flags (all independent, all default false)
```
FEATURE_MEMORY_AGENT_LLM_ENABLED
FEATURE_RESEARCH_AGENT_LLM_ENABLED
FEATURE_SENTIMENT_AGENT_LLM_ENABLED
FEATURE_REBALANCING_AGENT_LLM_ENABLED
FEATURE_RISK_AGENT_LLM_ENABLED
FEATURE_TRADE_PROPOSAL_AGENT_LLM_ENABLED
FEATURE_FALLBACK_ON_LLM_FAILURE=true   ← keeps system working if Bedrock fails
```

### Model Assignments
| Agent | Model | Rationale |
|---|---|---|
| Memory | claude-3-haiku | Fast, cheap, semantic queries |
| Research | claude-3-5-sonnet | Complex synthesis |
| Sentiment | claude-3-haiku | Classification task |
| Rebalancing | claude-3-sonnet | Drift explanation |
| Risk | claude-3-5-sonnet | Critical policy decisions |
| Trade Proposal | claude-3-5-sonnet | High-stakes recommendations |

### LLM Pattern (every agent follows this)
1. Run deterministic logic first (always)
2. If LLM enabled → call Bedrock to enhance/explain
3. Validate LLM output against deterministic result
4. If validation fails or LLM errors → fall back to deterministic
5. Return result with confidence score

### Prompt Templates
Stored as YAML in `prompts/{agent-name}/v1.0.0.yaml`. Each template has:
- `system_prompt`
- `user_prompt_template` (with `{variable}` placeholders)
- `validation.output_schema` (JSON Schema)
- `validation.confidence_threshold`

---

## 7. AWS Deployment

### Live URLs
- **Frontend**: `http://asset-management-dev-frontend-855603407942.s3-website.us-east-2.amazonaws.com`
- **API**: `https://r62c0mmp4i.execute-api.us-east-1.amazonaws.com`

### Lambda Functions (us-east-1)
| Function | Handler | Memory | Timeout |
|---|---|---|---|
| `asset-management-dev-backend` | `app.lambda_handler.handler` | 512 MB | 30s |
| `asset-management-dev-research-agent` | `app.lambda_handler.handler` | 256 MB | 15s |
| `asset-management-dev-sentiment-mcp` | `app.lambda_handler.handler` | 256 MB | 15s |

### DynamoDB Tables (us-east-1)
- `asset-management-dev-approvals`
- `asset-management-dev-audit-events`
- `asset-management-dev-portfolios`
- `asset-management-dev-sessions`
- `asset-management-dev-memory-queue`
- `asset-management-dev-preferences`

### IAM Permissions
- Backend Lambda: DynamoDB (all 6 tables) + Bedrock (`InvokeModel`, `InvokeModelWithResponseStream`)
- Research/Sentiment Lambdas: Bedrock only

### CORS
- API Gateway: `allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]`, `allow_origins = ["*"]`
- FastAPI middleware: same methods, `allow_origins=["*"]`, `allow_headers=["*"]`

### Deploy Commands
```bash
# Package Lambdas
./infra/scripts/package_lambda.sh

# Deploy infrastructure
cd infra/terraform/environments/dev
terraform apply -auto-approve -var-file=dev.tfvars

# Deploy frontend
./infra/scripts/publish_frontend.sh "https://r62c0mmp4i.execute-api.us-east-1.amazonaws.com" "asset-management-dev-frontend-855603407942"
```

---

## 8. Local Development

### Start Everything
```bash
docker compose up --build
```

Services:
- Backend: `http://localhost:8000`
- Research Agent (A2A): `http://localhost:8101`
- Sentiment Agent (MCP): `http://localhost:8201`
- DynamoDB Local: `http://localhost:55000`

### Start Frontend
```bash
cd frontend
npm install
npm start   # http://localhost:4200
```

Frontend proxies `/api/*` to `http://localhost:8000` in dev mode.

### Enable LLMs Locally
Set in `docker-compose.yml` or `backend/.env`:
```
FEATURE_MEMORY_AGENT_LLM_ENABLED=true
FEATURE_RISK_AGENT_LLM_ENABLED=true
# etc.
```
Mount AWS credentials: `~/.aws:/root/.aws:ro` (already in docker-compose.yml).

### Run Tests
```bash
cd backend
uv run pytest tests/ -v
```

---

## 9. API Reference

### POST `/api/rebalance`
Triggers the full LangGraph workflow.

**Request** (`PortfolioRebalanceRequest`):
```json
{
  "actor": { "actor_id": "...", "role": "OWNER", ... },
  "client_profile": { "client_id": "client_demo", ... },
  "account_profile": { "account_id": "acct_demo", ... },
  "portfolio_snapshot": {
    "holdings": [{ "symbol": "EQUITY", "asset_class": "equity", "market_value": "7000", ... }],
    "cash": "1000",
    "total_value": "10000"
  },
  "allocation_target": {
    "asset_class_targets": { "equity": "60", "fixed_income": "30", "cash": "10" },
    "tolerance_bands": { "equity": "5", "fixed_income": "5", "cash": "5" }
  },
  "risk_profile": {
    "risk_level": "balanced",
    "max_single_position_pct": "85",
    "max_sector_pct": "60",
    "allowed_asset_classes": ["equity", "fixed_income", "cash"]
  }
}
```

**Response** (`OrchestrationResponse`):
```json
{
  "workflow_state": "NORMAL|DEGRADED|BLOCKED",
  "recommendation_package": {
    "summary": "...",
    "agent_stages": [...],
    "current_allocation": { "equity": "70", ... },
    "target_allocation": { "equity": "60", ... },
    "proposed_allocation": { "equity": "60", ... },
    "risk_policy": { "verdict": "COMPLIANT", "drift": [...] },
    "proposal": { "proposal_status": "READY_FOR_REVIEW", "trades": [...] },
    "workflow_state": "NORMAL",
    "approval_eligibility": true
  },
  "approval_artifact": {
    "approval_id": "apr_...",
    "approval_status": "PENDING",
    "recommendation_hash": "..."
  }
}
```

### POST `/api/approvals/{approval_id}/actions`
```json
{
  "action": "APPROVE|REJECT|REQUEST_REVISION",
  "actor_id": "local_owner",
  "note": "Required for REJECT and REQUEST_REVISION",
  "expected_recommendation_hash": "..."
}
```

### GET `/api/market/stream`
Server-Sent Events stream. Each event:
```json
{
  "monitoring": {
    "portfolio_value": "10000",
    "current_allocation": { "equity": "67.5", ... },
    "drift": { "equity": "7.5", ... },
    "max_abs_drift_pct": "7.5"
  },
  "rebalance": {
    "signal": "NO_ACTION|WATCH|REBALANCE_NEEDED",
    "reason": "...",
    "threshold_pct": "5.00"
  }
}
```

### GET/PUT `/api/preferences/{client_id}`
GET returns current preferences. PUT updates them.

**PUT body**:
```json
{
  "risk_profile": {
    "risk_profile_id": "risk_balanced",
    "risk_level": "balanced",
    "max_single_position_pct": "85",
    "max_sector_pct": "60",
    "allowed_asset_classes": ["equity", "fixed_income", "cash"]
  },
  "allocation_target": {
    "asset_class_targets": { "equity": "60", "fixed_income": "30", "cash": "10" },
    "tolerance_bands": { "equity": "5", "fixed_income": "5", "cash": "5" }
  },
  "constraints": {
    "tax_strategy": "none",
    "esg_preference": "none",
    "dividend_preference": "no_preference",
    "excluded_sectors": []
  }
}
```

---

## 10. Frontend Architecture

### Angular Signals Pattern
The app uses Angular Signals (not RxJS subjects) for state:
```typescript
protected readonly recommendation = signal<OrchestrationResponse | null>(null);
protected readonly portfolios = signal<PortfolioRecord[]>([]);
protected readonly selectedAccountId = signal<string>('acct_demo');
protected readonly selectedPortfolio = computed(() =>
  this.portfolios().find(p => p.account_profile.account_id === this.selectedAccountId()) ?? null
);
```

### Key Frontend Logic (app.ts)
- `submitRebalance()` — fetches latest preferences, builds request, calls `/api/rebalance`
- `approveRecommendation()` — calls approve action
- `rejectRecommendation()` — calls reject action
- `acknowledgeBlockedRecommendation()` — calls reject with acknowledgment note, clears recommendation
- `simulateDriftAgain()` — enables forced drift mode, triggers auto-recommendation
- `isBlockedByPolicy()` — returns true when `workflow_state === 'BLOCKED'` or `verdict === 'NON_COMPLIANT'`
- `canReviewDecision()` — returns true when status is PENDING and trades exist
- `reviewHeadline()` — returns display string for recommendation status

### Preferences Wizard (preferences.component.ts)
4-step wizard: Risk Profile → Allocation → Constraints → Review

Key computed signals:
- `allocationTotal()` — sum of equity + fixed income + cash targets
- `allocationValid()` — total must equal 100%
- `concentrationWarning()` — array of violations where any target > `max_single_position_pct`
- `cashWarning()` — string warning when cash = 0%
- `hasValidationErrors()` — blocks progression if concentration violated

Auto-population: When user selects a risk level on step 1, step 2 auto-populates safe allocations:
```
conservative → 30/60/10
balanced     → 60/30/10
growth       → 75/20/5
aggressive   → 85/10/5   ← capped at 85% to respect concentration limit
```

### API Config
`frontend/src/app/core/api/api-config.ts` — reads `window.appConfig.apiBaseUrl` at runtime (set by `app-config.js` which is overwritten during S3 deploy).

---

## 11. Data Contracts (Critical)

All contracts extend `ContractModel` (Pydantic `BaseModel`). All financial values use `Decimal`, not `float`.

### Key Types
```python
# WorkflowState
class WorkflowState(StrEnum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"

# PolicyVerdictStatus
class PolicyVerdictStatus(StrEnum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    UNRESOLVED = "UNRESOLVED"

# ApprovalAction
class ApprovalAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_REVISION = "REQUEST_REVISION"
```

### Validation Rules in Contracts
- `PortfolioSnapshot`: `total_value` must equal sum of holdings + cash (±0.01 tolerance)
- `AllocationTarget`: `asset_class_targets` must sum to 100 (±0.01 tolerance)
- `RiskProfile`: `max_single_position_pct` and `max_sector_pct` must be > 0 and ≤ 100

---

## 12. Persistence Layer

### WorkflowStore Protocol
```python
class WorkflowStore(Protocol):
    def save_portfolio(self, portfolio: PortfolioRecord) -> PortfolioRecord: ...
    def get_portfolio(self, account_id: str) -> PortfolioRecord | None: ...
    def list_portfolios(self) -> list[PortfolioRecord]: ...
    def save_approval(self, artifact: ApprovalArtifact) -> ApprovalArtifact: ...
    def get_approval(self, approval_id: str) -> ApprovalArtifact | None: ...
    def update_approval(self, approval_id: str, action: ApprovalActionRequest) -> ApprovalTransitionResult: ...
    def add_audit_event(self, ...) -> AuditEvent: ...
```

Two implementations:
- `InMemoryWorkflowStore` — used locally or in tests (selected via `PERSISTENCE_MODE=memory`)
- `DynamoDBWorkflowStore` — used in AWS (selected via `PERSISTENCE_MODE=aws` or `local`, legacy `DYNAMODB_MODE`)

### Default Seed Portfolios
On startup, the store seeds demo portfolios. The primary demo client is:
- `client_id`: `client_demo`
- `account_id`: `acct_demo`
- Risk profile: balanced, max 85% concentration
- Target allocation: 60% equity, 30% fixed income, 10% cash

---

## 13. Known Issues and Recent Fixes

| Issue | Fix Applied | File |
|---|---|---|
| Preferences save 400 error | Convert string values to `Decimal` (not `float`) | `backend/app/api/routes/preferences.py` |
| CORS blocking PUT requests | Added PUT/DELETE to FastAPI middleware AND API Gateway | `backend/app/main.py`, `infra/terraform/modules/http_api/main.tf` |
| Approve button missing on blocked recommendations | Added `isBlockedByPolicy()` + Acknowledge button | `frontend/src/app/app.ts`, `app.html` |
| 409 on acknowledge | Backend was blocking all actions on BLOCKED state; now only blocks APPROVE | `backend/app/api/routes/approvals.py` |
| Preferences not reloading after save | Added reload in router NavigationEnd handler | `frontend/src/app/app.ts` |
| Risk level not populating allocations | Added `riskLevelToAllocation` map in `nextStep()` | `frontend/src/app/preferences/preferences.component.ts` |
| 90% equity causing policy block | Added concentration validation in preferences wizard | `frontend/src/app/preferences/preferences.component.ts` |

---

## 14. What's Implemented vs Placeholder

### Fully Implemented
- LangGraph graph structure and routing
- Deterministic policy evaluation (`policy.py`)
- Drift calculation (`portfolio.py`)
- Memory agent (with LLM support)
- Risk & compliance agent (with LLM support)
- Approval workflow (approve/reject/acknowledge)
- Market simulation stream (SSE)
- Preferences CRUD
- Portfolio seeding
- Full AWS deployment pipeline
- Frontend: main dashboard, preferences wizard, approval controls

### Placeholder / Stub
- `run_research` node — returns static placeholder output
- `run_sentiment_analysis` node — returns NEUTRAL placeholder
- `run_portfolio_rebalancing` node — calls deterministic logic only (no LLM enhancement yet)
- `generate_execution_proposal` node — simple COMPLIANT/BLOCKED logic, no real trade generation
- AgentCore memory (uses in-memory stub)
- Bedrock guardrails (checks for hardcoded keywords only)
- Tracing (generates URLs but doesn't actually send traces)

---

## 15. Enhancement Opportunities

These are the highest-value areas to build next:

### High Priority
1. **Real trade proposal generation** — `generate_execution_proposal` currently returns a stub. Implement actual buy/sell trade calculation based on drift and portfolio value.
2. **Research agent LLM integration** — `run_research` is a placeholder. Wire up the A2A research agent with real Bedrock calls.
3. **Sentiment agent LLM integration** — `run_sentiment_analysis` returns NEUTRAL. Wire up the MCP sentiment server.
4. **Rebalancing agent LLM enhancement** — Add LLM-powered drift explanation and strategy recommendation on top of the existing deterministic calculation.

### Medium Priority
5. **Real AgentCore memory** — Replace the in-memory stub with actual AgentCore memory API calls for persistent client context.
6. **Bedrock guardrails** — Replace the keyword-check stub with real Bedrock Guardrails API calls.
7. **LangSmith tracing** — Wire up the tracing adapter to actually send traces to LangSmith or Bedrock AgentCore.
8. **Multi-portfolio support** — The UI supports selecting portfolios but the demo only has one. Add more seed portfolios or a portfolio creation flow.

### Lower Priority
9. **Preference history** — The `/preferences/{client_id}/history` endpoint returns an empty list. Implement change tracking.
10. **Memory consolidation** — The `memory_queue` DynamoDB table exists but consolidation logic is not implemented.
11. **Eval suite** — Property-based tests exist but no LLM eval harness. Add evals for recommendation quality.
12. **Auth** — Currently uses `local_owner` as a hardcoded actor. Add real authentication.

---

## 16. Coding Conventions

### Python
- Pydantic v2 for all data models; extend `ContractModel`
- Use `Decimal` for all financial values, never `float`
- Async throughout (`async def` for all route handlers and agent methods)
- Feature flags checked via `get_feature_flags()` (cached singleton)
- Settings via `get_settings()` (cached singleton)
- All agents follow the pattern: deterministic first, LLM enhancement second, fallback on failure

### TypeScript / Angular
- Angular Signals for all reactive state (not RxJS BehaviorSubject)
- `computed()` for derived state
- `inject()` for dependency injection (not constructor injection)
- All API calls go through service classes in `core/api/`
- Financial values come from API as strings, displayed as-is or converted with `Number()`

### Infrastructure
- All AWS resources managed by Terraform in `infra/terraform/environments/dev/`
- Lambda packages built by `infra/scripts/package_lambda.sh` (uses Docker SAM image)
- Frontend deployed by `infra/scripts/publish_frontend.sh` (builds Angular, uploads to S3, writes `app-config.js`)
- Never commit `.tfstate`, `.terraform/`, or real `.tfvars` files

---

## 17. Environment Variables Reference

```bash
# Core
APP_NAME="Asset Management API"
ENVIRONMENT=dev
SCHEMA_VERSION=1.0.0
POLICY_VERSION=1.0.0

# Persistence
PERSISTENCE_MODE=memory|local|aws|firestore   # legacy alias: DYNAMODB_MODE
DYNAMODB_ENDPOINT_URL=http://localhost:55000   # omit for AWS
AWS_REGION=us-east-1

# DynamoDB table names (AWS mode)
APPROVALS_TABLE_NAME=asset-management-dev-approvals
PORTFOLIOS_TABLE_NAME=asset-management-dev-portfolios
PREFERENCES_TABLE_NAME=asset-management-dev-preferences
# ... etc

# Remote agents
RESEARCH_AGENT_URL=http://localhost:8101/a2a/research
RESEARCH_AGENT_REMOTE_ENABLED=true
SENTIMENT_MCP_URL=http://localhost:8201/mcp
SENTIMENT_MCP_ENABLED=true

# Feature flags (all default false)
FEATURE_MEMORY_AGENT_LLM_ENABLED=true
FEATURE_RESEARCH_AGENT_LLM_ENABLED=true
FEATURE_SENTIMENT_AGENT_LLM_ENABLED=true
FEATURE_REBALANCING_AGENT_LLM_ENABLED=true
FEATURE_RISK_AGENT_LLM_ENABLED=true
FEATURE_TRADE_PROPOSAL_AGENT_LLM_ENABLED=true
FEATURE_FALLBACK_ON_LLM_FAILURE=true

# LLM models
LLM_BEDROCK_REGION=us-east-1
LLM_MEMORY_AGENT_MODEL=anthropic.claude-3-haiku-20240307-v1:0
LLM_RESEARCH_AGENT_MODEL=anthropic.claude-3-5-sonnet-20240620-v1:0
LLM_RISK_AGENT_MODEL=anthropic.claude-3-5-sonnet-20240620-v1:0
LLM_TRADE_PROPOSAL_AGENT_MODEL=anthropic.claude-3-5-sonnet-20240620-v1:0

# Market stream
MARKET_STREAM_MAX_EVENTS=20   # 0 = unlimited (local), 20 = bounded (Lambda)
SEED_DEFAULT_PORTFOLIOS=true
```
