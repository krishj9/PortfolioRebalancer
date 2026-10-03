"""Tests for Retry, Fault Injection, and Idempotent Recovery Scenarios (Task P4-03)."""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.contracts.common import ErrorSeverity
from app.contracts.workflow import PortfolioRebalanceRequest
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import InMemoryWorkflowStore
from tests.test_rebalance import request_payload


@pytest.fixture
def test_setup():
    store = InMemoryWorkflowStore()
    app.dependency_overrides[get_workflow_store] = lambda: store
    client = TestClient(app)
    req = PortfolioRebalanceRequest.model_validate(request_payload())
    yield client, store, req
    app.dependency_overrides.clear()


def test_tool_fault_injection_503(test_setup):
    """Verify tool router intercepts and simulates 503 when X-Fault-Inject is set."""
    client, store, req = test_setup
    payload = {"account_id": req.account_profile.account_id}
    resp = client.post(
        "/tools/get_portfolio",
        json=payload,
        headers={"X-Fault-Inject": "503"},
    )
    assert resp.status_code == 503
    assert "Simulated transient service unavailability" in resp.json()["detail"]
    assert resp.headers.get("retry-after") == "1"


def test_tool_normal_operation_without_fault_injection(test_setup):
    """Verify tool endpoint succeeds normally when fault injection header is absent."""
    client, store, req = test_setup
    payload = {
        "portfolio_snapshot": req.portfolio_snapshot.model_dump(mode="json"),
        "allocation_target": req.allocation_target.model_dump(mode="json"),
    }
    resp = client.post("/tools/compute_drift", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "current_allocation" in data
    assert "drift" in data


def test_rebalance_retry_recovery_with_idempotency_key(test_setup):
    """
    Verify complete retry/restart scenario:
    1. First attempt fails due to simulated downstream outage -> returns retryable 500/502/503.
    2. Retry with same Idempotency-Key after service recovers -> returns 200 with proposal.
    3. Subsequent retry with same Idempotency-Key returns same proposal without duplicate.
    """
    client, store, req = test_setup
    idempotency_key = "idemp-test-retry-scenario-999"
    raw_payload = request_payload()

    # Step 1: Simulate downstream failure during rebalance
    with patch("app.services.orchestrator.Orchestrator.run", side_effect=RuntimeError("Vertex runtime timeout")):
        with pytest.raises(RuntimeError):
            client.post(
                "/rebalance",
                json=raw_payload,
                headers={"Idempotency-Key": idempotency_key},
            )

    # Step 2: Downstream service recovers; retry with exact same Idempotency-Key
    resp2 = client.post(
        "/rebalance",
        json=raw_payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["workflow_state"] in ("NORMAL", "COMPLIANT", "PENDING_APPROVAL")
    approval_id = data2["approval_artifact"]["approval_id"]
    assert approval_id is not None

    # Step 3: Duplicate retry with same Idempotency-Key returns identical artifact
    resp3 = client.post(
        "/rebalance",
        json=raw_payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["approval_artifact"]["approval_id"] == approval_id
    assert len(store.approvals) == 1
