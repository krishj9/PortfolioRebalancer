from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.contracts.common import ActorContext
from app.contracts.domain import PortfolioRecord
from app.core.auth import get_current_actor, verify_account_ownership
from app.core.config import Settings, get_settings
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import WorkflowStore

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


@router.get("", response_model=list[PortfolioRecord])
async def list_portfolios(
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
    actor: Annotated[ActorContext, Depends(get_current_actor)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[PortfolioRecord]:
    portfolios = store.list_portfolios()
    if settings.auth_mode.lower() == "none" and actor.actor_id == "local_owner":
        return portfolios
    # Filter by actor ownership
    return [
        p for p in portfolios
        if getattr(p, "owner_email", "local_owner") in (actor.actor_id, "local_owner")
    ]


@router.get("/{account_id}", response_model=PortfolioRecord)
async def get_portfolio(
    account_id: str,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
    actor: Annotated[ActorContext, Depends(get_current_actor)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PortfolioRecord:
    portfolio = store.get_portfolio(account_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    verify_account_ownership(portfolio, actor, settings)
    return portfolio

