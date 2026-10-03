from datetime import UTC, datetime
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.adapters.analytics import BaseAnalyticsAdapter, get_analytics_adapter
from app.contracts.analysis import (
    ApprovalArtifact,
    AuditEvent,
    ExecutionProposalResponse,
    RiskPolicyResponse,
)
from app.contracts.domain import PortfolioRecord
from app.core.config import Settings, get_settings
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


def verify_tool_caller(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_caller_identity: Annotated[Optional[str], Header(alias="X-Caller-Identity")] = None,
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
) -> str | None:
    """
    Defense-in-depth verification of caller identity on the deterministic tools service (Task P3-07).

    Cloud Run already enforces IAM at the infrastructure layer (INGRESS_TRAFFIC_INTERNAL_ONLY + roles/run.invoker).
    This tools-side check validates that the calling service account or verified header belongs
    to the allowed set of service accounts (e.g. sa-runtime@project.iam.gserviceaccount.com).
    """
    if settings.tools_caller_check.lower() != "enforce":
        return x_caller_identity or "local_caller"

    caller = x_caller_identity or request.headers.get("x-caller-identity")

    if not caller and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            import base64
            import json
            parts = token.split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1] + "=="
                payload_json = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
                claims = json.loads(payload_json)
                caller = claims.get("email") or claims.get("sub")
        except Exception:
            pass

    if not caller or caller not in settings.allowed_tool_callers:
        logger.warning("Tools caller check rejected unauthorized caller: %s", caller)
        raise HTTPException(
            status_code=403,
            detail=f"Caller '{caller}' is not authorized to invoke deterministic tools service",
        )

    return caller


router = APIRouter(prefix="/tools", tags=["tools"], dependencies=[Depends(verify_tool_caller)])


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
    analytics: Annotated[BaseAnalyticsAdapter, Depends(get_analytics_adapter)],
) -> ApprovalArtifact:
    """Persist an approval artifact to the operational store and emit analytics event."""
    saved = store.save_approval(request.approval_artifact)

    try:
        artifact = request.approval_artifact
        max_drift = 0.0
        if (
            artifact.recommendation
            and artifact.recommendation.risk_policy
            and artifact.recommendation.risk_policy.drift
        ):
            max_drift = max(
                abs(float(item.drift_pct))
                for item in artifact.recommendation.risk_policy.drift
            )

        trade_count = 0
        if (
            artifact.recommendation
            and artifact.recommendation.proposal
            and artifact.recommendation.proposal.trades
        ):
            trade_count = len(artifact.recommendation.proposal.trades)

        workflow_state = "PENDING_APPROVAL"
        if artifact.recommendation and artifact.recommendation.workflow_state:
            workflow_state = str(artifact.recommendation.workflow_state)

        account_id = "unknown"
        if artifact.account_profile and artifact.account_profile.account_id:
            account_id = artifact.account_profile.account_id

        run_id = (
            getattr(artifact.correlation, "run_id", None)
            or getattr(artifact.correlation, "trace_id", None)
        ) if artifact.correlation else None

        analytics.emit_proposal_event(
            event_ts=datetime.now(UTC),
            proposal_id=artifact.approval_id,
            account_id=account_id,
            event_type="PROPOSAL_CREATED",
            workflow_state=workflow_state,
            max_abs_drift_pct=max_drift,
            trade_count=trade_count,
            run_id=run_id,
        )
    except Exception as exc:
        logger.warning("Failed to emit proposal_created analytics event: %s", exc)

    return saved


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
