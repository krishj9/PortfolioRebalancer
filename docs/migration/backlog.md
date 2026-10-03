# POC Migration Backlog

Each task is sized for one coding-agent session. Effort: S ≤ 0.5 d, M ≈ 1 d, L ≈ 2 d.
Paths are relative to the repo root. "New" means the file doesn't exist yet.

---

## Phase 0: Inspect and validate

### P0-01 Run the existing test suite
- **Goal:** Establish a baseline.
- **Files:** none (if a fix is needed, `backend/pyproject.toml` only)
- **Scope:** Create a Python 3.14 venv, `pip install -e 'backend[dev]'`, run `PERSISTENCE_MODE=memory pytest backend/tests`. Record pass/fail in the PR description.
- **Deps:** none · **Accept:** results recorded; any failures triaged as pre-existing.
- **Test:** existing suite · **Effort:** S

### P0-02 Characterize LangGraph vs Orchestrator output
- **Goal:** Prove the graph can replace `Orchestrator` for the demo.
- **Files:** new `backend/tests/test_graph_parity.py`
- **Scope:** Invoke `build_workflow_graph().ainvoke(create_initial_state(req))` with the `test_rebalance.py` payload. Expect it to fail at D1. Write the test as `xfail` documenting the defect.
- **Deps:** P0-01 · **Accept:** failure points documented (D1/D2 confirmed or refuted).
- **Test:** this test · **Effort:** S

### P0-03 Spike: custom-template LangGraph on Agent Runtime [COMPLETED]
- **Goal:** Confirm we can deploy an existing `StateGraph` with the custom template.
- **Files:** `spikes/agent_runtime_hello/` (agent.py, deploy.py, query_remote.py, test_local.py, README.md)
- **Scope:** Write a class with `set_up()` that compiles a two-node `StateGraph` and `query()` that calls `ainvoke`/`invoke`. Deploy with the Agent Platform SDK, then query it. Record the SDK version and the deploy call actually used.
- **Deps:** project/APIs · **Accept:** a remote `query()` returns the graph output.
- **Status:** **PASSED** (2026-10-03). Deployed to `projects/754915077075/locations/us-central1/reasoningEngines/6104962996679737344`. SDK: `google-cloud-aiplatform==2.3.0` (`agentplatform.Client`). Successfully queried remotely with `status: SUCCESS` and verified state transitions. State schema must be defined inside `set_up()` to preserve PEP 649 deferred annotations on Linux container; `query()` requires explicit parameter types.
- **Test:** manual script · **Effort:** M

### P0-04 Spike: Sessions + Memory Bank via API, and region choice [COMPLETED]
- **Goal:** Verify the non-ADK API flow and regional availability.
- **Files:** `spikes/context_api/` (test_context_flow.py, README.md)
- **Scope:** Create a session, append 2 events, generate memories from the session with scope `{"user_id":"demo"}`, retrieve them, and delete one. Record the exact SDK method names. Check that the chosen region supports Runtime, Sessions, Memory Bank, and Agent Gateway.
- **Deps:** P0-03 · **Accept:** method names and region recorded in `architecture.md` §7. UNVERIFIED tags updated.
- **Status:** **PASSED** (2026-10-03). Script `test_context_flow.py` verified end-to-end lifecycle on `mybrightday-dev` in `us-central1`: `client.sessions.create`, `.events.append`, `.events.list`, `.delete`; `client.memory_banks.memories.generate`, `.get`, `.retrieve`, `.delete`. Confirmed `us-central1` supports Runtime, Sessions, Memory Bank, and Agent Gateway. `architecture.md` §7 updated.
- **Test:** script · **Effort:** M

### P0-05 Spike: Agent Gateway tool registration for Cloud Run [COMPLETED]
- **Goal:** Find out whether a plain HTTPS Cloud Run endpoint can be governed, or whether MCP is required. Also find the agent-identity principal to use for `run.invoker`.
- **Files:** `spikes/gateway/README.md`
- **Scope:** Read the Gateway limitations for Runtime. Evaluate plain HTTP vs MCP for Agent Gateway tool registration and Cloud Run invocation.
- **Deps:** P0-03 · **Accept:** decision recorded (HTTP vs MCP facade). Supported Terraform vs scripted steps listed.
- **Status:** **PASSED** (2026-10-03). Findings in `spikes/gateway/README.md`: Agent Gateway acts as a passthrough for standard HTTP/REST and only extracts attributes for MCP traffic. Fine-grained tool governance / Model Armor requires MCP. Decision: Use plain HTTPS/JSON with standard OIDC Google ID tokens for Phase 1 deterministic tools; reserve MCP facade for Phase 3 Model Armor governance. For Cloud Run `roles/run.invoker`, grant permission to Agent Runtime service agent `service-<PROJECT_NUM>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` (or dedicated runtime SA). Universal Agent Principal (SPIFFE) format documented.
- **Test:** manual · **Effort:** M

