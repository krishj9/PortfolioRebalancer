# Documentation Hub: Portfolio Rebalancer Platform

This `docs/` folder is the architecture and implementation reference for the AI-powered portfolio rebalancing platform.

The system monitors allocation drift, runs a LangGraph multi-agent workflow, and generates rebalancing recommendations that require explicit human approval before trades are applied.

## Project at a glance

- **Frontend:** Angular 19 SPA (signals-based UI)
- **Backend:** FastAPI + LangGraph (Python 3.12+)
- **AI layer:** Amazon Bedrock Claude models with feature-flagged LLM agent behavior
- **Remote agent protocols:** A2A (research), MCP (sentiment)
- **Data:** DynamoDB in AWS, DynamoDB Local in local development
- **Infra:** AWS Lambda + API Gateway + S3 + CloudWatch, managed with Terraform

## High-level architecture

1. Browser UI calls API Gateway using `x-api-token` (in AWS deployments).
2. API Gateway routes traffic to:
   - Backend Lambda (`/api/*`)
   - Research Agent Lambda (`/a2a/research`)
   - Sentiment MCP Lambda (`/mcp`)
3. Backend orchestrates the portfolio workflow with LangGraph.
4. Agent stages may call Bedrock depending on `FEATURE_*_LLM_ENABLED` flags.
5. State, approvals, preferences, and audit events are persisted in DynamoDB.

For full diagrams and routing details, start with:
- `architecture.md`
- `architecture-diagrams.md`
- `01-architecture/system-architecture.md`

## Documentation map

### Core architecture and requirements

- `architecture.md` — comprehensive end-to-end architecture
- `architecture-diagrams.md` — visual system diagrams
- `00-product/` — product goals and scope boundaries
- `01-architecture/` — LangGraph design and orchestration details
- `02-requirements/` — functional and non-functional requirements
- `03-data-contracts/` — schema and contract catalog

### Delivery and implementation

- `04-implementation/implementation-guide.md` — phased implementation guide
- `04-implementation/workstreams.md` — execution tracks
- `04-implementation/tasks.md` — task-level breakdown
- `08-features/PREFERENCE_COLLECTION_IMPLEMENTATION.md` — preference collection feature notes

### Operations and deployment

- `05-operations/` — security, governance, observability, evals, and seeding strategy
- `06-deployment/aws-lambda-bedrock-agentcore.md` — deployment model notes
- `07-operations/USER_PREFERENCE_COLLECTION.md` — preference operations flow

### Steering and agent context

- `99-steering/cursor-steering.md` — generation guidance for Cursor workflows

## Canonical run/deploy guides (outside `docs/`)

Use these for day-to-day setup and deployment commands:

- `STARTUP_GUIDE.md` — local development, LLM feature behavior, validation flows, troubleshooting
- `AWS_DEPLOYMENT_GUIDE.md` — AWS provisioning, verification, rollback, and command reference
- `AGENT_CONTEXT.md` — practical repository map and coding context for agents

## Recommended reading order

1. `architecture.md`
2. `00-product/product.md`
3. `00-product/scope.md`
4. `01-architecture/system-architecture.md`
5. `01-architecture/langgraph-orchestration.md`
6. `03-data-contracts/README.md`
7. `03-data-contracts/data-contract-catalog.md`
8. `04-implementation/implementation-guide.md`
9. `05-operations/observability-evals.md`
10. `STARTUP_GUIDE.md`
11. `AWS_DEPLOYMENT_GUIDE.md`

## Notes

- Treat `STARTUP_GUIDE.md` and `AWS_DEPLOYMENT_GUIDE.md` as the operational source of truth for commands.
- Keep architecture details in `docs/` synchronized with production behavior (especially feature flags, routing, and policy/approval flows).
