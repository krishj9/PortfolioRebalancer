"""Authentication and resource authorization (Task P3-06)."""

import logging
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Request, status

from app.contracts.common import ActorContext, ActorRole
from app.contracts.domain import PortfolioRecord
from app.core.config import Settings, get_settings
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import WorkflowStore

logger = logging.getLogger(__name__)


def extract_actor_from_request(
    request: Request,
    settings: Settings,
    x_goog_authenticated_user_email: Optional[str] = None,
    x_goog_iap_jwt_assertion: Optional[str] = None,
    x_actor_id: Optional[str] = None,
) -> ActorContext:
    """
    Extract authenticated actor context from IAP headers or dev fallbacks.

    In production behind GCP IAP:
    - X-Goog-Authenticated-User-Email is formatted as "accounts.google.com:user@domain.com"
      or simply "user@domain.com".
    - X-Goog-IAP-JWT-Assertion contains signed cryptographically verifiable token.

    In local/dev mode (AUTH_MODE=none):
    - Fallback to X-Actor-Id or 'local_owner'.
    """
    auth_mode = settings.auth_mode.lower()

    # 1. Check IAP header first (takes precedence whenever present)
    email_header = x_goog_authenticated_user_email or request.headers.get("x-goog-authenticated-user-email")
    if email_header:
        # Strip accounts.google.com: prefix if present
        email = email_header.split(":")[-1].strip() if ":" in email_header else email_header.strip()
        if email:
            logger.debug("Resolved actor from IAP header: %s", email)
            return ActorContext(
                actor_id=email,
                display_name=email.split("@")[0].replace(".", " ").capitalize(),
                role=ActorRole.OWNER,
            )

    # 2. If AUTH_MODE is explicitly IAP and header is missing -> 401 Unauthorized
    if auth_mode == "iap":
        logger.warning("IAP authentication required but no IAP header present")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: missing IAP identity header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Local/Dev fallback (AUTH_MODE=none or dev)
    dev_actor = (
        x_actor_id
        or request.headers.get("x-actor-id")
        or "local_owner"
    )
    return ActorContext(
        actor_id=dev_actor,
        display_name=dev_actor,
        role=ActorRole.OWNER,
    )


async def get_current_actor(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_goog_authenticated_user_email: Annotated[Optional[str], Header(alias="x-goog-authenticated-user-email")] = None,
    x_goog_iap_jwt_assertion: Annotated[Optional[str], Header(alias="x-goog-iap-jwt-assertion")] = None,
    x_actor_id: Annotated[Optional[str], Header(alias="x-actor-id")] = None,
) -> ActorContext:
    """FastAPI dependency resolving the caller's ActorContext."""
    return extract_actor_from_request(
        request=request,
        settings=settings,
        x_goog_authenticated_user_email=x_goog_authenticated_user_email,
        x_goog_iap_jwt_assertion=x_goog_iap_jwt_assertion,
        x_actor_id=x_actor_id,
    )


def verify_account_ownership(
    portfolio: PortfolioRecord,
    actor: ActorContext,
    settings: Settings,
) -> None:
    """
    Verify that the actor owns or is authorized to operate on the given portfolio record.

    Rules:
    - If settings.auth_mode == "none" and actor.actor_id == "local_owner", access is permitted.
    - If portfolio.owner_email matches actor.actor_id, access is permitted.
    - If portfolio.owner_email == "local_owner" and actor.actor_id == "local_owner", access is permitted.
    - Otherwise, HTTP 403 Forbidden is raised.
    """
    if settings.auth_mode.lower() == "none" and actor.actor_id == "local_owner":
        return

    # Authorized if matching owner_email
    owner = getattr(portfolio, "owner_email", None) or "local_owner"
    if owner == actor.actor_id or (owner == "local_owner" and actor.actor_id == "local_owner"):
        return

    logger.warning(
        "Unauthorized access attempt: actor '%s' attempted to access portfolio owned by '%s'",
        actor.actor_id,
        owner,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Actor '{actor.actor_id}' is not authorized to access this portfolio",
    )