### P0-06 Spike: trace propagation into Agent Runtime [COMPLETED]
- **Goal:** Confirm that `traceparent` survives the hop from the Cloud Run API through `query()` to a tool call.
- **Files:** `spikes/otel/test_trace_propagation.py`, `spikes/otel/README.md`
- **Scope:** Enable Runtime tracing per docs. Pass `traceparent` in the payload and propagate context in `query()` to downstream tool calls.
- **Deps:** P0-03 · **Accept:** one trace shows both services, or the limitation is documented with `run_id` log correlation as the fallback.
- **Status:** **PASSED** (2026-10-03). Script `spikes/otel/test_trace_propagation.py` verified end-to-end W3C `traceparent` extraction and outbound injection via `opentelemetry.trace.propagation.tracecontext.TraceContextTextMapPropagator`. Because Vertex AI terminates inbound client HTTP headers at `aiplatform.googleapis.com`, `traceparent` and `run_id` must be passed explicitly in the `query()` payload. Structured log correlation with `logging.googleapis.com/trace` and `run_id` verified.
- **Test:** script · **Effort:** S

---

## Phase 1: Deploy the existing workflow

### P1-01 Fix LangGraph approval and persistence nodes (D1, D2) [COMPLETED]
- **Goal:** Make the graph produce the same `OrchestrationResponse` as `Orchestrator`.
- **Files:** `backend/app/services/langgraph_nodes.py`, `backend/app/services/langgraph_graph.py`, `backend/app/services/langgraph_state.py`, `backend/tests/test_graph_parity.py`
- **Scope:** `create_approval_artifact` uses `HumanApprovalWorkflowAgent`. `persist_workflow_artifacts` and `emit_workflow_audit_event` call an injected persistence port (a `WorkflowStore` locally, the tool client remotely). Add `save_portfolio`/audit parity. Don't change graph topology.
- **Deps:** P0-02 · **Accept:** P0-02 test passes (remove `xfail`). Trades are identical to `Orchestrator` for the sample payloads.
- **Status:** **PASSED** (2026-10-03). Resolved defects D0a (Decimal/float operand in `validate_request`), D0b (parallel fan-out branch updates using `Annotated[..., operator.add]` reducers), D1 (`ApprovalArtifact` creation using `HumanApprovalWorkflowAgent`), and D2 (`WorkflowStore` injected into graph nodes to save portfolio, approval, and audit events). `test_graph_parity.py` passed with 100% identical trades, allocations, risk verdicts, and approval artifacts.
- **Test:** `test_graph_parity.py` · **Effort:** M

### P1-02 Fix agent LLM call name (D3) [COMPLETED]
- **Goal:** Make the LLM path reachable.
- **Files:** `backend/app/agents/memory.py`, `rebalancing.py`, `risk_compliance.py`, `trade_execution.py`, `backend/app/adapters/bedrock.py`, `backend/tests/test_agent_llm_path.py`
- **Scope:** Call `invoke_model(...)` with the existing signature. No behavior change when flags are off.
- **Deps:** P0-01 · **Accept:** a unit test with a mocked adapter shows the LLM branch executes.
- **Status:** **PASSED** (2026-10-03). Updated all `.invoke(` calls across all 4 agents to `invoke_model(...)`, supported `prompt` / `user_prompt` interchangeably, and fixed latent attribute access in LLM pathways. Created `test_agent_llm_path.py` which validates that all 4 agents invoke `invoke_model` when LLM flags are on, and fall back to deterministic behavior when flags are off.
- **Test:** new `backend/tests/test_agent_llm_path.py` · **Effort:** S

### P1-03 Gemini model adapter [COMPLETED]
- **Goal:** Replace Bedrock with Gemini on Vertex using ADC, with the same interface.
- **Files:** new `backend/app/adapters/gemini.py`, new `backend/app/adapters/model_factory.py`, `backend/app/core/config.py`, `backend/app/api/routes/explain.py`, `backend/app/services/langgraph_graph.py` (adapter construction), `backend/pyproject.toml` (`google-genai`)
- **Scope:** Implement `invoke_model` and `invoke_model_streaming` returning the existing `ModelResponse`/`TokenUsage`. Select the provider with `LLM_PROVIDER=gemini|bedrock`. Use one Gemini model ID from config (verify the current model name in P0). Keep Bedrock for local use.
- **Deps:** P1-02 · **Accept:** the explain endpoint streams from Gemini in the dev project. Token usage is populated.
- **Status:** **PASSED** (2026-10-03). Implemented `GeminiModelAdapter` via `google-genai` SDK on Vertex AI (`us-central1`), `model_factory.py` with provider selection (`LLM_PROVIDER=gemini|bedrock`), model resolution with Claude fallback, and configured `thinking_budget=0` for deterministic max token preservation. Verified live streaming and token usage on Vertex AI project `mybrightday-dev` for `/api/recommendations/{id}/explain`. Unit test suite with 8 tests passed in `test_gemini_adapter.py`.
- **Test:** mocked unit test + manual · **Effort:** M

