# Spike P0-05: Agent Gateway Tool Registration & IAM Principal Evaluation

## 1. Executive Summary

This spike evaluates how Vertex AI Agent Runtime invokes external deterministic tools hosted on Cloud Run, specifically assessing:
1. **Plain HTTPS vs. Model Context Protocol (MCP)**: Whether Agent Gateway can govern plain REST/HTTP endpoints or if an MCP facade is required.
2. **IAM Principal Identity for `roles/run.invoker`**: How Cloud Run services authenticate requests originating from Agent Runtime.
3. **Deployment Strategy**: Supported Terraform resources vs. scripted/gcloud operations for Agent Gateway in Phase 3.

---

## 2. Findings

### Finding 1: Plain HTTPS vs. MCP Facade
- **Agent Gateway Traffic Parsing**:
  - Google Cloud Agent Gateway (`networkservices.googleapis.com`) acts as a reverse proxy for agent egress and ingress traffic.
  - **HTTP/REST Traffic**: Agent Gateway acts as a **passthrough**. It enforces basic network boundaries and routing, but does *not* parse request or response payloads for attribute extraction.
  - **MCP Traffic**: Agent Gateway natively parses Model Context Protocol messages (JSON-RPC tool calls and returns). It extracts tool names, arguments, and return values for attribute-based access control and policy enforcement.
- **Model Armor Integration**:
  - Model Armor templates can be attached to Agent Gateways to screen tool requests and responses against content safety and prompt-injection policies.
  - Because payload inspection requires attribute extraction, **fine-grained tool governance and parameter inspection require MCP**.
- **Decision for PortfolioRebalancer**:
  - **Phase 1**: Use **plain HTTPS/JSON** with standard Google ID token (OIDC) authentication. The deterministic tools (`portfolio.py`, `policy.py`, `proposal.py`) do not use LLMs, do not generate unstructured text, and operate strictly on validated JSON schemas. Adding an MCP layer in Phase 1 adds unnecessary latency and serialization overhead.
  - **Phase 3**: When implementing the optional Model Armor gateway policy (for screening untrusted user input in free-text preference fields), implement an MCP adapter/facade for the tools service so that Agent Gateway can inspect tool call metadata.

---

### Finding 2: Agent Identity & Cloud Run Authentication (`run.invoker`)

When Agent Runtime calls Cloud Run (`rebalancer-tools`), Cloud Run requires `roles/run.invoker` on the caller.

#### Identity Models:
1. **Standard Service Account (ADC & ID Tokens)**:
   - Vertex AI Agent Runtime containers run under the managed service agent:
     ```
     service-<PROJECT_NUM>@gcp-sa-aiplatform-re.iam.gserviceaccount.com
     ```
     (For project `mybrightday-dev` [754915077075]: `service-754915077075@gcp-sa-aiplatform-re.iam.gserviceaccount.com`).
   - If a custom service account is specified at deployment time via `--service-account` (e.g., `sa-rebalancer-runtime@mybrightday-dev.iam.gserviceaccount.com`), the container inherits that identity.
   - The tool client in Agent Runtime generates an OIDC ID token using ambient credentials:
     ```python
     import google.auth.transport.requests
     import google.oauth2.id_token

     auth_req = google.auth.transport.requests.Request()
     id_token = google.oauth2.id_token.fetch_id_token(auth_req, audience=TOOLS_SERVICE_URL)
     headers = {"Authorization": f"Bearer {id_token}"}
     ```
   - Granting `roles/run.invoker` on the Cloud Run service to this Service Account allows direct, authenticated invocation.

2. **Universal Agent Principal (UAP) SPIFFE Identity**:
   - Agent Platform supports cryptographic SPIFFE IDs for reasoning engines:
     - **Specific Engine**:
       `principal://agents.global.org-<ORG_ID>.system.id.goog/resources/aiplatform/projects/<PROJECT_NUM>/locations/<REGION>/reasoningEngines/<ENGINE_ID>`
     - **All Engines in Project**:
       `principalSet://agents.global.org-<ORG_ID>.system.id.goog/attribute.platformContainer/aiplatform/projects/<PROJECT_NUM>`
   - This principal format is used primarily for Agent Gateway IAM bindings and platform policy enforcement. For direct Cloud Run IAM bindings without an intermediate gateway, standard Google Service Account IAM is the primary mechanism.

---

## 3. Terraform vs. Scripted Operations

| Component | Management Mechanism | Phase | Notes |
|---|---|---|---|
| Cloud Run `rebalancer-tools` | Terraform `google_cloud_run_v2_service` | Phase 1 | Standard resource; IAM binding `google_cloud_run_v2_service_iam_member` |
| Runtime Service Account | Terraform `google_service_account` | Phase 1 | Custom SA or managed service agent |
| Cloud Run Invoker IAM | Terraform `google_cloud_run_v2_service_iam_member` | Phase 1 | Grants `roles/run.invoker` to the runtime SA |
| Agent Gateway | Terraform `google_network_services_agent_gateway` / `gcloud network-services agent-gateways` | Phase 3 | Enterprise governance edge |
| Agent Registry / MCP tools | `gcloud network-services agent-gateways` / API | Phase 3 | Register tool endpoints with gateway |
| Model Armor Template | Terraform `google_model_armor_floor_setting` / API | Phase 3 | Attach safety template to gateway |

---

## 4. Phase 1 Implementation Contract

1. **Deterministic Tools Service (`rebalancer-tools`)**:
   - Exposes REST endpoints:
     - `POST /tools/get_portfolio`
     - `POST /tools/compute_drift`
     - `POST /tools/evaluate_policy`
     - `POST /tools/generate_proposal`
     - `POST /tools/persist_proposal`
     - `POST /tools/audit`
   - Authentication: Cloud Run IAM enforces valid Google ID tokens.
2. **Tools Client (`backend/app/tools/client.py`)**:
   - Uses `httpx.AsyncClient`.
   - When `TOOL_MODE=remote`:
     - Fetches Google ID token with audience matching `TOOLS_SERVICE_URL`.
     - Adds `Authorization: Bearer <ID_TOKEN>`.
     - Implements 1 retry on 5xx and explicit timeout.
   - When `TOOL_MODE=inprocess` (default for local unit testing):
     - Executes local Python functions directly without network hop.
