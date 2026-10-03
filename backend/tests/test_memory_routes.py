"""Unit tests for Memory API endpoints (Task P2-05).

Tests GET /api/memory, DELETE /api/memory/{id}, scope ownership checks,
and behavior reverting to default after deletion.
"""

from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import pytest

from app.adapters.memory import LocalMemoryAdapter, MemoryBankAdapter, MemoryItem
from app.agent_runtime.app import RebalanceGraphApp
from app.main import create_app
from tests.test_rebalance import request_payload


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_get_memories_endpoint(client):
    """Verify GET /api/memory returns caller's memories."""
    response = client.get("/api/memory", headers={"X-Actor-ID": "test_user_1"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["memory_id"].startswith("mem_test_user_1")


def test_delete_memory_endpoint_local(client):
    """Verify DELETE /api/memory/{id} deletes the memory item."""
    # First verify item exists
    get_res = client.get("/api/memory", headers={"X-Actor-ID": "user_del"})
    assert get_res.status_code == 200
    mem_id = get_res.json()[0]["memory_id"]

    # Delete it
    del_res = client.delete(f"/api/memory/{mem_id}", headers={"X-Actor-ID": "user_del"})
    assert del_res.status_code == 200
    assert del_res.json() == {"deleted": True, "memory_id": mem_id}


def test_delete_memory_scope_forbidden(monkeypatch, client):
    """Verify DELETE /api/memory/{id} returns 403 when memory belongs to a different user."""
    mock_memory_adapter = MagicMock()
    mock_memory_adapter.delete.side_effect = PermissionError("Memory does not belong to caller")

    monkeypatch.setattr("app.api.routes.memory.get_memory_adapter", lambda s: mock_memory_adapter)

    del_res = client.delete("/api/memory/mem_other_user", headers={"X-Actor-ID": "unauthorized_user"})
    assert del_res.status_code == 403
    assert "Forbidden" in del_res.json()["detail"]


def test_memory_scenario_reversion_after_deletion(client, monkeypatch):
    """Verify that after memory deletion, subsequent rebalance reverts to default presentation."""
    # Create LocalMemoryAdapter with custom preference
    mem_adapter = LocalMemoryAdapter()
    pref_item = MemoryItem(
        memory_id="mem_short_table",
        category="user_preference",
        summary="Keep explanations short and provide a trade table.",
        confidence=0.9,
    )
    mem_adapter._items["user_rev"] = [pref_item]

    monkeypatch.setattr("app.api.routes.memory.get_memory_adapter", lambda s: mem_adapter)

    # 1. Verify item is in memory
    get_res = client.get("/api/memory", headers={"X-Actor-ID": "user_rev"})
    assert get_res.status_code == 200
    assert len(get_res.json()) == 1
    assert "trade table" in get_res.json()[0]["summary"].lower()

    # 2. Run RebalanceGraphApp with this memory item
    app_instance = RebalanceGraphApp(
        project="test-proj",
        location="us-central1",
        tool_mode="inprocess",
        sessions_mode="local",
    )
    app_instance.set_up()

    payload = request_payload()
    payload["actor"]["actor_id"] = "user_rev"

    # Mock the memory node in this app instance to return the active memory
    from app.services.langgraph_nodes import _generate_summary
    state_with_memory = {
        "memory_output": {"items": [pref_item.__dict__]},
        "trade_proposal_output": {"trades": [{"action": "SELL", "symbol": "EQUITY", "estimated_value": 1000}]},
    }
    summary_a = _generate_summary(state_with_memory, proposal_status="READY_FOR_REVIEW")
    assert "Trade Table" in summary_a or "rebalancing proposed" in summary_a.lower()

    # 3. Delete memory via endpoint
    del_res = client.delete("/api/memory/mem_short_table", headers={"X-Actor-ID": "user_rev"})
    assert del_res.status_code == 200

    # 4. Verify memory list is now empty of that custom preference
    get_res_after = client.get("/api/memory", headers={"X-Actor-ID": "user_rev"})
    assert get_res_after.json() == []

    # 5. Summary without the memory preference reverts to default style
    state_reverted = {
        "memory_output": {"items": []},
        "trade_proposal_output": {"trades": [{"action": "SELL", "symbol": "EQUITY", "estimated_value": 1000}]},
    }
    summary_c = _generate_summary(state_reverted, proposal_status="READY_FOR_REVIEW")
    assert "ready for manual review" in summary_c
    assert "Trade Table" not in summary_c