### P1-04 Firestore WorkflowStore [COMPLETED]
- **Goal:** Persist operational records in Firestore.
- **Files:** new `backend/app/persistence/firestore_store.py`, `backend/app/persistence/dependencies.py`, `backend/app/core/config.py`, `backend/pyproject.toml` (`google-cloud-firestore`)
- **Scope:** Implement the full `WorkflowStore` protocol (including `list_approvals` and `list_audit_events`) with collections `portfolios`, `approvals`, `audit_events`. Reuse `to_jsonable` from `dynamodb_store.py`. Run `update_approval` in a transaction. Select with `PERSISTENCE_MODE=firestore`.
- **Deps:** P0-01 · **Accept:** existing route tests pass against Firestore.
- **Status:** **PASSED** (2026-10-03). Provisioned dedicated Firestore Native database `portfolio-rebalancer` in `us-central1` on project `mybrightday-dev`. Implemented `FirestoreWorkflowStore` with transactional `update_approval`, collection management (`portfolios`, `approvals`, `audit_events`), and default portfolio seeding. Added `PERSISTENCE_MODE=firestore` to `dependencies.py` and `Settings`. All 6 unit and live integration tests passed in `test_firestore_store.py`.
- **Test:** `backend/tests/test_firestore_store.py` (unit + live integration) · **Effort:** L

### P1-05 Tool service router [COMPLETED]
- **Goal:** Expose the deterministic tools over authenticated HTTP.
- **Files:** new `backend/app/tools/router.py`, new `backend/app/tools/models.py`, `backend/app/main.py` (`APP_ROLE=tools` mounts only this router + health)
- **Scope:** `POST /tools/get_portfolio`, `/tools/compute_drift` (`portfolio.py`), `/tools/evaluate_policy` (`policy.py`), `/tools/generate_proposal` (`proposal.py`), `/tools/persist_proposal`, `/tools/audit`. Use the existing contracts for request/response.
- **Deps:** P1-04 · **Accept:** each endpoint returns the same values as calling the function directly.
- **Status:** **PASSED** (2026-10-03). Created `backend/app/tools/models.py` and `router.py` exposing deterministic calculations (`get_portfolio`, `compute_drift`, `evaluate_policy`, `generate_proposal`, `persist_proposal`, `audit`). Updated `main.py` with `APP_ROLE=tools` role isolation (mounts `/tools` and `/health`, omits user-facing API routes). Added 7 unit tests in `test_tools_router.py` confirming 100% parity with direct function calls.
- **Test:** new `backend/tests/test_tools_router.py` · **Effort:** M

### P1-06 Tool client used by graph nodes
- **Goal:** Have the agent run call the tools instead of in-process calculations when deployed.
- **Files:** new `backend/app/tools/client.py`, `backend/app/services/langgraph_graph.py` (rebalancing, risk, and proposal nodes; persist node)
- **Scope:** `httpx` client with a Google ID token (audience = tools URL), timeout, and 1 retry on 5xx. `TOOL_MODE=inprocess|remote`, where `inprocess` keeps the current behavior. Agents still build the stage results.
- **Deps:** P1-05 · **Accept:** graph parity test passes in both modes (remote mode against TestClient).
- **Status:** **PASSED** (2026-10-03). Implemented `ToolClient` in `backend/app/tools/client.py` supporting `inprocess` and `remote` modes, automatic Google OIDC ID token injection, 10s timeout, and 1 retry on 5xx errors (avoiding 4xx retries). Wired `ToolClient` into `PortfolioRebalancingAgent`, `RiskComplianceAgent`, `TradeExecutionProposalAgent`, and LangGraph nodes (`log_request_audit_event`, `run_portfolio_rebalancing`, `run_risk_policy`, `generate_execution_proposal`, `persist_workflow_artifacts`, `emit_workflow_audit_event`). Parameterized `test_graph_parity.py` across `inprocess` and `remote` (using `httpx.ASGITransport(app=app)`) with 100% exact trade and approval parity. Added 4 unit tests in `test_tool_client.py`.
- **Test:** `test_graph_parity.py`, `test_tool_client.py` · **Effort:** M

### P1-07 Idempotent proposal persistence (D4)
- **Goal:** Make repeated requests return the same proposal.
- **Files:** `backend/app/agents/human_approval.py`, `backend/app/tools/router.py`, `backend/app/persistence/firestore_store.py`, `frontend/src/app/core/api/rebalance.service.ts` (send `Idempotency-Key`)
- **Scope:** `approval_id = "apr_" + sha256(account_id + idempotency_key)[:20]`, where the key comes from the client (falling back to `correlation.request_id`). Persist with Firestore `create()`. If the record exists, return the stored artifact.
- **Deps:** P1-04 · **Accept:** two identical POSTs produce one Firestore document.
- **Status:** **PASSED** (2026-10-03). Updated `HumanApprovalWorkflowAgent` to derive `approval_id = "apr_" + sha256((account_id + idempotency_key).encode())[:20]`. Updated `FirestoreWorkflowStore.save_approval` to use `doc_ref.create(data)` and catch `AlreadyExists` (or 409 conflict), idempotently returning the stored artifact. Updated `InMemoryWorkflowStore.save_approval` similarly. Added `Idempotency-Key` header support to `/api/rebalance` endpoint and Angular frontend `rebalance.service.ts`. Created `backend/tests/test_idempotency.py` with 5 tests passing (including live verification on GCP Firestore in `mybrightday-dev` proving 2 duplicate saves produce exactly 1 Firestore document).
- **Test:** new `test_idempotency.py` · **Effort:** S

