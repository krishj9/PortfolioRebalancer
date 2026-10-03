"""Unit tests for AnalyticsAdapter and BigQuery proposal event emission."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.adapters.analytics import (
    BigQueryAnalyticsAdapter,
    InMemoryAnalyticsAdapter,
    get_analytics_adapter,
)
from app.contracts.analysis import (
    ApprovalAction,
    ApprovalActionRequest,
    ApprovalArtifact,
    DriftItem,
    ExecutionProposalResponse,
    PolicyVerdictStatus,
    RecommendationPackage,
    RiskPolicyResponse,
    TradeAction,
    TradeProposal,
)
from app.contracts.common import ActorContext, CorrelationMetadata, WorkflowState
from app.contracts.domain import AccountProfile, ClientProfile, PortfolioSnapshot
from app.main import app
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import InMemoryWorkflowStore


from tests.test_firestore_store import create_sample_approval


def _sample_approval_artifact(approval_id: str = "apr_analytics_test") -> ApprovalArtifact:
    return create_sample_approval(approval_id)


def test_in_memory_analytics_adapter():
    adapter = InMemoryAnalyticsAdapter()
    assert len(adapter.events) == 0

    success = adapter.emit_proposal_event(
        event_ts=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
        proposal_id="apr_001",
        account_id="acct_001",
        event_type="PROPOSAL_CREATED",
        workflow_state="PENDING_APPROVAL",
        max_abs_drift_pct=0.10,
        trade_count=2,
        run_id="run_001",
    )
    assert success is True
    assert len(adapter.events) == 1
    ev = adapter.events[0]
    assert ev["proposal_id"] == "apr_001"
    assert ev["account_id"] == "acct_001"
    assert ev["event_type"] == "PROPOSAL_CREATED"
    assert ev["workflow_state"] == "PENDING_APPROVAL"
    assert ev["max_abs_drift_pct"] == 0.10
    assert ev["trade_count"] == 2
    assert ev["run_id"] == "run_001"

    adapter.clear()
    assert len(adapter.events) == 0


def test_bigquery_analytics_adapter_success():
    mock_client = MagicMock()
    mock_client.insert_rows_json.return_value = []  # No errors

    adapter = BigQueryAnalyticsAdapter(
        project_id="test-project",
        dataset_id="test_dataset",
        table_id="test_table",
        client=mock_client,
    )

    success = adapter.emit_proposal_event(
        event_ts=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
        proposal_id="apr_bq_1",
        account_id="acct_bq_1",
        event_type="PROPOSAL_CREATED",
        workflow_state="PENDING_APPROVAL",
        max_abs_drift_pct=0.08,
        trade_count=3,
        run_id="run_bq_1",
    )
    assert success is True
    mock_client.insert_rows_json.assert_called_once()
    table_arg, rows_arg = mock_client.insert_rows_json.call_args[0]
    assert table_arg == "test-project.test_dataset.test_table"
    assert len(rows_arg) == 1
    assert rows_arg[0]["proposal_id"] == "apr_bq_1"
    assert rows_arg[0]["max_abs_drift_pct"] == 0.08
    assert rows_arg[0]["trade_count"] == 3


def test_bigquery_analytics_adapter_error_handling():
    mock_client = MagicMock()
    # BigQuery streaming insert returns errors list if rows failed
    mock_client.insert_rows_json.return_value = [{"index": 0, "errors": [{"message": "Invalid field"}]}]

    adapter = BigQueryAnalyticsAdapter(
        project_id="test-project",
        dataset_id="test_dataset",
        table_id="test_table",
        client=mock_client,
    )

    success = adapter.emit_proposal_event(
        event_ts=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
        proposal_id="apr_fail",
        account_id="acct_fail",
        event_type="PROPOSAL_CREATED",
        workflow_state="PENDING_APPROVAL",
        max_abs_drift_pct=0.05,
        trade_count=1,
    )
    assert success is False

    # Also test unexpected client exception
    mock_client.insert_rows_json.side_effect = RuntimeError("Network timeout")
    success_exc = adapter.emit_proposal_event(
        event_ts=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
        proposal_id="apr_fail_2",
        account_id="acct_fail_2",
        event_type="PROPOSAL_CREATED",
        workflow_state="PENDING_APPROVAL",
        max_abs_drift_pct=0.05,
        trade_count=1,
    )
    assert success_exc is False


def test_persist_proposal_emits_analytics():
    store = InMemoryWorkflowStore()
    analytics = InMemoryAnalyticsAdapter()

    app.dependency_overrides[get_workflow_store] = lambda: store
    app.dependency_overrides[get_analytics_adapter] = lambda: analytics
    client = TestClient(app)

    try:
        artifact = _sample_approval_artifact("apr_router_test")
        response = client.post(
            "/tools/persist_proposal",
            json={"approval_artifact": artifact.model_dump(mode="json")},
        )
        assert response.status_code == 200

        # Verify event was emitted to analytics
        assert len(analytics.events) == 1
        ev = analytics.events[0]
        assert ev["proposal_id"] == "apr_router_test"
        assert ev["account_id"] == "acct_demo"
        assert ev["event_type"] == "PROPOSAL_CREATED"
        assert ev["workflow_state"] == "NORMAL"
        assert ev["max_abs_drift_pct"] == 10.0
        assert ev["trade_count"] == len(artifact.recommendation.proposal.trades)
        assert ev["run_id"] == artifact.correlation.trace_id
    finally:
        app.dependency_overrides.clear()


def test_approval_action_emits_analytics():
    store = InMemoryWorkflowStore()
    analytics = InMemoryAnalyticsAdapter()

    app.dependency_overrides[get_workflow_store] = lambda: store
    app.dependency_overrides[get_analytics_adapter] = lambda: analytics
    client = TestClient(app)

    try:
        artifact = _sample_approval_artifact("apr_action_test")
        store.save_approval(artifact)

        action_req = ApprovalActionRequest(
            action=ApprovalAction.APPROVE,
            actor_id="supervisor-1",
            note="Approved for execution",
            expected_recommendation_hash=artifact.recommendation_hash,
        )

        response = client.post(
            f"/api/approvals/{artifact.approval_id}/actions",
            json=action_req.model_dump(mode="json"),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] is True
        assert data["next_status"] == "APPROVED"

        # Verify analytics event
        assert len(analytics.events) == 1
        ev = analytics.events[0]
        assert ev["proposal_id"] == "apr_action_test"
        assert ev["account_id"] == "acct_demo"
        assert ev["event_type"] == "PROPOSAL_APPROVE"
        assert ev["workflow_state"] == "APPROVED"
        assert ev["max_abs_drift_pct"] == 10.0
        assert ev["trade_count"] == len(artifact.recommendation.proposal.trades)
        assert ev["run_id"] == artifact.correlation.trace_id
    finally:
        app.dependency_overrides.clear()


def test_analytics_failure_resilience():
    """Verify that if analytics fails, the operational tools and approvals endpoints still succeed."""
    store = InMemoryWorkflowStore()
    failing_analytics = MagicMock()
    failing_analytics.emit_proposal_event.side_effect = RuntimeError("BigQuery connection failure")

    app.dependency_overrides[get_workflow_store] = lambda: store
    app.dependency_overrides[get_analytics_adapter] = lambda: failing_analytics
    client = TestClient(app)

    try:
        artifact = _sample_approval_artifact("apr_resilience_test")

        # 1. persist_proposal still succeeds
        response = client.post(
            "/tools/persist_proposal",
            json={"approval_artifact": artifact.model_dump(mode="json")},
        )
        assert response.status_code == 200
        assert response.json()["approval_id"] == "apr_resilience_test"

        # 2. apply_approval_action still succeeds
        action_req = ApprovalActionRequest(
            action=ApprovalAction.APPROVE,
            actor_id="supervisor-1",
            note="Approved for execution",
            expected_recommendation_hash=artifact.recommendation_hash,
        )
        act_resp = client.post(
            f"/api/approvals/{artifact.approval_id}/actions",
            json=action_req.model_dump(mode="json"),
        )
        assert act_resp.status_code == 200
        assert act_resp.json()["accepted"] is True
    finally:
        app.dependency_overrides.clear()

