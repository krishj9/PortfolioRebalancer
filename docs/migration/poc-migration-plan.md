# PortfolioRebalancer → Gemini Enterprise Agent Platform: POC Migration Plan

Status: **Draft for approval** · Written 2026-10-03 · Scope: POC only (not production trading)

Companion docs: [architecture.md](./architecture.md) · [backlog.md](./backlog.md)

Labels used in this document:
- **[VERIFIED-CODE]**: confirmed by reading repository files (static inspection).
- **[VERIFIED-DOC]**: confirmed in official Google Cloud docs (checked 2026-10-03; URLs in architecture.md §7).
- **[PROPOSAL]**: a recommendation in this plan.
- **[UNVERIFIED]**: an assumption that has a matching validation task in Phase 0.

---

## 1. Repository findings

### 1.1 What the repository contains [VERIFIED-CODE]

| Area | Files | Notes |
|---|---|---|
| API (FastAPI) | `backend/app/main.py`, `backend/app/api/routes/*.py` | Routes: rebalance, approvals, explain (SSE), intelligence, market (SSE), portfolios, preferences, health |
| Live orchestration | `backend/app/services/orchestrator.py` → `Orchestrator.run` | **Plain sequential Python.** This is what `POST /api/rebalance` uses (`routes/rebalance.py:get_orchestrator`) |
| LangGraph graph | `backend/app/services/langgraph_graph.py` (`build_workflow_graph`, `LangGraphOrchestrator`), `langgraph_nodes.py`, `langgraph_routing.py`, `langgraph_state.py` | 16 nodes with parallel memory/research fan-out and 3 conditional edges. **Not wired to any route.** Compiled with `graph.compile()`, so it has **no checkpointer** |
| Deterministic calculations | `services/portfolio.py` (allocation/drift), `services/policy.py` (concentration/tolerance verdict), `services/proposal.py` (`generate_execution_proposal`, which creates real BUY/SELL trades from drift) | Reusable unchanged. AGENT_CONTEXT.md §14 says trade generation is a stub, but it's outdated: `proposal.py` already generates trades |
| Agents | `backend/app/agents/*.py` | Rule: deterministic first, optional LLM second, fallback on failure. `Orchestrator` builds agents **without** an LLM adapter, so the live path is fully deterministic |
| Approval | `agents/human_approval.py`, `routes/approvals.py` | SHA-256 recommendation hash with stale-artifact check. APPROVE is blocked when the workflow is BLOCKED. `approval_id = new_id("apr")` is random on every run |
| LLM adapter | `adapters/bedrock.py` (`BedrockModelAdapter.invoke_model`, `invoke_model_streaming`) | Bedrock/Claude only. Used directly by `routes/explain.py` |
| Prompts | `prompts/*/v1.0.0.yaml`, `adapters/prompts.py`, `adapters/validation.py` | Model-agnostic YAML. Reusable |
| Memory / guardrails / tracing | `adapters/memory.py` (`LocalMemoryAdapter`), `adapters/guardrails.py` (keyword list), `adapters/tracing.py` (no-op) | All stubs |
| Persistence | `persistence/memory_store.py` (`WorkflowStore` Protocol, `InMemoryWorkflowStore`), `persistence/dynamodb_store.py`, `persistence/dependencies.py` | Clean protocol seam. Preferences are stored **inside** `PortfolioRecord`. The sessions and memory-queue tables are created but unused |
| Remote agents | `remote-agents/research-agent` (A2A), `mcp-servers/sentiment` (MCP) | Placeholder content. Callers fall back locally (`agents/research.py`, `agents/sentiment.py`) |
| Frontend | `frontend/` (Angular), `core/api/api-config.ts` reads `window.appConfig.apiBaseUrl` | Portable. Only the deploy target changes |
| Infra | `infra/terraform` (AWS Lambda, API GW, DynamoDB), `infra/scripts/*.sh`, `backend/Dockerfile`, `docker-compose.yml` | AWS only. **No CI pipeline** (no `.github/`) |
| Tests | `backend/tests/` (rebalance, portfolios, health, market stream, lambda handler, contracts, property tests incl. LangGraph routing) | `test_rebalance.py` covers the live `Orchestrator` path |
| Sample data | `seeds/*.jsonl`, default seed portfolio `acct_demo` in the stores | Enough for the demo |

### 1.2 Traced workflow (live path) [VERIFIED-CODE]

