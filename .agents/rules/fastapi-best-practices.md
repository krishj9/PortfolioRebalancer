---
description: FastAPI backend best practices for this project
globs: **/*.py
alwaysApply: false
---

# FastAPI Best Practices

- Organize backend code by responsibility: routers, services, adapters, models/schemas, persistence, and tests.
- Use Pydantic v2 models for all API, LangGraph, persistence, seed, and eval contracts. Keep generated JSON Schema artifacts synchronized.
- Validate requests at the API boundary before invoking LangGraph or AWS adapters.
- Return `StructuredError` envelopes for machine-consumable failures; do not leak raw exceptions to clients.
- Use dependency injection for settings, auth context, repositories, AWS clients, and tracing adapters.
- Prefer async route handlers when calling async services. Do not block the event loop with long CPU-bound work.
- Keep deterministic portfolio math and policy checks outside LLM/prompt code, with unit tests.
- Keep Bedrock, AgentCore, DynamoDB, and CloudWatch calls behind small adapter interfaces so tests can run locally.
- Preserve `request_id`, `session_id`, `trace_id`, `schema_version`, `policy_version`, and `environment` across every boundary.
- Add tests for contract validation, structured errors, graph-path behavior, approval transitions, and persistence edge cases.

```python
@router.post("/rebalance", response_model=OrchestrationResponse)
async def rebalance(
    request: PortfolioRebalanceRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> OrchestrationResponse:
    return await orchestrator.run(request)
```