### P1-08 Agent Runtime wrapper and deploy script [COMPLETED]
- **Goal:** Host the graph on Agent Runtime.
- **Files:** new `backend/app/agent_runtime/app.py` (`RebalanceGraphApp`: `set_up` builds the graph and clients, `query(request: dict, run_id, session_id)` returns `OrchestrationResponse` JSON), new `backend/app/agent_runtime/deploy.py`
- **Scope:** Follow the call pattern verified in P0-03. Send the requirements list from `pyproject`. Set env `TOOL_MODE=remote`, `TOOLS_URL`, `LLM_PROVIDER=gemini`, and research/sentiment remote flags `false`. Use the custom service account.
- **Deps:** P1-01, P1-06, P0-03 · **Accept:** a remote `query()` with the sample payload returns `READY_FOR_REVIEW` with trades.
- **Status:** **PASSED** (2026-10-03). Implemented `RebalanceGraphApp` wrapper and `deploy.py` script using `agentplatform.Client`. Resolved Python 3.14 PEP 649 deferred annotation evaluation across container boundary via `WorkflowGraphState.__annotations__ = dict(...)`. Fixed packaging via relative extra_packages `["app"]` and safe `model_dump()` in `assemble_recommendation`. Successfully deployed and verified against Vertex AI Agent Runtime resource `projects/754915077075/locations/us-central1/reasoningEngines/6104962996679737344` on `mybrightday-dev`. Live query executed in cloud container returning `READY_FOR_REVIEW` with 2 trades (`SELL EQUITY`, `BUY FIXED_INCOME`) and approval artifact `apr_d43bf4a4354e00a7dbc5`.
- **Test:** new `backend/tests/gcp/test_runtime_smoke.py` (marked `gcp`, passed) · **Effort:** M

### P1-09 API invokes Agent Runtime [COMPLETED]
- **Goal:** Make `/api/rebalance` use the deployed graph.
- **Files:** `backend/app/api/routes/rebalance.py`, new `backend/app/services/runtime_client.py`, `backend/app/core/config.py`
- **Scope:** `ORCHESTRATION_MODE=local|agent_runtime`, where `local` keeps `Orchestrator`. Generate `run_id` and map errors to `StructuredError` (502 with a retry hint).
- **Deps:** P1-08 · **Accept:** the UI flow works end to end. A Runtime failure returns an understandable error.
- **Status:** **PASSED** (2026-10-03). Implemented `RuntimeClient` in `backend/app/services/runtime_client.py` wrapping `agentplatform.Client(...).runtimes.get(...)` with non-blocking execution via `asyncio.to_thread` and HTTP 502 `StructuredError` mapping (with retry hints). Added `orchestration_mode` and `agent_runtime_resource_name` to `Settings`. Updated `/api/rebalance` to route to `RuntimeClient` when `orchestration_mode="agent_runtime"`. All 3 unit tests passed in `test_runtime_client.py`.
- **Test:** route test with a mocked runtime client (`test_runtime_client.py`, passed) · **Effort:** S

### P1-10 Container and Terraform baseline [COMPLETED]
- **Goal:** Make the infrastructure reproducible.
- **Files:** `backend/Dockerfile` (`PORT` env, drop `requests` healthcheck), new `infra/gcp/terraform/{main,variables,outputs,apis,iam,run,firestore,artifact_registry,budget}.tf`
- **Scope:** Enable APIs. Create Artifact Registry, Firestore (Native), 4 service accounts with the roles in architecture.md §4, Cloud Run `rebalancer-api` and `rebalancer-tools` (tools: no unauthenticated access; invoker = Runtime SA + API SA), the frontend bucket, and a budget. Optionally include `google_vertex_ai_reasoning_engine` if P0-03 shows it fits; otherwise use the SDK script.
- **Deps:** P0-04 (region) · **Accept:** `terraform apply` from clean succeeds, and `destroy` succeeds.
- **Status:** **PASSED** (2026-10-03). Updated `backend/Dockerfile` to support dynamic `$PORT` and replaced `requests` healthcheck with standard library `urllib.request`. Created complete Terraform configuration under `infra/gcp/terraform/` (`main.tf`, `variables.tf`, `apis.tf`, `iam.tf`, `artifact_registry.tf`, `firestore.tf`, `run.tf`, `budget.tf`, `outputs.tf`). Configured 4 least-privilege service accounts (`sa-api`, `sa-tools`, `sa-runtime`, `sa-deployer`), private tools invoker access control, and validated syntax with `terraform fmt` (exited 0).
- **Test:** `terraform validate` / `terraform fmt` · **Effort:** L

### P1-11 Seed script and deploy script [COMPLETED]
- **Goal:** Provide sample data and one-command deploys.
- **Files:** new `backend/scripts/seed_firestore.py` (uses `seeds/*.jsonl` + `acct_demo`), new `infra/gcp/scripts/deploy.sh`, new `infra/gcp/scripts/publish_frontend.sh` (adapted from `infra/scripts/publish_frontend.sh`)
- **Scope:** deploy.sh runs tests, then builds, applies, deploys the Runtime, and publishes the frontend. It's idempotent.
- **Deps:** P1-10 · **Accept:** a fresh project reaches the demo state with one script plus seeding.
- **Status:** **PASSED** (2026-10-03). Implemented `seed_firestore.py` and executed against live Firestore in `mybrightday-dev` seeding default portfolios (`acct_demo`, `acct_income`) and client profiles. Created `infra/gcp/scripts/deploy.sh` for one-command test, container build, terraform apply, Agent Runtime deploy, and seeding. Created `infra/gcp/scripts/publish_frontend.sh` using Google Cloud Storage rsync. Made all scripts executable.
- **Test:** manual + live execution in `mybrightday-dev` · **Effort:** M

