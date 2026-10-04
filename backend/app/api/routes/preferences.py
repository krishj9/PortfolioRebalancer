"""API routes for user preference management."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.contracts.common import ActorContext
from app.contracts.domain import AllocationTarget, ClientProfile, RiskProfile
from app.core.auth import get_current_actor, verify_account_ownership
from app.core.config import Settings, get_settings
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import WorkflowStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/preferences", tags=["preferences"])


# ============================================================================
# Request/Response Models
# ============================================================================


class PreferenceProfile:
    """Complete preference profile for a client."""

    def __init__(
        self,
        client_profile: ClientProfile,
        risk_profile: RiskProfile,
        allocation_target: AllocationTarget,
        constraints: dict | None = None,
    ):
        self.client_profile = client_profile
        self.risk_profile = risk_profile
        self.allocation_target = allocation_target
        self.constraints = constraints or {}


class PreferenceUpdateRequest:
    """Request to update client preferences."""

    def __init__(
        self,
        risk_profile: RiskProfile | None = None,
        allocation_target: AllocationTarget | None = None,
        constraints: dict | None = None,
    ):
        self.risk_profile = risk_profile
        self.allocation_target = allocation_target
        self.constraints = constraints


# ============================================================================
# Routes
# ============================================================================


@router.get("/{client_id}")
async def get_preferences(
    client_id: str,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
    actor: Annotated[ActorContext, Depends(get_current_actor)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """
    Get current preferences for a client.

    Args:
        client_id: Client identifier
        store: Workflow store
        actor: Authenticated actor context
        settings: Application settings

    Returns:
        Preference profile with client, risk, allocation, and constraints
    """
    # Find portfolio by client_id
    portfolios = store.list_portfolios()
    portfolio = next(
        (p for p in portfolios if p.client_profile.client_id == client_id),
        None
    )

    if not portfolio:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")

    verify_account_ownership(portfolio, actor, settings)

    return {
        "client_id": client_id,
        "client_profile": {
            "client_id": portfolio.client_profile.client_id,
            "display_label": portfolio.client_profile.display_label,
            "risk_profile_id": portfolio.client_profile.risk_profile_id,
            "tax_profile_id": portfolio.client_profile.tax_profile_id,
        },
        "risk_profile": {
            "risk_profile_id": portfolio.risk_profile.risk_profile_id,
            "risk_level": portfolio.risk_profile.risk_level,
            "max_single_position_pct": str(portfolio.risk_profile.max_single_position_pct),
            "max_sector_pct": str(portfolio.risk_profile.max_sector_pct),
            "allowed_asset_classes": portfolio.risk_profile.allowed_asset_classes,
        }
        if portfolio.risk_profile
        else None,
        "allocation_target": {
            "target_id": portfolio.allocation_target.target_id,
            "account_id": portfolio.allocation_target.account_id,
            "asset_class_targets": {
                k: str(v) for k, v in portfolio.allocation_target.asset_class_targets.items()
            },
            "tolerance_bands": {
                k: str(v) for k, v in portfolio.allocation_target.tolerance_bands.items()
            },
        },
        "constraints": {},  # TODO: Add constraints storage
        "updated_at": portfolio.updated_at.isoformat(),
    }


@router.put("/{client_id}")
async def update_preferences(
    client_id: str,
    request: dict,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
    actor: Annotated[ActorContext, Depends(get_current_actor)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """
    Update preferences for a client.

    Args:
        client_id: Client identifier
        request: Preference update request
        store: Workflow store
        actor: Authenticated actor context
        settings: Application settings

    Returns:
        Updated preference profile
    """
    from decimal import Decimal
    
    # Find portfolio by client_id
    portfolios = store.list_portfolios()
    portfolio = next(
        (p for p in portfolios if p.client_profile.client_id == client_id),
        None
    )

    if not portfolio:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")

    verify_account_ownership(portfolio, actor, settings)

    # Update fields if provided
    if "risk_profile" in request:
        risk_data = request["risk_profile"]
        # Convert string percentages to Decimal
        max_single = risk_data.get("max_single_position_pct", portfolio.risk_profile.max_single_position_pct)
        max_sector = risk_data.get("max_sector_pct", portfolio.risk_profile.max_sector_pct)
        
        portfolio.risk_profile = RiskProfile(
            risk_profile_id=risk_data.get("risk_profile_id", portfolio.risk_profile.risk_profile_id),
            risk_level=risk_data.get("risk_level", portfolio.risk_profile.risk_level),
            max_single_position_pct=Decimal(str(max_single)),
            max_sector_pct=Decimal(str(max_sector)),
            allowed_asset_classes=risk_data.get(
                "allowed_asset_classes", portfolio.risk_profile.allowed_asset_classes
            ),
        )

    if "allocation_target" in request:
        alloc_data = request["allocation_target"]
        
        # Convert string percentages to Decimal for targets and tolerances
        asset_class_targets = alloc_data.get(
            "asset_class_targets", portfolio.allocation_target.asset_class_targets
        )
        tolerance_bands = alloc_data.get(
            "tolerance_bands", portfolio.allocation_target.tolerance_bands
        )
        
        # Ensure all values are Decimals
        if asset_class_targets:
            asset_class_targets = {
                k: Decimal(str(v)) for k, v in asset_class_targets.items()
            }
        
        if tolerance_bands:
            tolerance_bands = {
                k: Decimal(str(v)) for k, v in tolerance_bands.items()
            }
        
        portfolio.allocation_target = AllocationTarget(
            target_id=portfolio.allocation_target.target_id,
            account_id=portfolio.allocation_target.account_id,
            asset_class_targets=asset_class_targets,
            security_targets=alloc_data.get(
                "security_targets", portfolio.allocation_target.security_targets
            ),
            tolerance_bands=tolerance_bands,
        )

    # Update timestamp
    portfolio.updated_at = datetime.now()

    # Save updated portfolio
    store.save_portfolio(portfolio)

    logger.info(f"Updated preferences for client {client_id}")

    # Return updated preferences
    return await get_preferences(client_id, store, actor, settings)


@router.get("/{client_id}/history")
async def get_preference_history(
    client_id: str,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
) -> list[dict]:
    """
    Get preference change history for a client.

    Args:
        client_id: Client identifier
        store: Workflow store

    Returns:
        List of preference changes
    """
    # TODO: Implement preference history tracking
    # For now, return empty list
    return []
