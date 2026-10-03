from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header

from app.contracts import OrchestrationResponse, PortfolioRebalanceRequest
from app.core.config import Settings, get_settings
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import WorkflowStore
from app.services.orchestrator import Orchestrator
from app.services.runtime_client import RuntimeClient

router = APIRouter(prefix="/rebalance", tags=["rebalance"])


def get_orchestrator(store: Annotated[WorkflowStore, Depends(get_workflow_store)]) -> Orchestrator:
    return Orchestrator(store)


def get_runtime_client(settings: Annotated[Settings, Depends(get_settings)]) -> RuntimeClient:
    return RuntimeClient(settings=settings)


@router.post("", response_model=OrchestrationResponse)
async def create_rebalance_request(
    request: PortfolioRebalanceRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    orchestrator: Annotated[Orchestrator, Depends(get_orchestrator)],
    runtime_client: Annotated[RuntimeClient, Depends(get_runtime_client)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
    session_id: Annotated[Optional[str], Header(alias="X-Session-ID")] = None,
) -> OrchestrationResponse:
    if idempotency_key and not request.correlation.idempotency_key:
        request.correlation.idempotency_key = idempotency_key

    if session_id and not request.correlation.session_id:
        request.correlation.session_id = session_id

    request.version = request.version.model_copy(
        update={
            "schema_version": settings.schema_version,
            "policy_version": settings.policy_version,
            "app_version": settings.app_version,
            "environment": settings.environment,
        }
    )

    if settings.orchestration_mode == "agent_runtime":
        return await runtime_client.run(request, session_id=request.correlation.session_id)

    return await orchestrator.run(request)