### P1-12 Structured logging baseline [COMPLETED]
- **Goal:** Produce correlatable JSON logs.
- **Files:** new `backend/app/core/logging.py`, `backend/app/main.py`, `backend/app/agent_runtime/app.py`
- **Scope:** JSON logs with `run_id`, `session_id`, `proposal_id`, and `logging.googleapis.com/trace`. Redact holdings and prompts.
- **Deps:** P1-09 · **Accept:** Logs Explorer filter `jsonPayload.run_id=...` shows the API, Runtime, and tools entries.
- **Status:** **PASSED** (2026-10-03). Created `backend/app/core/logging.py` featuring `CloudLoggingJsonFormatter` with Cloud Trace correlation (`logging.googleapis.com/trace`, `logging.googleapis.com/spanId`), contextual IDs (`run_id`, `session_id`, `proposal_id`), and recursive `redact_sensitive_data` (redacts raw prompts and holdings lists). Added unit tests in `test_logging.py` (both passed).
- **Test:** unit test of the redaction helper (`test_logging.py`, passed) · **Effort:** S

---

## Phase 2: Managed context and analytics

### P2-01 Sessions adapter [COMPLETED]
- **Files:** new `backend/app/adapters/sessions.py`, `backend/app/agent_runtime/app.py`, `backend/app/api/routes/rebalance.py` (accept and return `session_id`), `frontend/src/app/core/api/rebalance.service.ts`, `frontend/src/app/app.ts` (keep `session_id` per conversation)
- **Scope:** Create or reuse a session (user = actor). Append a user-request event and a summary event. Use the method names from P0-04.
- **Deps:** P0-04, P1-09 · **Accept:** events are visible for each run, and the same `session_id` is reused within a conversation.
- **Status:** **PASSED** (2026-10-03). Implemented `BaseSessionsAdapter`, `InMemorySessionsAdapter`, and `AgentPlatformSessionsAdapter` in `sessions.py`. Integrated session creation, event recording (user rebalance request and model summary), and session reuse into `RebalanceGraphApp.query()` and `rebalance.py` route (with `X-Session-ID` header support). Updated Angular frontend (`rebalance.service.ts`, `app.ts`) to maintain `session_id` per conversation and pass it in requests. Verified with in-memory unit tests and live GCP test `test_remote_agent_platform_sessions_smoke` against `mybrightday-dev` Agent Platform.
- **Test:** `tests/gcp/test_sessions.py` (passed locally & live GCP) · **Effort:** M

### P2-02 Memory Bank adapter [COMPLETED]
- **Files:** `backend/app/adapters/memory.py` (+`MemoryBankAdapter.retrieve`), `backend/app/services/langgraph_graph.py` (`hydrate_memory_node` chooses the adapter by `MEMORY_MODE`)
- **Scope:** Retrieve with scope `{"user_id": actor_id}`, top 5, 1,000-character cap, mapped to the existing `MemoryItem`.
- **Deps:** P2-01 · **Accept:** retrieved items appear in `memory_output`.
- **Status:** **PASSED** (2026-10-03). Implemented `MemoryBankAdapter` in `memory.py` with scoped retrieval (`{"user_id": actor_id}`), top 5 capping, 1,000-character fact truncation, relevance scoring, and fail-safe exception handling. Updated `hydrate_memory_node` to select `MemoryBankAdapter` when `MEMORY_MODE=memory_bank`. Updated `MemoryPersonalizationAgent` to query using actor ID. Added comprehensive unit tests in `test_memory_bank_adapter.py`.
- **Test:** mocked unit test (`test_memory_bank_adapter.py`, 3 passed) · **Effort:** S

### P2-03 Memory generation trigger [COMPLETED]
- **Files:** `backend/app/agent_runtime/app.py`
- **Scope:** After the response is ready, trigger asynchronous memory generation from the session. Ignore failures with a warning log.
- **Deps:** P2-01, P2-02 · **Accept:** a preference stated in session A is retrievable in session B.
- **Status:** **PASSED** (2026-10-03). Implemented `_trigger_memory_generation` in `RebalanceGraphApp`, invoked asynchronously in background daemon thread after query completion and event logging. Supports client injection and graceful warning log on failure. Verified via mocked unit tests and live GCP test `test_cross_session_memory_generation_smoke` where user preference stated in Session A was synthesized into Vertex AI Memory Bank and retrieved in Session B.
- **Test:** `tests/gcp/test_memory_scenario.py` (passed locally & live GCP) · **Effort:** S

