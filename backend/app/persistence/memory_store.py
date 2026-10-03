from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.contracts.analysis import (
    ApprovalAction,
    ApprovalActionRequest,
    ApprovalArtifact,
    ApprovalTransitionResult,
    AuditEvent,
)
from app.contracts.common import CorrelationMetadata, new_id
from app.contracts.domain import (
    AccountProfile,
    AllocationTarget,
    ClientProfile,
    PortfolioHolding,
    PortfolioRecord,
    PortfolioSnapshot,
    RiskProfile,
)


@runtime_checkable
class WorkflowStore(Protocol):
    def save_portfolio(self, portfolio: PortfolioRecord) -> PortfolioRecord: ...

    def get_portfolio(self, account_id: str) -> PortfolioRecord | None: ...

    def list_portfolios(self) -> list[PortfolioRecord]: ...

    def save_approval(self, artifact: ApprovalArtifact) -> ApprovalArtifact: ...

    def get_approval(self, approval_id: str) -> ApprovalArtifact | None: ...

    def list_approvals(self) -> list[ApprovalArtifact]: ...

    def list_audit_events(self) -> list[AuditEvent]: ...

    def update_approval(
        self, approval_id: str, action: ApprovalActionRequest
    ) -> ApprovalTransitionResult: ...

    def add_audit_event(
        self,
        event_type: str,
        correlation: CorrelationMetadata,
        outcome: str,
        actor_id: str | None = None,
        details: dict[str, str] | None = None,
    ) -> AuditEvent: ...


class InMemoryWorkflowStore:
    def __init__(self) -> None:
        self.approvals: dict[str, ApprovalArtifact] = {}
        self.audit_events: list[AuditEvent] = []
        self.portfolios: dict[str, PortfolioRecord] = {}
        for portfolio in default_portfolios():
            self.save_portfolio(portfolio)

    def save_portfolio(self, portfolio: PortfolioRecord) -> PortfolioRecord:
        self.portfolios[portfolio.account_profile.account_id] = portfolio
        return portfolio

    def get_portfolio(self, account_id: str) -> PortfolioRecord | None:
        return self.portfolios.get(account_id)

    def list_portfolios(self) -> list[PortfolioRecord]:
        return sorted(
            self.portfolios.values(),
            key=lambda portfolio: portfolio.client_profile.display_label,
        )

    def save_approval(self, artifact: ApprovalArtifact) -> ApprovalArtifact:
        self.approvals[artifact.approval_id] = artifact
        return artifact

    def get_approval(self, approval_id: str) -> ApprovalArtifact | None:
        return self.approvals.get(approval_id)

    def list_approvals(self) -> list[ApprovalArtifact]:
        return list(self.approvals.values())

    def list_audit_events(self) -> list[AuditEvent]:
        return list(self.audit_events)

    def update_approval(
        self, approval_id: str, action: ApprovalActionRequest
    ) -> ApprovalTransitionResult:
        approval = self.approvals[approval_id]
        previous = approval.approval_status
        if action.expected_recommendation_hash != approval.recommendation_hash:
            event = self.add_audit_event(
                "APPROVAL_ACTION_REJECTED",
                approval.correlation,
                "STALE_APPROVAL_ARTIFACT",
                action.actor_id,
            )
            return ApprovalTransitionResult(
                approval_id=approval_id,
                previous_status=previous,
                next_status=previous,
                accepted=False,
                audit_event_id=event.event_id,
                message="Recommendation hash did not match approval artifact.",
            )

        next_status = {
            ApprovalAction.APPROVE: "APPROVED",
            ApprovalAction.REJECT: "REJECTED",
            ApprovalAction.REQUEST_REVISION: "REVISION_REQUESTED",
        }[action.action]
        approval.approval_status = next_status
        if action.action == ApprovalAction.APPROVE:
            approval.approved_by = action.actor_id
            approval.approved_at = datetime.now(UTC)
        if action.action == ApprovalAction.REJECT:
            approval.rejection_note = action.note
        self.approvals[approval_id] = approval
        event = self.add_audit_event(
            "APPROVAL_ACTION",
            approval.correlation,
            next_status,
            action.actor_id,
            {"approval_id": approval_id, "action": action.action},
        )
        return ApprovalTransitionResult(
            approval_id=approval_id,
            previous_status=previous,
            next_status=next_status,
            accepted=True,
            audit_event_id=event.event_id,
            message="Approval state updated.",
        )

    def add_audit_event(
        self,
        event_type: str,
        correlation: CorrelationMetadata,
        outcome: str,
        actor_id: str | None = None,
        details: dict[str, str] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=new_id("evt"),
            event_type=event_type,
            correlation=correlation,
            actor_id=actor_id,
            outcome=outcome,
            details=details or {},
            created_at=datetime.now(UTC),
        )
        self.audit_events.append(event)
        return event