`frontend app.ts submitRebalance()` → `POST /api/rebalance` → `Orchestrator.run`:
1. Audit `REQUEST_RECEIVED`, then `save_portfolio`.
2. Memory, Research, and Sentiment agents (deterministic or local fallback).
3. `PortfolioRebalancingAgent` → current allocation and drift (`portfolio.py`).
4. `RiskComplianceAgent` → `evaluate` verdict (`policy.py`).
5. `TradeExecutionProposalAgent` → `generate_execution_proposal` (`proposal.py`).
6. Build `RecommendationPackage` → `HumanApprovalWorkflowAgent` creates `ApprovalArtifact` + hash → `save_approval` → audit.
7. UI → `POST /api/approvals/{id}/actions` → hash check → portfolio updated if approved.

Optional: `POST /api/explain/{approval_id}/explain` streams a Bedrock explanation.

### 1.3 Defects found by inspection & runtime execution [VERIFIED-CODE, confirmed in P0-02]

| # | Defect | Location | Impact on migration |
|---|---|---|---|
| D0a | `validate_request` performs `abs(total - 1.0) > 0.01` comparing `Decimal` (sum is `100`) against `float`, raising `TypeError` | `langgraph_nodes.py:44` | Initial validation step fails immediately on standard rebalance request |
| D0b | `hydrate_memory` and `run_research` run in parallel but both return the full `state` dict rather than delta dicts, causing `InvalidUpdateError` on concurrent keys like `request_id` | `langgraph_graph.py:92-98`, `langgraph_nodes.py` | LangGraph parallel fan-out fails with `INVALID_CONCURRENT_GRAPH_UPDATE` |
| D1 | `create_approval_artifact` imports `ApprovalArtifact` from `app.contracts.domain` (missing, raises `ImportError`) and passes mismatched fields (`request_id`, `session_id`, `created_at`) | `langgraph_nodes.py:222-250` | Approval artifact generation fails |
| D2 | `persist_workflow_artifacts` and `emit_workflow_audit_event` only log | `langgraph_nodes.py:253-310` | The graph path doesn't persist anything to the store |
| D3 | Agents call `self.bedrock_adapter.invoke(...)`, but the adapter defines only `invoke_model` | `agents/memory.py:182,251,312`, `trade_execution.py:170,230` (and others) | The LLM path silently falls back to deterministic output |
| D4 | `approval_id` is random per request | `agents/human_approval.py` | Repeating a request creates duplicate proposals |
| D5 | Actor is hardcoded (`local_owner`), and CORS is `*` | `main.py`, frontend | Needs basic auth for the cloud POC |

### 1.4 Tests

Baseline test suite execution in Python 3.14 (`PERSISTENCE_MODE=memory`) was completed in task **P0-01** (22 passed, 11 pre-existing failures triaged). In task **P0-02**, `backend/tests/test_graph_parity.py` was created to prove sequential `Orchestrator` works as a baseline and LangGraph fails at the documented defects (D0a, D0b, D1).

### 1.5 Reuse classification [PROPOSAL]

| Classification | Items |
|---|---|
| Reuse unchanged | `services/portfolio.py`, `policy.py`, `proposal.py`; `contracts/*`; `agents/human_approval.py` (except ID); `routes/approvals.py` logic; prompts YAML; `langgraph_routing.py`, `langgraph_state.py`; frontend components |
| Small adaptation | `langgraph_nodes.py` (D1, D2, tool-client calls); `langgraph_graph.py` (inject tool client and model adapter); `routes/rebalance.py` (invoke Agent Runtime); `persistence/dependencies.py` (+Firestore); new `adapters/gemini.py` matching `BedrockModelAdapter`'s signatures; `adapters/memory.py` (+Memory Bank); `core/config.py`; `Dockerfile`; `api-config`/deploy script |
| Defer | Research A2A and Sentiment MCP services (keep local fallbacks with remote flags off); LLM enhancement for every agent other than one explanation; preference history; memory-queue consolidation; AWS Terraform (leave in place) |

---

## 2. Existing-framework decision [PROPOSAL]

**Decision: keep LangGraph and deploy the existing `StateGraph` to Agent Runtime with the custom agent template, without moving to ADK.**

