from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from app.contracts.analysis import (
    ApprovalArtifact,
    AuditEvent,
    DriftItem,
    ExecutionProposalResponse,
    RiskPolicyResponse,
)
from app.contracts.common import CorrelationMetadata
from app.contracts.domain import (
    AllocationTarget,
    PortfolioRecord,
    PortfolioSnapshot,
    RiskProfile,
)


class GetPortfolioRequest(BaseModel):
    account_id: str = Field(..., description="Account ID to look up")


class ComputeDriftRequest(BaseModel):
    portfolio_snapshot: PortfolioSnapshot
    allocation_target: AllocationTarget


class ComputeDriftResponse(BaseModel):
    current_allocation: dict[str, Decimal]
    drift: list[DriftItem]


class EvaluatePolicyRequest(BaseModel):
    portfolio_snapshot: PortfolioSnapshot
    drift: list[DriftItem]
    risk_profile: Optional[RiskProfile] = None


class GenerateProposalRequest(BaseModel):
    portfolio_snapshot: PortfolioSnapshot
    risk_policy: RiskPolicyResponse


class PersistProposalRequest(BaseModel):
    approval_artifact: ApprovalArtifact


class AuditRequest(BaseModel):
    event_type: str
    correlation: CorrelationMetadata
    outcome: str
    actor_id: Optional[str] = None
    details: Optional[dict[str, str]] = None