def default_portfolios() -> list[PortfolioRecord]:
    now = datetime.now(UTC)
    return [
        _portfolio_record(
            client_id="client_demo",
            account_id="acct_demo",
            display_label="Demo Investor",
            equity_value=Decimal("6750"),   # 67.5% — clearly above 60% target, triggers REBALANCE_NEEDED
            fixed_income_value=Decimal("2250"),  # 22.5% — below 30% target
            cash_value=Decimal("1000"),     # 10% — at target
            as_of=now,
        ),
        _portfolio_record(
            client_id="client_income",
            account_id="acct_income",
            display_label="Income Investor",
            equity_value=Decimal("3800"),   # 38% — below 60% target
            fixed_income_value=Decimal("5200"),  # 52% — above 30% target
            cash_value=Decimal("1000"),     # 10% — at target
            as_of=now,
        ),
    ]


def _portfolio_record(
    client_id: str,
    account_id: str,
    display_label: str,
    equity_value: Decimal,
    fixed_income_value: Decimal,
    cash_value: Decimal,
    as_of: datetime,
) -> PortfolioRecord:
    return PortfolioRecord(
        client_profile=ClientProfile(
            client_id=client_id,
            display_label=display_label,
            risk_profile_id="risk_balanced",
            synthetic=True,
        ),
        account_profile=AccountProfile(account_id=account_id, client_id=client_id),
        portfolio_snapshot=PortfolioSnapshot(
            snapshot_id=f"snap_{account_id}",
            account_id=account_id,
            as_of=as_of,
            holdings=[
                PortfolioHolding(
                    instrument_id=f"{account_id}_equity",
                    symbol="EQUITY",
                    asset_class="equity",
                    quantity=Decimal("1"),
                    market_price=equity_value,
                    market_value=equity_value,
                    as_of=as_of,
                ),
                PortfolioHolding(
                    instrument_id=f"{account_id}_fixed_income",
                    symbol="BONDS",
                    asset_class="fixed_income",
                    quantity=Decimal("1"),
                    market_price=fixed_income_value,
                    market_value=fixed_income_value,
                    as_of=as_of,
                ),
            ],
            cash=cash_value,
            total_value=equity_value + fixed_income_value + cash_value,
        ),
        allocation_target=AllocationTarget(
            target_id=f"target_{account_id}",
            account_id=account_id,
            asset_class_targets={
                "equity": Decimal("60"),
                "fixed_income": Decimal("30"),
                "cash": Decimal("10"),
            },
            tolerance_bands={
                "equity": Decimal("5"),
                "fixed_income": Decimal("5"),
                "cash": Decimal("5"),
            },
        ),
        risk_profile=RiskProfile(
            risk_profile_id="risk_balanced",
            risk_level="balanced",
            max_single_position_pct=Decimal("85"),
            max_sector_pct=Decimal("60"),
            allowed_asset_classes=["equity", "fixed_income", "cash"],
        ),
        updated_at=as_of,
        source="seed",
    )