Rationale:
- The repo has two orchestrators. The LangGraph graph is the designed target (see `docs/01-architecture/langgraph-orchestration.md`). The sequential `Orchestrator` is the working path. The graph already calls the same agent classes, so making it the live path takes only small fixes (D1, D2), not a rewrite.
- Agent Runtime's prebuilt `LanggraphAgent` template wraps a model plus tools (a ReAct-style agent). It doesn't wrap an arbitrary existing `StateGraph` [VERIFIED-DOC]. The **custom agent template** (a Python class with `set_up()` and `query()`/`stream_query()`) does. Its documented example builds a LangGraph graph in `set_up()` [VERIFIED-DOC]. We wrap `build_workflow_graph()` the same way.
- No checkpointer is used today, so there's no checkpoint blocker.

**Fallback if Phase 0 spike P0-03 fails** (for example, packaging or dependency issues on Agent Runtime): run the same graph in-process in the Cloud Run API, then retry Agent Runtime later. That still proves Cloud Run, Firestore, and Gemini. Record the blocker. Don't switch frameworks.

**ADK** is optional and comes later (§7).

### Checkpoint position

- The graph is a single-shot pipeline (one request → one recommendation). Human approval happens **outside** the graph through `/api/approvals`, against a persisted `ApprovalArtifact`. Exact checkpoint recovery is **not** needed.
- Restart model: if a run fails, the user retries. Proposal writes are idempotent (P1-07), so retries don't create duplicates.
- Agent Runtime docs show LangGraph checkpointing only through a `checkpointer_builder` backed by AlloyDB or Cloud SQL for PostgreSQL [VERIFIED-DOC]. Sessions and Memory Bank are **not** documented as checkpoint stores. **We don't add Cloud SQL or claim a managed checkpoint replacement.**

---

## 3. Architecture recommendation (summary)

Full diagrams are in [architecture.md](./architecture.md).

| Component | Hosts | Phase |
|---|---|---|
| Agent Runtime (custom template) | `RebalanceGraphApp` wrapping `build_workflow_graph()`; Gemini calls for explanation; Sessions/Memory Bank calls | 1 (Sessions/Memory in 2) |
| Cloud Run `rebalancer-api` | Existing FastAPI routes; invokes Agent Runtime for `/api/rebalance`; approvals, portfolios, preferences, explain, market SSE | 1 |
| Cloud Run `rebalancer-tools` | Deterministic tools: get portfolio, compute drift, evaluate policy, generate proposal, persist proposal (idempotent). Plain HTTPS/JSON, the same FastAPI codebase with a different router set | 1 |
| Firestore (Native) | `portfolios`, `approvals`, `audit_events` | 1 |
| BigQuery | `rebalancer_poc.proposal_events` (small) | 2 |
| Frontend | Angular static build in Cloud Storage behind the same external ALB | 1 (bucket), 3 (ALB) |
| Agent Gateway + Model Armor | Egress gateway on the Runtime; Model Armor template on the gateway | 3 |
| External ALB + Cloud Armor + IAP | Public edge for the API and frontend | 3 |
| Cloud Run Jobs | **None required.** Seeding uses a script (`scripts/seed_firestore.py`) | — |
| Cloud Storage | Only for the frontend bucket and Agent Runtime staging | 1 |

Tool interface: plain authenticated HTTPS/JSON. We only add MCP if P0-05 shows Agent Gateway or Agent Registry needs it for a Cloud Run tool endpoint [UNVERIFIED].

---

## 4. State ownership

| Category | Target | Why | Minimum migration work | Limitation |
|---|---|---|---|---|
| Conversation history | Agent Platform Sessions (events) | Managed, per user/session, and the source for memory generation [VERIFIED-DOC] | Create a session per UI "conversation". Append the user request event and the final summary event through the API (not ADK) | Without ADK, the app calls the Sessions API explicitly |
| Temporary conversational context | Session state, plus `WorkflowGraphState` in-process | Exists only during the run | Put `session_id` in graph state (field already exists) | Graph state isn't persisted between runs (by design) |
| Cross-session preferences (presentation style) | Memory Bank, scope `{"user_id": <actor_id>}` | Cross-session personalization [VERIFIED-DOC] | `MemoryBankAdapter` implementing `LocalMemoryAdapter.retrieve` | Generated memories are non-authoritative and may be imperfect |
| Investment preferences (risk, targets, constraints) | **Firestore** `portfolios` (inside `PortfolioRecord`, as today) | Authoritative business data | Firestore store | Must **not** come from Memory Bank |
| Portfolio data | Firestore `portfolios/{account_id}` | Operational record | `FirestoreWorkflowStore` implementing `WorkflowStore` | — |
| Allocation models | Firestore (embedded in `PortfolioRecord.allocation_target`) | Same as today; no new model | None beyond the store | — |
| Proposals and approval state | Firestore `approvals/{approval_id}` | Existing `ApprovalArtifact` already holds status and hash | Store, deterministic ID, transactional update | — |
| Audit events | Firestore `audit_events` | Existing behavior | Store | — |
| Checkpoints | **None** | Single-shot graph; approval lives outside the graph | — | Checkpoint limitation: LangGraph checkpointers (AlloyDB/Cloud SQL) are excluded in this POC because conversation history is captured via Sessions and workflow execution is single-shot and idempotent. DynamoDB session/memory-queue tables are retired from the GCP configuration (P2-07). |
| Analytics | BigQuery `proposal_events` | Small SQL demo | Best-effort row insert on proposal create and approval action | Not the system of record |
| Files and reports | None (frontend bucket only) | The app produces no files | — | — |