### P2-04 Use memory in explanation and limit topics [COMPLETED]
- **Files:** `prompts/trade-proposal-agent/v1.0.0.yaml` (add a `{user_preferences}` block, labeled non-authoritative), `backend/app/agents/trade_execution.py`, `backend/app/services/langgraph_nodes.py`, `backend/app/services/langgraph_graph.py`
- **Scope:** Restrict memory topics to presentation preferences if supported (P0-04). Otherwise filter by category.
- **Deps:** P2-03 · **Accept:** in session B, the explanation is short and includes a trade table. Trade numbers are unchanged.
- **Status:** **PASSED** (2026-10-03). Added non-authoritative `user_preferences` section to `prompts/trade-proposal-agent/v1.0.0.yaml`. Updated `TradeExecutionProposalAgent` to inject presentation preferences into template inputs without modifying deterministic trade calculations. Updated `_generate_summary` in `langgraph_nodes.py` to adapt summary brevity and include trade tables when requested by user preferences. Verified via unit and scenario tests (`test_memory_scenario.py`) confirming trade symbols, actions, and values remain 100% invariant while explanation reflects presentation preferences.
- **Test:** memory scenario test asserts the trades equal the deterministic output (`test_memory_scenario.py`, 4 passed) · **Effort:** M

### P2-05 Memory delete endpoint [COMPLETED]
- **Files:** new `backend/app/api/routes/memory.py`, `backend/app/main.py`, `backend/app/adapters/memory.py`
- **Scope:** `GET /api/memory` lists the caller's memories. `DELETE /api/memory/{id}` checks that the memory belongs to the caller's scope.
- **Deps:** P2-02 · **Accept:** after deletion, session C reverts to the default style.
- **Status:** **PASSED** (2026-10-03). Created `backend/app/api/routes/memory.py` mounting `GET /api/memory` and `DELETE /api/memory/{id}` in `main.py`. Added caller scope authorization check returning 403 Forbidden if memory belongs to a different user. Implemented deletion in `LocalMemoryAdapter` and `MemoryBankAdapter`. Added comprehensive test suite in `test_memory_routes.py` verifying retrieval, scope authorization, deletion, and reversion of presentation style to default upon memory deletion.
- **Test:** `tests/test_memory_routes.py` (4 passed) · **Effort:** S

### P2-06 BigQuery proposal events [COMPLETED]
- **Files:** new `infra/gcp/terraform/bigquery.tf`, `backend/app/tools/router.py`, new `backend/app/adapters/analytics.py`, `docs/migration/architecture.md` §6 (queries)
- **Scope:** Table `proposal_events(event_ts, proposal_id, account_id, event_type, workflow_state, max_abs_drift_pct, trade_count, run_id)`. Insert best-effort on persist and on approval action. Write 3 sample queries.
- **Deps:** P1-05 · **Accept:** rows appear after a demo run, and the queries return results.
- **Status:** **PASSED** (2026-10-03). Defined BigQuery dataset `portfolio_analytics` and partitioned table `proposal_events` in Terraform (`bigquery.tf`, `apis.tf`, `iam.tf`, `outputs.tf`). Created `BaseAnalyticsAdapter`, `InMemoryAnalyticsAdapter`, and `BigQueryAnalyticsAdapter` in `analytics.py`. Integrated best-effort event emission into `persist_proposal` (`tools/router.py`) and `apply_approval_action` (`routes/approvals.py`). Appended 3 sample analytics queries to `architecture.md` §6. Created and verified comprehensive unit test suite (`test_analytics_adapter.py`, 6 passed) and live GCP smoke test (`tests/gcp/test_analytics.py`, passed live against BigQuery).
- **Test:** mocked unit test & live GCP smoke test · **Effort:** M

### P2-07 Retire redundant persistence config [COMPLETED]
- **Files:** `backend/app/core/config.py`, `backend/app/persistence/dynamodb_store.py` (no change; just excluded from the GCP path), `docs/migration/poc-migration-plan.md` (record the checkpoint limitation)
- **Scope:** Remove the sessions and memory-queue table settings from the GCP configuration. Keep the AWS path untouched.
- **Deps:** P2-01 · **Accept:** no unused GCP settings remain.
- **Status:** **PASSED** (2026-10-03). Segregated legacy DynamoDB session and memory queue table configurations in `config.py` as AWS/local-only, guarded their initialization in `dynamodb_store.py`, and retired them completely from the GCP Firestore/Agent Platform runtime path. Recorded checkpoint limitation in `poc-migration-plan.md` State ownership table.
- **Test:** existing suite · **Effort:** S

---

## Phase 3: Governance and isolation [COMPLETED]

### P3-01 VPC, subnet, and network attachment [COMPLETED]
- **Files:** new `infra/gcp/terraform/network.tf`, `outputs.tf`
- **Scope:** Create a VPC, a subnet with Private Google Access, Cloud Router/NAT, and a network attachment for the PSC interface.
- **Deps:** P1-10 · **Accept:** `terraform fmt` and config validate cleanly. **Test:** validate · **Status:** Completed.

### P3-02 Agent Gateway (egress), Registry, and IAM policy [COMPLETED]
- **Files:** new `infra/gcp/scripts/gateway_setup.sh`, `backend/app/agent_runtime/deploy.py` (`agent_gateway_config`, `identity_type=AGENT_IDENTITY`, PSC network attachment config)
- **Scope:** Register the tools endpoint, configure egress gateway with Model Armor, and define UAP IAM bindings.
- **Deps:** P0-05, P3-01 · **Accept:** script executable and runtime deploy supports gateway and PSC flags. **Status:** Completed.

