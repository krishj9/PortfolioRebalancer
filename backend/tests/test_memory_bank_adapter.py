"""Unit tests for MemoryBankAdapter and Memory Bank integration (Task P2-02)."""

from unittest.mock import MagicMock
import pytest

from app.adapters.memory import MemoryBankAdapter, MemoryItem
from app.contracts.common import ActorContext, CorrelationMetadata
from app.contracts.workflow import PortfolioRebalanceRequest
from app.services.langgraph_graph import hydrate_memory_node
from tests.test_rebalance import request_payload


class MockMemory:
    def __init__(self, name: str, fact: str):
        self.name = name
        self.fact = fact
        self.scope = {"user_id": "test_actor"}
        self.create_time = "2026-10-03T10:00:00Z"


class MockRetrievedItem:
    def __init__(self, name: str, fact: str, distance: float = 0.1):
        self.memory = MockMemory(name, fact)
        self.distance = distance


@pytest.mark.asyncio
async def test_memory_bank_adapter_retrieval_and_capping():
    """Verify MemoryBankAdapter caps at top 5 items and truncates facts > 1000 chars."""
    mock_client = MagicMock()
    # Create 7 items with long facts
    long_fact = "A" * 1500
    mock_items = [
        MockRetrievedItem(name=f"projects/123/memories/{i}", fact=f"Preference {i}: {long_fact}", distance=0.05 * i)
        for i in range(7)
    ]
    mock_client.memory_banks.memories.retrieve.return_value = iter(mock_items)

    adapter = MemoryBankAdapter(
        project="test-proj",
        location="us-central1",
        runtime_name="projects/123/reasoningEngines/456",
        client=mock_client,
    )

    items = await adapter.retrieve(user_id="user_demo")

    # Assert top 5 capped
    assert len(items) == 5
    mock_client.memory_banks.memories.retrieve.assert_called_once_with(
        name="projects/123/reasoningEngines/456",
        scope={"user_id": "user_demo"},
    )

    # Assert 1,000 char cap on summary and content
    for item in items:
        assert isinstance(item, MemoryItem)
        assert len(item.summary) <= 1000
        assert len(item.content) <= 1000
        assert item.category == "user_preference"
        assert item.confidence == 0.9
        assert item.memory_type == "PREFERENCE"
        assert item.relevance_score is not None


@pytest.mark.asyncio
async def test_memory_bank_adapter_failure_fallback():
    """Verify MemoryBankAdapter gracefully returns empty list on client failure."""
    mock_client = MagicMock()
    mock_client.memory_banks.memories.retrieve.side_effect = RuntimeError("API unavailable")

    adapter = MemoryBankAdapter(
        project="test-proj",
        location="us-central1",
        runtime_name="projects/123/reasoningEngines/456",
        client=mock_client,
    )

    items = await adapter.retrieve(user_id="user_demo")
    assert items == []


@pytest.mark.asyncio
async def test_hydrate_memory_node_with_memory_bank_mode(monkeypatch):
    """Verify hydrate_memory_node uses MemoryBankAdapter when MEMORY_MODE=memory_bank."""
    monkeypatch.setenv("MEMORY_MODE", "memory_bank")
    monkeypatch.setenv("AGENT_RUNTIME_RESOURCE_NAME", "projects/123/reasoningEngines/456")

    # Clear lru_cache for settings
    from app.core.config import get_settings
    get_settings.cache_clear()

    mock_client = MagicMock()
    mock_items = [
        MockRetrievedItem(name="mem_1", fact="Client prefers low turnover portfolios.", distance=0.1)
    ]
    mock_client.memory_banks.memories.retrieve.return_value = iter(mock_items)

    # Patch agentplatform.Client
    import sys
    mock_agentplatform = MagicMock()
    mock_agentplatform.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "agentplatform", mock_agentplatform)

    payload = request_payload()
    req = PortfolioRebalanceRequest.model_validate(payload)
    state = {
        "request_id": req.correlation.request_id,
        "request": req,
        "actor": req.actor,
    }

    result = await hydrate_memory_node(state)
    assert "memory_output" in result
    items = result["memory_output"]["items"]
    assert len(items) == 1
    assert "low turnover" in items[0]["summary"]

    get_settings.cache_clear()
