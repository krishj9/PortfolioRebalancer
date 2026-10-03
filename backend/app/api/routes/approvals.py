from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.analytics import BaseAnalyticsAdapter, get_analytics_adapter
from app.contracts.analysis import (
    ApprovalAction,
    ApprovalActionRequest,
    ApprovalArtifact,
    ApprovalTransitionResult,
)
from app.contracts.common import new_id
from app.contracts.domain import PortfolioHolding, PortfolioRecord, PortfolioSnapshot
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import WorkflowStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.post("/{approval_id}/actions", response_model=ApprovalTransitionResult)
async def apply_approval_action(
    approval_id: str,
    action: ApprovalActionRequest,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
    analytics: Annotated[BaseAnalyticsAdapter, Depends(get_analytics_adapter)],
) -> ApprovalTransitionResult:
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval artifact not found")

    # Only block APPROVE actions on blocked recommendations, allow REJECT
    if approval.recommendation.workflow_state == "BLOCKED" and action.action == ApprovalAction.APPROVE:
        raise HTTPException(status_code=409, detail="Blocked recommendations cannot be approved")

    if action.action in {"REJECT", "REQUEST_REVISION"} and not action.note:
        raise HTTPException(status_code=422, detail="A note is required for this approval action")

    transition = store.update_approval(approval_id, action)
    if transition.accepted:
        try:
            current_app = store.get_approval(approval_id)
            if current_app:
                max_drift = 0.0
                if (
                    current_app.recommendation
                    and current_app.recommendation.risk_policy
                    and current_app.recommendation.risk_policy.drift
                ):
                    max_drift = max(
                        abs(float(item.drift_pct))
                        for item in current_app.recommendation.risk_policy.drift
                    )
                trade_count = (
                    len(current_app.recommendation.proposal.trades)
                    if current_app.recommendation and current_app.recommendation.proposal
                    else 0
                )
                account_id = (
                    current_app.account_profile.account_id
                    if current_app.account_profile
                    else "unknown"
                )
                run_id = (
                    getattr(current_app.correlation, "run_id", None)
                    or getattr(current_app.correlation, "trace_id", None)
                ) if current_app.correlation else None

                analytics.emit_proposal_event(
                    event_ts=datetime.now(UTC),
                    proposal_id=approval_id,
                    account_id=account_id,
                    event_type=f"PROPOSAL_{action.action.value}",
                    workflow_state=transition.next_status,
                    max_abs_drift_pct=max_drift,
                    trade_count=trade_count,
                    run_id=run_id,
                )
        except Exception as exc:
            logger.warning("Failed to emit proposal approval action analytics event: %s", exc)

    if transition.accepted and action.action == ApprovalAction.APPROVE:
        updated = store.get_approval(approval_id)
        if updated is not None:
            portfolio = _approved_portfolio(updated)
            store.save_portfolio(portfolio)
            store.add_audit_event(
                "PORTFOLIO_UPDATED_FROM_APPROVAL",
                updated.correlation,
                "PERSISTED",
                action.actor_id,
                {
                    "approval_id": approval_id,
                    "account_id": portfolio.account_profile.account_id,
                },
            )
    return transition


def _approved_portfolio(approval: ApprovalArtifact) -> PortfolioRecord:
    as_of = datetime.now(UTC)
    total_value = approval.original_portfolio_snapshot.total_value
    proposed = approval.recommendation.proposed_allocation
    equity_value = _allocation_value(total_value, proposed.get("equity", Decimal("0")))
    fixed_income_value = _allocation_value(
        total_value, proposed.get("fixed_income", Decimal("0"))
    )
    cash_value = total_value - equity_value - fixed_income_value
    snapshot = PortfolioSnapshot(
        snapshot_id=new_id("snap"),
        account_id=approval.account_profile.account_id,
        as_of=as_of,
        holdings=[
            PortfolioHolding(
                instrument_id="approved_equity",
                symbol="EQUITY",
                asset_class="equity",
                quantity=Decimal("1"),
                market_price=equity_value,
                market_value=equity_value,
                as_of=as_of,
            ),
            PortfolioHolding(
                instrument_id="approved_fixed_income",
                symbol="BONDS",
                asset_class="fixed_income",
                quantity=Decimal("1"),
                market_price=fixed_income_value,
                market_value=fixed_income_value,
                as_of=as_of,
            ),
        ],
        cash=cash_value,
        total_value=total_value,
    )
    return PortfolioRecord(
        client_profile=approval.client_profile,
        account_profile=approval.account_profile,
        portfolio_snapshot=snapshot,
        allocation_target=approval.allocation_target,
        risk_profile=approval.risk_profile,
        updated_at=as_of,
        source="approval",
        source_approval_id=approval.approval_id,
    )


def _allocation_value(total_value: Decimal, percent: Decimal) -> Decimal:
    return (total_value * percent / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