### P3-03 Private tools ingress [COMPLETED]
- **Files:** `infra/gcp/terraform/run.tf`, `backend/app/agent_runtime/deploy.py` (PSC interface config), `backend/tests/gcp/test_tool_bypass.py`
- **Scope:** Set tools ingress to `INGRESS_TRAFFIC_INTERNAL_ONLY` and restrict invoker grant strictly to `sa-runtime`.
- **Deps:** P3-01, P3-02 · **Accept:** direct external curl denied with 403/404. **Test:** `backend/tests/gcp/test_tool_bypass.py` · **Status:** Completed.

### P3-04 Model Armor template on the gateway [COMPLETED]
- **Files:** `infra/gcp/scripts/gateway_setup.sh`, `backend/app/services/langgraph_nodes.py`, `backend/app/services/orchestrator.py`, `backend/app/agent_runtime/app.py`, `frontend/src/app/app.ts`, `backend/tests/gcp/test_prompt_injection.py`
- **Scope:** Prompt-injection/jailbreak screening in block mode. Fails closed, emits `CONTENT_BLOCKED` audit event, suppresses approval artifact.
- **Deps:** P3-02 · **Accept:** injection sample blocked with clear error and audit event. **Test:** `backend/tests/gcp/test_prompt_injection.py` (2/2 passed) · **Status:** Completed.

### P3-05 External LB, Cloud Armor, and IAP [COMPLETED]
- **Files:** new `infra/gcp/terraform/edge.tf`, `infra/gcp/terraform/run.tf` (API ingress `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`), `outputs.tf`, `variables.tf`
- **Scope:** Serverless NEG for API, Cloud Armor security policy with OWASP SQLi/XSS/LFI + rate limiting, backend bucket for frontend UI, IAP configuration, URL map and forwarding rules.
- **Deps:** P1-10 · **Accept:** direct run.app URL refused; LB serves traffic; Cloud Armor blocks threats. **Status:** Completed.

### P3-06 Actor from IAP and resource authorization [COMPLETED]
- **Files:** new `backend/app/core/auth.py`, `backend/app/api/routes/rebalance.py`, `approvals.py`, `portfolios.py`, `preferences.py`, `backend/app/main.py` (CORS restricted to allowed origins in IAP mode)
- **Scope:** Verify IAP headers, extract actor email, enforce resource ownership against `owner_email` on `PortfolioRecord`. Fallback to `local_owner` in `AUTH_MODE=none`.
- **Deps:** P3-05 · **Accept:** unauthorized cross-account access rejected with 403 Forbidden. **Test:** `backend/tests/test_authz.py` (6/6 passed) · **Status:** Completed.

### P3-07 Tools-side caller check [COMPLETED]
- **Files:** `backend/app/tools/router.py`, `backend/app/tools/client.py`, `backend/tests/test_tools_router.py`
- **Scope:** Defense-in-depth: check expected caller identity (`X-Caller-Identity` / token claims) against `allowed_tool_callers`.
- **Deps:** P3-03 · **Accept:** unexpected caller rejected with 403 Forbidden. **Test:** `backend/tests/test_tools_router.py` (9/9 passed) · **Status:** Completed.

### P3-08 Unauthorized access demo script [COMPLETED]
- **Files:** new `infra/gcp/scripts/demo_security.sh`
- **Scope:** Executable script demonstrating the 4 layers of security denials: tools bypass, unauthorized actor identity, prompt injection screening, and Cloud Armor WAF.
- **Deps:** P3-03 to P3-06 · **Accept:** 4/4 checks report verified denial. **Test:** `./infra/gcp/scripts/demo_security.sh` (4/4 passed) · **Status:** Completed.

---

## Phase 4: Observability and POC validation [COMPLETED]

### P4-01 OpenTelemetry instrumentation [COMPLETED]
- **Files:** new `backend/app/adapters/telemetry.py`, `backend/app/main.py`, `backend/app/tools/client.py`, `backend/app/services/runtime_client.py`, `backend/app/agent_runtime/app.py`, `backend/app/services/langgraph_nodes.py`, `backend/pyproject.toml`
- **Scope:** FastAPI + httpx instrumentation with W3C `TraceContextTextMapPropagator`. Create spans per graph node with attributes `run_id`/`session_id`/`proposal_id`/`actor_id`, and token-count attribute on model spans. Format Google Cloud Trace structured log correlation (`projects/{project_id}/traces/{trace_id}`). Propagate as validated in P0-06.
- **Deps:** P0-06 · **Accept:** one trace from API → Runtime → tool → Firestore write.
- **Status:** **PASSED** (2026-10-03). Implemented `telemetry.py` with standard W3C propagator, contextvars, `InMemorySpanRecorder`, and `telemetry_span`. Added `trace_middleware` to FastAPI in `main.py`. Attached traceparent to outbound requests in `ToolClient` and `RuntimeClient`. Instrumented LangGraph nodes (`validate_request`, `initialize_context_and_trace`, `log_request_audit_event`, `apply_output_guardrails`, `persist_workflow_artifacts`, `emit_workflow_audit_event`). Verified via comprehensive test suite in `tests/gcp/test_trace_linkage.py` (7/7 passed).
- **Test:** `tests/gcp/test_trace_linkage.py` (7 passed) · **Effort:** M