### Memory demonstration [PROPOSAL]
- **Scenario:** In session A the user says, "Keep explanations short and always show a trade table." In session B (a new conversation), the explanation is short and includes a trade table without the user asking again.
- **Trigger:** after a run completes, the tool/agent path calls Memory Bank memory generation from the Session (Sessions as the data source [VERIFIED-DOC]). It runs asynchronously and doesn't block the response.
- **Retrieval point:** the `hydrate_memory` node. Results feed only the explanation prompt.
- **Scope:** `{"user_id": actor_id}` (authenticated user from IAP, or `demo_user` in Phase 2 before IAP).
- **Context limit:** top 5 memories, 1,000 characters total. Restrict memory topics to presentation preferences (topic configuration [UNVERIFIED], task P2-04).
- **Correction/deletion:** a `DELETE /api/memory/{memory_id}` admin endpoint that calls the platform delete-memory operation. The exact SDK method is [UNVERIFIED] (P2-05). Re-stating a preference updates it through consolidation [VERIFIED-DOC].

### Context envelope (explanation prompt)
1. System instructions (`prompts/rebalancing-agent` or `trade-proposal-agent`).
2. The last N session events (N ≤ 6).
3. Retrieved memories, marked as *user preferences, non-authoritative*.
4. The current portfolio from the tool service.
5. Structured drift, policy, and proposal JSON from the tools.
6. Proposal status.

Rules: tool and Firestore data override memory. Tool output and imported text go in delimited data blocks and are never treated as instructions. Numbers in the explanation must come from item 5. The existing `adapters/validation.py` checks the LLM output against the deterministic result.

---

## 5. Phases

Effort is for one engineer who knows Python and GCP. Ranges include learning time for preview features.

### Phase 0: Inspect and validate (3–5 days)
- **Objective:** Make the repo runnable, confirm the uncertain platform capabilities, and lock the region.
- **Files:** none in `app/` (spike code lives in `spikes/`, then gets deleted). Fix test running in `backend/pyproject.toml` only if needed.
- **Tasks:** P0-01 to P0-06 (backlog).
- **Dependencies:** GCP project with billing; Agent Platform APIs enabled.
- **Acceptance:** existing tests pass locally (or failures are documented). A hello-world custom-template LangGraph agent is deployed and queried. Session create/append and Memory Bank generate/retrieve work from plain Python. Region is chosen with Agent Runtime, Sessions, Memory Bank, and Agent Gateway available. The Gateway→Cloud Run tool registration path is known.
- **Demo:** spike notebook/script output.
- **Risks:** preview features differ by region; Gateway may require MCP or Agent Registry registration for tools.

### Phase 1: Deploy the existing workflow (8–12 days)
- **Objective:** Run one full rebalance → approve flow on GCP using the LangGraph graph on Agent Runtime, the tools on Cloud Run, Firestore, and Gemini.
- **Files:** `langgraph_nodes.py`, `langgraph_graph.py`, new `backend/app/agent_runtime/app.py`, new `backend/app/tools/router.py`, new `backend/app/tools/client.py`, new `backend/app/adapters/gemini.py`, `agents/*.py` (D3 rename), `agents/human_approval.py` (D4), new `persistence/firestore_store.py`, `persistence/dependencies.py`, `routes/rebalance.py`, `routes/explain.py`, `core/config.py`, `backend/Dockerfile`, `backend/pyproject.toml`, new `infra/gcp/terraform/*`, new `infra/gcp/scripts/*`, `frontend/public/app-config.js` (deploy-time).
- **Tasks:** P1-01 to P1-12.
- **Dependencies:** Phase 0.
- **Acceptance:** from the deployed UI, rebalancing `acct_demo` returns a recommendation. Its trades equal the local deterministic output. The approval is persisted in Firestore and can be approved. Repeating the same request doesn't create a second approval. The tool service rejects unauthenticated calls. Structured logs show `run_id` and `proposal_id`.
- **Demo:** UI walkthrough plus Firestore console.
- **Risks:** Agent Runtime packaging of the `app` package; latency from Runtime→Cloud Run hops; Gemini output-format differences from Claude prompts.

