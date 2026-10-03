from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from app.contracts.common import ContractModel


class AccountType(StrEnum):
    TAXABLE = "TAXABLE"
    IRA = "IRA"
    ROTH_IRA = "ROTH_IRA"
    BROKERAGE = "BROKERAGE"
    OTHER = "OTHER"


class ClientProfile(ContractModel):
    client_id: str
    household_id: str | None = None
    display_label: str
    risk_profile_id: str
    tax_profile_id: str | None = None
    synthetic: bool = True


class AccountProfile(ContractModel):
    account_id: str
    client_id: str
    account_type: AccountType = AccountType.BROKERAGE
    base_currency: str = Field(default="USD", min_length=3, max_length=3)
    custodian_label: str | None = None
    taxable: bool = True


class PortfolioHolding(ContractModel):
    instrument_id: str
    symbol: str
    asset_class: str = "unknown"
    sector: str | None = None
    quantity: Decimal = Field(ge=0)
    market_price: Decimal = Field(ge=0)
    market_value: Decimal = Field(ge=0)
    cost_basis: Decimal | None = Field(default=None, ge=0)
    as_of: datetime


class PortfolioSnapshot(ContractModel):
    snapshot_id: str
    account_id: str
    as_of: datetime
    holdings: list[PortfolioHolding] = Field(min_length=1)
    cash: Decimal = Field(default=Decimal("0"), ge=0)
    total_value: Decimal = Field(gt=0)
    allocation: dict[str, Decimal] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_total_value(self) -> "PortfolioSnapshot":
        holdings_value = sum((holding.market_value for holding in self.holdings), Decimal("0"))
        expected_total = holdings_value + self.cash
        tolerance = Decimal("0.01")
        if abs(expected_total - self.total_value) > tolerance:
            raise ValueError("total_value must equal holdings market value plus cash")
        return self


class AllocationTarget(ContractModel):
    target_id: str
    account_id: str
    asset_class_targets: dict[str, Decimal] = Field(default_factory=dict)
    security_targets: dict[str, Decimal] = Field(default_factory=dict)
    tolerance_bands: dict[str, Decimal] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_targets(self) -> "AllocationTarget":
        if not self.asset_class_targets and not self.security_targets:
            raise ValueError("at least one allocation target must be provided")
        total = sum(self.asset_class_targets.values(), Decimal("0"))
        if self.asset_class_targets and abs(total - Decimal("100")) > Decimal("0.01"):
            raise ValueError("asset_class_targets must sum to 100")
        return self


class RiskProfile(ContractModel):
    risk_profile_id: str
    risk_level: str
    max_single_position_pct: Decimal = Field(gt=0, le=100)
    max_sector_pct: Decimal = Field(gt=0, le=100)
    allowed_asset_classes: list[str] = Field(default_factory=list)


class PortfolioRecord(ContractModel):
    client_profile: ClientProfile
    account_profile: AccountProfile
    portfolio_snapshot: PortfolioSnapshot
    allocation_target: AllocationTarget
    risk_profile: RiskProfile | None = None
    updated_at: datetime
    source: str = "seed"
    source_approval_id: str | None = None
    owner_email: str = "local_owner"

