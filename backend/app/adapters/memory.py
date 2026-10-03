from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    category: str
    summary: str
    confidence: float

    # Additional fields for LLM integration
    timestamp: Optional[str] = None
    memory_type: Optional[str] = None
    content: Optional[str] = None
    relevance_score: Optional[float] = None


class LocalMemoryAdapter:
    """Local deterministic memory adapter returning synthetic default preferences."""

    def __init__(self) -> None:
        self._items: dict[str, list[MemoryItem]] = {}

    async def retrieve(
        self,
        client_id: str,
        semantic_query: Optional[str] = None,
        keywords: Optional[list[str]] = None,
    ) -> list[MemoryItem]:
        """Retrieve memory items for a client."""
        if client_id in self._items:
            return list(self._items[client_id])
        return [
            MemoryItem(
                memory_id=f"mem_{client_id}_preference",
                category="derived_preference",
                summary="Synthetic default preference: keep recommendations advisory-only.",
                confidence=0.8,
                timestamp="2024-01-01T00:00:00Z",
                memory_type="PREFERENCE",
                content="Synthetic default preference: keep recommendations advisory-only.",
                relevance_score=0.8,
            )
        ]

    async def list_memories(self, user_id: str) -> list[MemoryItem]:
        return await self.retrieve(client_id=user_id)

    async def delete(self, memory_name: str, user_id: Optional[str] = None) -> bool:
        if user_id and user_id in self._items:
            self._items[user_id] = [m for m in self._items[user_id] if m.memory_id != memory_name]
            return True
        for uid in list(self._items.keys()):
            self._items[uid] = [m for m in self._items[uid] if m.memory_id != memory_name]
        return True


class MemoryBankAdapter:
    """Adapter for Vertex AI Memory Bank retrieval and management (Task P2-02, P2-05)."""

    def __init__(
        self,
        project: str = "mybrightday-dev",
        location: str = "us-central1",
        runtime_name: Optional[str] = None,
        client: Any = None,
    ) -> None:
        self.project = project
        self.location = location
        self._runtime_name = runtime_name
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import agentplatform

            self._client = agentplatform.Client(project=self.project, location=self.location)
        return self._client

    @property
    def runtime_name(self) -> str:
        if not self._runtime_name:
            deployed_file = (
                Path(__file__).resolve().parent.parent
                / "agent_runtime"
                / "deployed_runtime.txt"
            )
            if deployed_file.exists():
                self._runtime_name = deployed_file.read_text().strip()
            if not self._runtime_name:
                raise ValueError("Agent Runtime resource name is required for MemoryBankAdapter")
        return self._runtime_name

    async def retrieve(
        self,
        user_id: str,
        semantic_query: Optional[str] = None,
        keywords: Optional[list[str]] = None,
    ) -> list[MemoryItem]:
        """Retrieve memories for user_id, top 5, capped at 1,000 characters per fact."""
        try:
            scope = {"user_id": user_id}
            retrieved_raw = list(
                self.client.memory_banks.memories.retrieve(
                    name=self.runtime_name,
                    scope=scope,
                )
            )

            # Cap at top 5
            top_items = retrieved_raw[:5]
            memories: list[MemoryItem] = []

            for idx, item in enumerate(top_items):
                mem = getattr(item, "memory", item)
                fact = getattr(mem, "fact", "") or ""
                # Cap at 1,000 characters
                fact_capped = fact[:1000]

                mem_name = getattr(mem, "name", f"mem_{user_id}_{idx}")
                create_time = getattr(mem, "create_time", None)
                ts_str = str(create_time) if create_time else "2026-10-03T00:00:00Z"

                distance = getattr(item, "distance", None)
                relevance = round(max(0.0, 1.0 - float(distance)), 2) if distance is not None else 0.9

                memories.append(
                    MemoryItem(
                        memory_id=mem_name,
                        category="user_preference",
                        summary=fact_capped,
                        confidence=0.9,
                        timestamp=ts_str,
                        memory_type="PREFERENCE",
                        content=fact_capped,
                        relevance_score=relevance,
                    )
                )

            return memories
        except Exception as e:
            logger.warning(f"Memory Bank retrieval failed for user_id={user_id}: {e}", exc_info=True)
            return []

    async def get_memory(self, memory_name: str) -> Optional[Any]:
        """Fetch memory item details."""
        try:
            return self.client.memory_banks.memories.get(name=memory_name)
        except Exception as e:
            logger.warning(f"Failed to get memory {memory_name}: {e}")
            return None

    async def delete(self, memory_name: str, user_id: Optional[str] = None) -> bool:
        """Delete a memory item by resource name, verifying user_id scope if specified."""
        try:
            if user_id:
                mem = await self.get_memory(memory_name)
                if mem and hasattr(mem, "scope") and mem.scope:
                    mem_user = mem.scope.get("user_id")
                    if mem_user and mem_user != user_id:
                        raise PermissionError(
                            f"Memory {memory_name} does not belong to user {user_id}"
                        )

            op = self.client.memory_banks.memories.delete(name=memory_name)
            return bool(getattr(op, "done", True))
        except PermissionError:
            raise
        except Exception as e:
            logger.warning(f"Memory Bank delete failed for {memory_name}: {e}")
            return False

    async def list_memories(self, user_id: str) -> list[MemoryItem]:
        """List all memories for a user scope."""
        return await self.retrieve(user_id=user_id)


def get_memory_adapter(settings: Optional[Any] = None) -> Any:
    """Factory to get configured memory adapter."""
    from app.core.config import get_settings

    s = settings or get_settings()
    if s.memory_mode == "memory_bank":
        return MemoryBankAdapter(
            project=s.project_id,
            location=s.gcp_location,
            runtime_name=s.agent_runtime_resource_name or None,
        )
    return LocalMemoryAdapter()