### Phase 2: Managed context and analytics (5–8 days)
- **Objective:** Add Sessions, one Memory Bank scenario, and BigQuery.
- **Files:** new `adapters/sessions.py`, `adapters/memory.py` (+`MemoryBankAdapter`), `langgraph_graph.py` (`hydrate_memory_node`), `agent_runtime/app.py`, `tools/router.py` (BigQuery insert), new `routes/memory.py`, `infra/gcp/terraform/bigquery.tf`, sample queries appended to `docs/migration/architecture.md` §6. Delete `sessions` and `memory_queue` config from the GCP path.
- **Tasks:** P2-01 to P2-07.
- **Dependencies:** Phase 1.
- **Acceptance:** session events are visible for a run. The memory scenario passes across two sessions. Deleting the memory removes the behavior. BigQuery query shows proposals by status and average max drift.
- **Risks:** memory generation latency or quality; topic configuration API details.

### Phase 3: Governance and isolation (6–10 days) [COMPLETED]
- **Objective:** Add Agent Gateway, Model Armor, ALB + Cloud Armor + IAP, and private tool access.
- **Files:** `infra/gcp/terraform/network.tf`, `edge.tf`, `run.tf`, `infra/gcp/scripts/gateway_setup.sh`, `infra/gcp/scripts/demo_security.sh`, `agent_runtime/deploy.py`, `routes/*`, `core/auth.py`, `main.py`, `tools/router.py`.
- **Tasks:** P3-01 to P3-08.
- **Dependencies:** Phases 1–2. Basic IAM already exists from Phase 1.
- **Status:** **100% Completed**. All 4 defense-in-depth isolation and governance layers implemented and verified:
  1. Private tools ingress (`INGRESS_TRAFFIC_INTERNAL_ONLY` + `roles/run.invoker` restricted to `sa-runtime`).
  2. Actor extraction from IAP (`x-goog-authenticated-user-email`) and resource authorization on portfolio accounts (`owner_email`), returning 403 Forbidden on mismatch.
  3. Model Armor prompt-injection and jailbreak screening in block mode, suppressing approval artifacts and emitting `CONTENT_BLOCKED` audit events.
  4. Cloud Armor edge WAF security policy with preconfigured OWASP SQLi, XSS, and LFI rules plus rate limiting.
- **Acceptance:** Direct calls to `rebalancer-tools` from internet denied; unauthorized cross-account access fails with 403; prompt-injection attempt triggers `BLOCKED` workflow state and suppresses approvals; Cloud Armor WAF configuration verified. Automated security demo script `./infra/gcp/scripts/demo_security.sh` passes 4/4 checks.

### Phase 4: Observability and validation (4–6 days) [COMPLETED]
- **Objective:** Connect traces end to end, add a dashboard, and run the minimum test set and runbooks.
- **Files:** new `adapters/telemetry.py` (W3C propagator + contextvars + in-memory span recorder), `main.py` (`trace_middleware`), `tools/router.py`, `tools/client.py`, `agent_runtime/app.py`, `langgraph_nodes.py`, `infra/gcp/terraform/monitoring.tf`, `cloudbuild.yaml`, `infra/gcp/scripts/run_poc_tests.sh`, `docs/migration/architecture.md` §6 & §8 (saved queries + operational runbook), tests in `backend/tests/gcp/test_trace_linkage.py` and `test_retry.py`.
- **Tasks:** P4-01 to P4-06.
- **Status:** **100% Completed**. All 6 Phase 4 tasks implemented and verified:
  1. W3C distributed trace context propagation across API → Agent Runtime → Node Spans → Tools → Cloud Trace log correlation formatting (`test_trace_linkage.py`, 7/7 passed).
  2. Cloud Monitoring log-based metrics (`CONTENT_BLOCKED`, `TOOL_DENIED`, `tokens_total`, `rebalance_requests_total`) and operational dashboard resource in `monitoring.tf` plus Log Explorer saved queries in `architecture.md` §6.
  3. Fault injection (`X-Fault-Inject: 503` / `TOOLS_FAULT_INJECT=503`) and idempotent recovery test (`test_retry.py`, 3/3 passed) verifying that retry with the same `Idempotency-Key` yields an identical proposal without duplicates or corrupted store state.
  4. Minimum test set run script `./infra/gcp/scripts/run_poc_tests.sh` executing all 3 test stages with 0 failures (70 passing tests).
  5. Minimal CI Cloud Build configuration (`cloudbuild.yaml`) running tests, building containers with commit tags, pushing to Artifact Registry, and deploying Cloud Run revisions.
  6. Operational runbook expanded in `docs/migration/architecture.md` §8 with exact deployment commands, top 5 troubleshooting scenarios, rollback rehearsal notes, and full teardown instructions.
