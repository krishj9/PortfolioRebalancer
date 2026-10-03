"""Tests for RebalanceGraphApp local execution and query interface."""

import pytest

from app.agent_runtime.app import RebalanceGraphApp
from tests.test_rebalance import request_payload


def test_rebalance_graph_app_inprocess_query():
    """Verify RebalanceGraphApp executes successfully in inprocess mode."""
    app_instance = RebalanceGraphApp(
        project="mybrightday-dev",
        location="us-central1",
        tool_mode="inprocess",
    )
    app_instance.set_up()

    payload = request_payload()
    result = app_instance.query(payload, run_id="run-test-123", session_id="ses-test-456")

    assert result["workflow_state"] == "NORMAL"
    assert result["approval_artifact"] is not None
    assert result["approval_artifact"]["approval_status"] == "PENDING"
    assert len(result["recommendation_package"]["proposal"]["trades"]) > 0
    assert result["correlation"]["trace_id"] == "run-test-123"
    assert result["correlation"]["session_id"] == "ses-test-456"