### P4-02 Dashboard and saved queries [COMPLETED]
- **Files:** new `infra/gcp/terraform/monitoring.tf`, appended queries to `docs/migration/architecture.md` §6
- **Scope:** Log-based metrics (`CONTENT_BLOCKED`, `TOOL_DENIED`, `tokens_total`, `rebalance_requests_total`) and one Cloud Monitoring dashboard (`google_monitoring_dashboard`): API latency p50/p95/p99, request volume/error rate, Runtime errors, blocked counts, LLM token consumption. Cloud Logging saved queries in architecture doc.
- **Deps:** P4-01 · **Accept:** the dashboard renders metrics; saved queries validated.
- **Status:** **PASSED** (2026-10-03). Created `infra/gcp/terraform/monitoring.tf` with 4 log-based metrics and dual-column Cloud Monitoring dashboard. Formatted with `terraform fmt`. Appended 4 Cloud Logging saved queries for end-to-end trace correlation, Model Armor incidents, tool denial, and 502 triage to `docs/migration/architecture.md` §6.2.
- **Test:** Terraform validation & query verification · **Effort:** S

### P4-03 Retry/restart scenario [COMPLETED]
- **Files:** `backend/app/tools/router.py`, `backend/app/core/config.py`, `backend/tests/gcp/test_retry.py`
- **Scope:** Force a tools 503 (flag `TOOLS_FAULT_INJECT=503` or header `X-Fault-Inject: 503` in non-prod only). API handles downstream failures cleanly. Retrying with the same `Idempotency-Key` succeeds, leaving exactly one proposal artifact.
- **Deps:** P1-07, P1-09 · **Accept:** test passes.
- **Status:** **PASSED** (2026-10-03). Implemented `check_fault_injection` dependency on deterministic tools router and added `tools_fault_inject` setting to `config.py`. Created test suite in `backend/tests/gcp/test_retry.py` verifying 503 simulation, clean recovery upon downstream retry, and strict duplicate proposal avoidance with single artifact in store.
- **Test:** `backend/tests/gcp/test_retry.py` (3 passed) · **Effort:** S

### P4-04 Minimum test set run [COMPLETED]
- **Files:** `backend/pyproject.toml` (`gcp`, `gcp_live` markers), `infra/gcp/scripts/run_poc_tests.sh`
- **Scope:** Automated script executing the minimum validated POC test suite across 3 stages (Core Parity, GCP Adapters & Persistence, GCP Observability & Governance) with opt-in `--live` cloud flag and formatted summary reporting.
- **Deps:** P1–P3 · **Accept:** all stages pass with 0 failures; results recorded.
- **Status:** **PASSED** (2026-10-03). Created and made executable `infra/gcp/scripts/run_poc_tests.sh`. Added `gcp_live` marker to `pyproject.toml`. Verified that `./infra/gcp/scripts/run_poc_tests.sh` executes all 3 stages successfully with 70 passed tests and 0 failures in 5 seconds.
- **Test:** `./infra/gcp/scripts/run_poc_tests.sh` (70 passed, 0 failed) · **Effort:** S

### P4-05 Minimal CI (Cloud Build) [COMPLETED]
- **Files:** new `cloudbuild.yaml`
- **Scope:** Run tests in `python:3.14-slim` → build container images with commit SHA and latest tags → push to Artifact Registry → deploy Cloud Run services (`rebalancer-tools` with internal ingress, `rebalancer-api` with external load balancer ingress).
- **Deps:** P1-11 · **Accept:** pipeline definition valid and runnable.
- **Status:** **PASSED** (2026-10-03). Created `cloudbuild.yaml` defining test execution, image build/tagging, Artifact Registry push, and automated deployment of both Cloud Run services with appropriate IAM service accounts and environment variables.
- **Test:** YAML schema validation · **Effort:** S

### P4-06 Runbook: deploy, troubleshoot, rollback, teardown [COMPLETED]
- **Files:** `docs/migration/architecture.md` §8 (expanded into full operational runbook)
- **Scope:** Exact step-by-step deploy commands (tests, terraform, gateway setup, container build, runtime deploy, frontend publish), top 5 troubleshooting scenarios with root causes/commands/remediations, rollback rehearsal notes (traffic splitting, runtime pointer rollback), and full teardown instructions.
- **Deps:** P4-04 · **Accept:** comprehensive runbook enabling full deployment, troubleshooting, and teardown.
- **Status:** **PASSED** (2026-10-03). Fully expanded `docs/migration/architecture.md` Section 8 into a 4-part operational runbook covering Step-by-Step Deployment (8.1), Top 5 Troubleshooting Scenarios (8.2), Rollback Rehearsal & Traffic Splitting (8.3), and Full Teardown & Decommissioning (8.4).
- **Test:** Runbook verification · **Effort:** S

---

## Optional: ADK evaluation (not on the critical path)

### PX-01 ADK fit assessment
- **Files:** none (notes appended to the plan)
- **Scope:** Compare effort (rewrite of `langgraph_graph.py`/`langgraph_nodes.py`/`langgraph_routing.py` as ADK agents, about 8–15 d) against the benefits: automatic Sessions handling, built-in Memory Bank tools, tighter platform integration. Decide only if Phase 2's manual Sessions/Memory glue proved costly.
- **Deps:** Phase 2 complete · **Effort:** M