- **Acceptance:** One Cloud Trace trace spans API → Runtime → tool → Firestore write, filterable by `run_id`. The dashboard shows latency, errors, and token counts. The minimum test set passes. Rollback and teardown runbook verified.
- **Risks:** trace-context propagation across Agent Runtime `query()` (RESOLVED via P0-06 and P4-01).

### Effort summary
| Phase | Effort | Critical path? |
|---|---|---|
| 0 | 3–5 d | yes |
| 1 | 8–12 d | yes |
| 2 | 5–8 d | yes |
| 3 | 6–10 d | yes |
| 4 | 4–6 d | yes |
| **Total** | **26–41 d** | |
| ADK evaluation (optional) | 2 d assessment; 8–15 d if pursued | no |

---

## 6. Required vs optional scope

**Required:** Phases 0–4 as described; one workflow (`acct_demo` rebalance → approve); one memory scenario; one prompt-injection case; one blocked direct-access case.

**Optional / deferred:** LLM enhancement for every agent except the explanation; Research A2A and Sentiment MCP on Cloud Run; Cloud Run Jobs; CI beyond a script plus Cloud Build; preference history; VPC Service Controls; ADK.

**Excluded (per brief):** broker execution, tax, settlement, rounding/fractional engineering, market-data reconciliation, compliance programs, DR, multi-region, tenancy, canary, load testing, cost attribution, large evals.

---

## 7. Optional later phase: ADK evaluation

- **Possible benefit:** ADK agents on Agent Runtime handle Sessions automatically and have first-class Memory Bank integration [VERIFIED-DOC: Sessions overview]. That would remove the manual glue from P2-01 to P2-03.
- **Code that would change:** `langgraph_graph.py`, `langgraph_nodes.py`, `langgraph_routing.py`, `langgraph_state.py`, and `agent_runtime/app.py`. The agents, tools, contracts, and calculations stay.
- **Effort:** 8–15 days, plus re-validating parity and the security demos.
- **Compared with keeping LangGraph:** the current graph is deterministic and single-shot, so ADK's conversational strengths add little. Revisit only if Phase 2's manual Sessions/Memory code proves fragile or the product becomes multi-turn. Not on the POC critical path.

## 8. Risks and open decisions

| # | Risk / decision | Resolution / Mitigation |
|---|---|---|
| R1 | Agent Gateway may only govern MCP or registered endpoints, not arbitrary Cloud Run HTTPS [UNVERIFIED] | P0-05. If MCP is required, add a thin MCP facade in `rebalancer-tools` (justified by the Gateway requirement) |
| R2 | Region availability for Gateway and Memory Bank [UNVERIFIED] | P0-04. Candidate `us-central1` to validate during Phase 0 spike |
| R3 | Private path Runtime → Cloud Run (PSC interface → VPC → internal Cloud Run) [UNVERIFIED in detail] | P3-03 spike. Fallback: Cloud Run ingress `all` with IAM-only authentication plus Gateway (documented as weaker) |
| R4 | Gemini output differs from the Claude-tuned prompts | Keep deterministic fallback (`FEATURE_FALLBACK_ON_LLM_FAILURE=true`). Enable only one LLM explanation |
| R5 | Two orchestrators drift apart | After Phase 1, `Orchestrator` stays only as the local/test fallback, behind `ORCHESTRATION_MODE=local\|agent_runtime` |
| D-1 | Live orchestration framework | **RESOLVED**: Adopt existing LangGraph `StateGraph` as the live path on Agent Runtime |
| D-2 | User authentication mechanism | **RESOLVED**: Identity-Aware Proxy (IAP) on external Application Load Balancer |
| D-3 | GCP Project Configuration | **RESOLVED**: Target GCP Project is `mybrightday-dev` (default region candidate `us-central1` to confirm in P0-04) |
