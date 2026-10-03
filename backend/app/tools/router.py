import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.contracts.analysis import (
    ApprovalArtifact,
    AuditEvent,
    ExecutionProposalResponse,
    RiskPolicyResponse,
)
from app.contracts.domain import PortfolioRecord
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import WorkflowStore
from app.services.policy import evaluate_policy
from app.services.portfolio import calculate_asset_allocation, calculate_drift
from app.services.proposal import generate_execution_proposal
from app.tools.models import (
    AuditRequest,
    ComputeDriftRequest,
    ComputeDriftResponse,
    EvaluatePolicyRequest,
    GenerateProposalRequest,
    GetPortfolioRequest,
    PersistProposalRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


@router.post("/get_portfolio", response_model=PortfolioRecord)
def get_portfolio(
    request: GetPortfolioRequest,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
) -> PortfolioRecord:
    """Retrieve portfolio record for an account ID."""
    portfolio = store.get_portfolio(request.account_id)
    if portfolio is None:
        raise HTTPException(
            status_code=404,
            detail=f"Portfolio record not found: {request.account_id}",
        )
    return portfolio


@router.post("/compute_drift", response_model=ComputeDriftResponse)
def compute_drift(request: ComputeDriftRequest) -> ComputeDriftResponse:
    """Deterministically compute asset allocation and drift against target bands."""
    current = calculate_asset_allocation(request.portfolio_snapshot)
    drift = calculate_drift(request.portfolio_snapshot, request.allocation_target)
    return ComputeDriftResponse(current_allocation=current, drift=drift)


@router.post("/evaluate_policy", response_model=RiskPolicyResponse)
def evaluate_risk_policy(request: EvaluatePolicyRequest) -> RiskPolicyResponse:
    """Deterministically evaluate risk policy rules on snapshot and drift."""
    return evaluate_policy(
        snapshot=request.portfolio_snapshot,
        drift=request.drift,
        risk_profile=request.risk_profile,
    )


@router.post("/generate_proposal", response_model=ExecutionProposalResponse)
def generate_proposal(request: GenerateProposalRequest) -> ExecutionProposalResponse:
    """Deterministically generate rebalancing trade proposals based on policy."""
    return generate_execution_proposal(
        snapshot=request.portfolio_snapshot,
        risk_policy=request.risk_policy,
    )


@router.post("/persist_proposal", response_model=ApprovalArtifact)
def persist_proposal(
    request: PersistProposalRequest,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
) -> ApprovalArtifact:
    """Persist an approval artifact to the operational store."""
    return store.save_approval(request.approval_artifact)


@router.post("/audit", response_model=AuditEvent)
def record_audit_event(
    request: AuditRequest,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
) -> AuditEvent:
    """Record an immutable audit event."""
    return store.add_audit_event(
        event_type=request.event_type,
        correlation=request.correlation,
        outcome=request.outcome,
        actor_id=request.actor_id,
        details=request.details,
    )
