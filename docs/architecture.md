# Asset Management Platform — Comprehensive Architecture

## 1. System Overview

An AI-powered portfolio rebalancing decision-support platform. It monitors a simulated portfolio for allocation drift, orchestrates a multi-agent AI workflow to generate rebalancing recommendations, and requires explicit human approval before any trades are applied.

**Core design principle:** The system never executes trades autonomously. Every recommendation goes through a human-in-the-loop approval step backed by an immutable audit trail.

**Key technologies:**
- **Frontend:** Angular 19, TypeScript, Angular Signals, SCSS
- **Backend:** Python 3.14, FastAPI, LangGraph, Pydantic v2
- **AI:** AWS Bedrock (Claude models via cross-region inference profiles)
- **Agent protocols:** A2A (Agent-to-Agent) for Research, MCP (Model Context Protocol) for Sentiment
- **Persistence:** DynamoDB (AWS hosted in prod, DynamoDB Local in dev)
- **Infrastructure:** Terraform, AWS Lambda (Mangum adapter), API Gateway v2, S3

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser (Angular 19 SPA)                                           │
│  - Two-panel dashboard: market monitor + AI recommendation          │
│  - Angular Signals for reactive state                               │
│  - HTTP interceptor injects x-api-token on every request            │
│  - SSE subscription for live market stream                          │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTPS + x-api-token
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  API Gateway v2 HTTP API (us-east-1)                                │
│  - CORS: GET/POST/PUT/DELETE/OPTIONS, allow-headers: x-api-token    │
│  - Stage throttling: 10 req/s sustained, 20 burst                   │
│  - Routes: ANY /{proxy+} → Backend Lambda                           │
│            ANY /a2a/research → Research Agent Lambda                │
│            ANY /mcp → Sentiment MCP Lambda                          │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Backend      │  │ Research     │  │ Sentiment    │
│ Lambda       │  │ Agent Lambda │  │ MCP Lambda   │
│ 512MB · 30s  │  │ 256MB · 15s  │  │ 256MB · 15s  │
│ FastAPI +    │  │ A2A Protocol │  │ MCP JSON-RPC │
│ LangGraph    │  │ Bedrock LLM  │  │ Bedrock LLM  │
└──────┬───────┘  └──────────────┘  └──────────────┘
       │
       ├──→ DynamoDB (6 tables)
       ├──→ Bedrock (Claude Sonnet + Haiku)
       ├──→ Research Agent Lambda (HTTP /a2a/research)
       └──→ Sentiment MCP Lambda (HTTP /mcp)
