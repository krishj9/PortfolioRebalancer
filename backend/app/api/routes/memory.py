"""Memory API routes for retrieving and deleting user memories (Task P2-05)."""

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.adapters.memory import MemoryItem, get_memory_adapter
from app.core.config import Settings, get_settings

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=list[dict[str, Any]])
async def list_user_memories(
    settings: Annotated[Settings, Depends(get_settings)],
    actor_id: Annotated[Optional[str], Header(alias="X-Actor-ID")] = None,
    user_id: Annotated[Optional[str], Query()] = None,
) -> list[dict[str, Any]]:
    """List memory items for the authenticated user/actor scope."""
    effective_user_id = user_id or actor_id or "local_owner"
    adapter = get_memory_adapter(settings)
    items = await adapter.list_memories(user_id=effective_user_id)
    return [
        {
            "memory_id": item.memory_id,
            "category": item.category,
            "summary": item.summary,
            "confidence": item.confidence,
            "timestamp": item.timestamp,
            "content": item.content,
            "relevance_score": item.relevance_score,
        }
        for item in items
    ]


@router.delete("/{memory_id:path}")
async def delete_user_memory(
    memory_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    actor_id: Annotated[Optional[str], Header(alias="X-Actor-ID")] = None,
    user_id: Annotated[Optional[str], Query()] = None,
) -> dict[str, Any]:
    """Delete a memory item, ensuring it belongs to the caller's scope."""
    effective_user_id = user_id or actor_id or "local_owner"
    adapter = get_memory_adapter(settings)
    try:
        deleted = await adapter.delete(memory_name=memory_id, user_id=effective_user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory not found or deletion failed")
        return {"deleted": True, "memory_id": memory_id}
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: {str(exc)}",
        ) from exc