```

---

## 3. Frontend Architecture

### Technology
- Angular 19 with standalone components
- Angular Signals (`signal()`, `computed()`) for all reactive state — no RxJS BehaviorSubject
- SCSS with a professional dark theme design system
- Vite dev server with `/api/*` proxy to `http://localhost:8000` in development

### Runtime Configuration
The frontend reads `window.assetManagementConfig` from `app-config.js` at runtime:
```javascript
window.assetManagementConfig = {
  apiBaseUrl: 'https://r62c0mmp4i.execute-api.us-east-1.amazonaws.com',
  apiToken: '<token>'
};
```
This file is overwritten during S3 deployment with the actual API Gateway URL and token. In local dev, `apiBaseUrl` defaults to `/api` (proxied by Vite).

### Security: Token Interceptor
An Angular `HttpInterceptorFn` reads `window.assetManagementConfig.apiToken` and injects `x-api-token: <token>` on every outbound HTTP request. If the token is empty (local dev), the header is omitted.

### Key Components
- **`app.ts`** — Root component. Owns all signals: `recommendation`, `portfolios`, `marketEvent`, `submitting`, `chatMessages`. Manages the market stream SSE subscription, auto-generate logic, and approval actions.
- **`app.html`** — Two-panel dashboard. Left: Portfolio Monitor + Position & Targets form. Right: Recommendation Review (situation → what changes → policy checks → AI market intelligence → decision bar → Q&A chat) + Agent Status Strip.
- **`preferences/`** — 4-step wizard: Risk Profile → Allocation → Constraints → Review. Validates concentration limits against `max_single_position_pct` before allowing progression.
- **`intelligence/`** — Separate route `/intelligence`. Three tabs: Workflow Trace, Agent Memory, HITL Audit Trail.
- **`core/api/`** — Service layer: `RebalanceService`, `PortfolioService`, `PreferenceService`, `MarketStreamService`, `HealthService`.

### Auto-Generate Logic
The market stream fires every ~1.5 seconds. On each tick, `maybeAutoGenerateRecommendation()` runs:
1. Skip if `submitting()` is true
2. Skip if signal is not `REBALANCE_NEEDED`
3. If a `NO_ACTION_NEEDED` or `APPROVED` recommendation exists → clear it (stale)
4. If a `PENDING`, `REJECTED`, or `BLOCKED` recommendation exists → leave it alone
5. Submit once — `submitting()` guard prevents double-submission

---

## 4. Backend Architecture

### FastAPI Application (`app/main.py`)
- CORS middleware: all methods, all origins, `x-api-token` in allowed headers
- **Token gate middleware**: reads `API_TOKEN` env var. If set, validates `x-api-token` header on all requests except `/health` and OPTIONS. Returns 401 if missing or wrong. Disabled locally (no env var).
- 8 routers registered under `/api`: rebalance, approvals, portfolios, preferences, market, health, explain, intelligence

### API Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `GET /health` | Public | Health check, no token required |
| `POST /api/rebalance` | Protected | Triggers full LangGraph workflow |
| `POST /api/approvals/{id}/actions` | Protected | APPROVE / REJECT / REQUEST_REVISION |
| `GET /api/portfolios` | Protected | List all portfolios |
| `GET/PUT /api/preferences/{client_id}` | Protected | Read/write client preferences |
| `GET /api/market/stream` | Protected | SSE market simulation stream |
| `POST /api/recommendations/{id}/explain` | Protected | Streaming LLM Q&A about recommendation |
| `POST /api/recommendations/{id}/simulate` | Protected | What-if scenario simulation |
| `GET /api/intelligence/workflow-trace/{account_id}` | Protected | Agent workflow trace |
| `GET /api/intelligence/memory/{client_id}` | Protected | Client memory timeline |
| `GET /api/intelligence/audit/{account_id}` | Protected | HITL audit trail |

### Data Contracts (Pydantic v2)
All models extend `ContractModel` (Pydantic `BaseModel` with `extra="forbid"`). All financial values use `Decimal`, never `float`.

**Key types:**
```python
class WorkflowState(StrEnum):
    NORMAL = "NORMAL"       # All checks passed, trades ready
    DEGRADED = "DEGRADED"   # Quality issues but usable
    BLOCKED = "BLOCKED"     # Critical violation, no approval possible

class PolicyVerdictStatus(StrEnum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    UNRESOLVED = "UNRESOLVED"

class ApprovalAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_REVISION = "REQUEST_REVISION"
```

**Request/Response flow:**
```
PortfolioRebalanceRequest
  → correlation: CorrelationMetadata (request_id, session_id, trace_id)
  → version: VersionMetadata (schema_version, policy_version, environment)
  → actor: ActorContext
  → client_profile: ClientProfile
  → account_profile: AccountProfile
  → portfolio_snapshot: PortfolioSnapshot (holdings + cash + total_value)
  → allocation_target: AllocationTarget (asset_class_targets + tolerance_bands)
  → risk_profile: RiskProfile (risk_level, max_single_position_pct, max_sector_pct)

OrchestrationResponse
  → workflow_state: WorkflowState
  → recommendation_package: RecommendationPackage
      → summary: str (includes sentiment context)
      → agent_stages: list[AgentStageResult]
      → current_allocation, target_allocation, proposed_allocation
      → risk_policy: RiskPolicyResponse (verdict + drift + rule_results)
      → proposal: ExecutionProposalResponse (trades + estimated_impact)
      → sentiment_output: dict | None
      → research_output: dict | None
  → approval_artifact: ApprovalArtifact
      → approval_id, recommendation_hash, approval_status
  → research_output: dict | None
  → sentiment_output: dict | None
```

### Persistence Layer
Two implementations behind a `WorkflowStore` Protocol:
- **`InMemoryWorkflowStore`** — local dev, seeded with 2 demo portfolios on startup
- **`DynamoDBWorkflowStore`** — AWS, selected via `DYNAMODB_MODE=aws`

Both implement: `save_portfolio`, `get_portfolio`, `list_portfolios`, `save_approval`, `get_approval`, `list_approvals`, `update_approval`, `list_audit_events`, `add_audit_event`.

---

## 5. LangGraph Orchestration

### Graph Structure
The workflow is a directed acyclic graph compiled by `build_workflow_graph()` using LangGraph's `StateGraph` API. All state is carried in `WorkflowGraphState` (a `TypedDict`).

```
START
  └─→ validate_request
        ├─[validation failed]─→ emit_workflow_audit_event → return_response → END
        └─[passed]─→ initialize_context_and_trace
                       └─→ log_request_audit_event
                             ├─→ hydrate_memory ──────────┐  (parallel fan-out)
                             └─→ run_research ────────────┘
                                                           └─→ run_sentiment_analysis
                                                                 └─→ run_portfolio_rebalancing
                                                                       └─→ run_risk_policy
                                                                             ├─[COMPLIANT]─→ generate_execution_proposal
                                                                             └─[NON_COMPLIANT/UNRESOLVED]─→ assemble_recommendation (BLOCKED)
                                                                                                    └─→ assemble_recommendation
                                                                                                          └─→ apply_output_guardrails
                                                                                                                ├─[NONE]─→ create_approval_artifact
                                                                                                                │           └─→ persist_workflow_artifacts
                                                                                                                └─[BLOCKED]─→ emit_workflow_audit_event
                                                                                                                               └─→ return_response → END
```

### Nodes

| Node | Type | What it does |
|------|------|-------------|
| `validate_request` | Sync | Validates allocation sum = 100%, holdings qty > 0 |
| `initialize_context_and_trace` | Sync | Assigns trace ID, generates CloudWatch trace URL |
| `log_request_audit_event` | Sync | Writes REQUEST_RECEIVED to audit log |
| `hydrate_memory` | Async | Memory Agent — retrieves client context, LLM synthesis if enabled |
| `run_research` | Async | Research Agent — calls A2A remote service, LLM market analysis |
| `run_sentiment_analysis` | Async | Sentiment Agent — calls MCP server per symbol, aggregates scores |
| `run_portfolio_rebalancing` | Async | Calculates drift deterministically (current vs target allocation) |
| `run_risk_policy` | Async | Evaluates concentration limits, returns COMPLIANT/NON_COMPLIANT/UNRESOLVED |
| `generate_execution_proposal` | Async | Calculates BUY/SELL trades from drift, LLM-enhanced rationale |
| `assemble_recommendation` | Async | Merges all outputs, sets workflow_state, injects sentiment context into summary |
| `apply_output_guardrails` | Async | Content safety check (keyword-based stub, Bedrock Guardrails in future) |
| `create_approval_artifact` | Async | Creates ApprovalArtifact with recommendation hash |
| `persist_workflow_artifacts` | Async | Writes to DynamoDB |
| `emit_workflow_audit_event` | Async | Writes WORKFLOW_COMPLETED to audit log |
| `return_response` | Async | Terminal node — state ready for serialization |

### Conditional Routing

**After `validate_request`:**
- `validation_error` set → skip to `emit_workflow_audit_event`
- No error → proceed to `initialize_context_and_trace`

**After `run_risk_policy`:**
- `COMPLIANT` → `generate_execution_proposal`
- `NON_COMPLIANT` or `UNRESOLVED` → set `workflow_state = BLOCKED`, skip to `assemble_recommendation`

**After `apply_output_guardrails`:**
- `action = NONE` → `create_approval_artifact`
- `action = BLOCKED` → set `workflow_state = BLOCKED`, skip to `emit_workflow_audit_event`

### WorkflowGraphState
The shared state object passed through every node:
```python
class WorkflowGraphState(TypedDict, total=False):
    request_id: str
    session_id: str
    trace_id: str
    request: PortfolioRebalanceRequest
    validation_error: Optional[dict]
    memory_output: Optional[dict]
    research_output: Optional[dict]
    sentiment_output: Optional[dict]
    rebalancing_output: Optional[dict]
    risk_policy_output: Optional[RiskPolicyResponse]
    trade_proposal_output: Optional[dict]
    guardrail_result: Optional[dict]
    approval_artifact: Optional[ApprovalArtifact]
    agent_stages: list[AgentStageResult]
    confidence_map: dict[str, float]
    degraded_reasons: list[str]
    blockers: list[str]
    recommendation_package: Optional[RecommendationPackage]
    workflow_state: WorkflowState
    audit_event_ids: list[str]
```

---

## 6. Agent Implementations

### Memory / Personalization Agent (LOCAL)
- Retrieves client memory items from `LocalMemoryAdapter`
- If `FEATURE_MEMORY_AGENT_LLM_ENABLED`: generates semantic query, synthesizes memories into coherent context, detects conflicts
- Falls back to raw memory retrieval if LLM fails
- Model: `us.anthropic.claude-3-5-haiku-20241022-v1:0`

### Research Agent (A2A REMOTE)
- Backend calls `POST {RESEARCH_AGENT_URL}/a2a/research` with portfolio context
- Research Agent Lambda receives `A2AResearchRequest` with symbols and portfolio snapshot
- If `FEATURE_RESEARCH_AGENT_LLM_ENABLED`: calls Bedrock with prompt asking for market regime, key risks, actionable insights
- Returns: `market_context`, `key_insights`, `regime` (BULL/BEAR/NEUTRAL/VOLATILE), `confidence`
- Falls back to structured placeholder if LLM disabled or fails
- Model: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`

### Sentiment Analysis Agent (MCP TOOL SERVER)
- Backend calls `POST {SENTIMENT_MCP_URL}/mcp` with JSON-RPC 2.0 envelope
- Method: `analyze_symbol_news_sentiment`, params: `{ symbol: "EQUITY" }`
- Sentiment MCP Lambda handles the request, calls Bedrock if `FEATURE_SENTIMENT_AGENT_LLM_ENABLED`
- Returns per-symbol: `overall_sentiment`, `sentiment_score` (-1.0 to 1.0), `summary`, `confidence`
- Aggregated across symbols: `overall_sentiment`, `average_sentiment_score`, `symbol_sentiments`
- Model: `us.anthropic.claude-3-5-haiku-20241022-v1:0`

### Portfolio Rebalancing Agent (LOCAL, DETERMINISTIC)
- Calculates `current_allocation` from holdings market values
- Calculates `drift` per asset class: `current_pct - target_pct`
- Flags items where `abs(drift_pct) > tolerance_pct` as `within_tolerance = False`
- No LLM enhancement currently (placeholder)

### Risk & Compliance Agent (LOCAL, DETERMINISTIC)
- **Concentration check**: largest position > `max_single_position_pct` → `NON_COMPLIANT`
- **Missing risk profile** → `UNRESOLVED`
- **All checks pass** → `COMPLIANT`
- Returns `RiskPolicyResponse` with verdict, drift items, rule results, confidence

### Trade Execution Proposal Agent (LOCAL, DETERMINISTIC + LLM)
- For each drift item where `not within_tolerance`:
  - `trade_value = abs(drift_pct / 100 * total_portfolio_value)`
  - `action = SELL if drift_pct > 0 else BUY`
- Creates `TradeProposal` with trade_id, symbol, action, estimated_value, rationale
- If `FEATURE_TRADE_PROPOSAL_AGENT_LLM_ENABLED`: enhances rationale with Bedrock
- Returns `ExecutionProposalResponse` with `proposal_status`, `trades`, `estimated_impact`
- Model: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`

### Sentiment → Summary Integration
After all agents run, `assemble_recommendation` calls `_sentiment_notes()` which:
- Maps symbol → sentiment from `sentiment_output.symbol_sentiments`
- For each proposed trade: if SELL + POSITIVE sentiment → "driven by drift, not weakness"; if BUY + NEGATIVE → "consider timing"; if BUY + POSITIVE → "sentiment supports this buy"
- Appends the note to the recommendation summary

---

## 7. Human-in-the-Loop Workflow

### Approval Artifact
Created by `create_approval_artifact` node. Contains:
- `approval_id` — unique identifier
- `recommendation_hash` — SHA-256 of recommendation content (stale artifact protection)
- `recommendation` — full `RecommendationPackage`
- `original_portfolio_snapshot` — snapshot at time of recommendation
- `approval_status` — `PENDING | APPROVED | REJECTED | REVISION_REQUESTED`

### Approval Actions (`POST /api/approvals/{id}/actions`)
```python
ApprovalActionRequest:
  action: APPROVE | REJECT | REQUEST_REVISION
  actor_id: str
  note: str | None  # required for REJECT and REQUEST_REVISION
  expected_recommendation_hash: str  # must match artifact hash
```

**Business rules:**
- `APPROVE` is forbidden when `workflow_state == BLOCKED` → 409
- `REJECT` and `REQUEST_REVISION` require a `note` → 422
- Hash mismatch → transition rejected, stale artifact logged

**On APPROVE:**
1. `approval_status` → `APPROVED`
2. New `PortfolioRecord` created from `proposed_allocation`
3. Portfolio saved to DynamoDB
4. `PORTFOLIO_UPDATED_FROM_APPROVAL` audit event written

### Audit Trail
Every significant action writes an `AuditEvent` to DynamoDB:
- `REQUEST_RECEIVED` — on workflow start
- `WORKFLOW_COMPLETED` — on workflow end
- `APPROVAL_ACTION` — on every approve/reject
- `APPROVAL_ACTION_REJECTED` — on hash mismatch
- `PORTFOLIO_UPDATED_FROM_APPROVAL` — on successful approval

---

## 8. Conversational AI (Q&A Chat)

### Endpoint: `POST /api/recommendations/{approval_id}/explain`
Streams an LLM explanation of the recommendation in response to a natural language question.

**Context built from approval artifact:**
- Portfolio ID, workflow state, policy verdict, summary
- Current and target allocation
- Proposed trades with rationale
- Policy rule results (PASS/FAIL)
- Risk profile (risk level, concentration limits)
- Sentiment output (per-symbol scores and labels)
- Research output (market regime, key insights)

**Streaming:** FastAPI `StreamingResponse` with SSE. Each token yields:
```
data: {"type": "token", "content": "..."}
```
Final event:
```
data: {"type": "done"}
```

**System prompt:** Instructs the LLM to answer only from the provided context — no hallucinated market data. Explicitly told it has sentiment and research data and should use it when asked.

**Frontend:** Angular `fetch()` with `ReadableStream`. Tokens stream into the last assistant message character by character. Thinking indicator (bouncing dots) shown before first token arrives.

---

## 9. AWS Infrastructure

### Resources (Terraform-managed)

| Resource | Name | Region |
|----------|------|--------|
| API Gateway v2 | asset-management-dev-api | us-east-1 |
| Backend Lambda | asset-management-dev-backend | us-east-1 |
| Research Agent Lambda | asset-management-dev-research-agent | us-east-1 |
| Sentiment MCP Lambda | asset-management-dev-sentiment-mcp | us-east-1 |
| DynamoDB: approvals | asset-management-dev-approvals | us-east-1 |
| DynamoDB: audit-events | asset-management-dev-audit-events | us-east-1 |
| DynamoDB: portfolios | asset-management-dev-portfolios | us-east-1 |
| DynamoDB: sessions | asset-management-dev-sessions | us-east-1 |
| DynamoDB: memory-queue | asset-management-dev-memory-queue | us-east-1 |
| DynamoDB: preferences | asset-management-dev-preferences | us-east-1 |
| S3 Frontend Bucket | asset-management-dev-frontend-855603407942 | us-east-2 |

### IAM Permissions
- **Backend Lambda**: DynamoDB (GetItem, PutItem, UpdateItem, Scan, Query on all 6 tables) + Bedrock (InvokeModel, InvokeModelWithResponseStream on `*`)
- **Research Agent Lambda**: Bedrock only (InvokeModel, InvokeModelWithResponseStream on `*`)
- **Sentiment MCP Lambda**: Bedrock only (InvokeModel, InvokeModelWithResponseStream on `*`)

### Lambda Configuration
All Lambdas use Mangum as the ASGI adapter to handle API Gateway events. Handler: `app.lambda_handler.handler`.

Backend Lambda environment variables (key ones):
```
DYNAMODB_MODE=aws
RESEARCH_AGENT_URL={api_endpoint}/a2a/research
SENTIMENT_MCP_URL={api_endpoint}/mcp
MARKET_STREAM_MAX_EVENTS=20
SEED_DEFAULT_PORTFOLIOS=true
FEATURE_*_LLM_ENABLED=true (all 6 agents)
FEATURE_FALLBACK_ON_LLM_FAILURE=true
API_TOKEN=<secret>
```

### Bedrock Models
All models use cross-region inference profiles (required for on-demand throughput):

| Agent | Model ID |
|-------|---------|
| Memory, Sentiment | `us.anthropic.claude-3-5-haiku-20241022-v1:0` |
| Research, Risk, Trade Proposal, Rebalancing | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |

Direct model IDs (without `us.` prefix) are not supported for on-demand invocation — they require provisioned throughput.

### Security
**Token gate (FastAPI middleware):**
- Reads `API_TOKEN` env var
- All `/api/*` routes require `x-api-token: <token>` header
- `/health` and OPTIONS preflight are exempt
- Returns 401 if token missing or wrong
- Disabled locally (no env var set)

**API Gateway throttling:**
- Burst limit: 20 concurrent requests
- Rate limit: 10 requests/second sustained
- Returns 429 when exceeded

**Frontend token injection:**
- Angular `HttpInterceptorFn` reads token from `window.assetManagementConfig.apiToken`
- Token baked into `app-config.js` at S3 deploy time
- Empty token in local dev → interceptor skips header

---

## 10. Local Development

### Docker Compose Services

| Service | Port | Notes |
|---------|------|-------|
| Backend | 8000 | FastAPI + LangGraph, all LLMs enabled |
| Research Agent | 8101 | A2A, Bedrock via `~/.aws` mount |
| Sentiment MCP | 8201 | MCP, Bedrock via `~/.aws` mount |
| DynamoDB Local | 55000 | In-memory, no token required |

AWS credentials are mounted read-only: `~/.aws:/root/.aws:ro`. Region: `us-east-2`.

### Frontend Dev Server
```bash
cd frontend && npm start  # http://localhost:4200
```
Vite proxies `/api/*` → `http://localhost:8000`. No token needed locally.

### Feature Flags
All LLM features are enabled by default in `docker-compose.yml`. To disable:
```yaml
FEATURE_MEMORY_AGENT_LLM_ENABLED=false
```

---

## 11. Deployment

### Package and Deploy
```bash
# 1. Package Lambda zips (uses Docker SAM image)
./infra/scripts/package_lambda.sh

# 2. Deploy infrastructure
cd infra/terraform/environments/dev
terraform apply -auto-approve -var-file=dev.tfvars

# 3. Deploy frontend with token
./infra/scripts/publish_frontend.sh \
  "https://r62c0mmp4i.execute-api.us-east-1.amazonaws.com" \
  "asset-management-dev-frontend-855603407942" \
  "<api_token>"
```

The `publish_frontend.sh` script builds Angular, writes `app-config.js` with the API URL and token, then syncs to S3.

### Live URLs
- **Frontend:** http://asset-management-dev-frontend-855603407942.s3-website.us-east-2.amazonaws.com
- **API:** https://r62c0mmp4i.execute-api.us-east-1.amazonaws.com

---

## 12. Key Design Decisions

### Why LangGraph instead of Bedrock native supervisor?
LangGraph gives explicit control over the state machine — conditional routing, parallel fan-out, and the human-in-the-loop node are all first-class graph constructs. Bedrock native supervisor mode abstracts these away, making it harder to enforce the "no autonomous execution" constraint.

### Why A2A for Research, MCP for Sentiment?
These protocols demonstrate different integration patterns:
- **A2A** (Agent-to-Agent): The Research Agent is a standalone service that receives a structured task envelope and returns a structured response. It can be deployed independently and scaled separately.
- **MCP** (Model Context Protocol): The Sentiment Agent exposes a JSON-RPC 2.0 tool interface. Any MCP-compatible client can call it, making it reusable across different orchestrators.

### Why deterministic-first for policy evaluation?
The concentration check and drift calculation are always deterministic. LLMs enhance the explanation and rationale but never override the deterministic verdict. This ensures the system is auditable and the policy outcome is reproducible.

### Why Decimal instead of float for financial values?
Floating-point arithmetic introduces rounding errors that compound in financial calculations. `Decimal` provides exact decimal arithmetic. All Pydantic models enforce this via type annotations.

### Why recommendation hash verification?
The `expected_recommendation_hash` in approval actions prevents approving a stale artifact. If the recommendation changes between when the user sees it and when they click Approve, the hash won't match and the action is rejected. This is a critical safety control.

### Why feature flags per agent?
Each LLM integration can be toggled independently without code changes. This allows gradual rollout, cost control, and safe fallback to deterministic logic if a model is unavailable or produces poor results.
