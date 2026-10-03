"""Smoke tests for Vertex AI Agent Runtime integration (Task P1-08)."""

import os
from pathlib import Path
import pytest

from app.agent_runtime.app import RebalanceGraphApp
from tests.test_rebalance import request_payload


def test_rebalance_graph_app_local_smoke():
    """Verify local RebalanceGraphApp execution produces READY_FOR_REVIEW with trades."""
    app_instance = RebalanceGraphApp(
        project="mybrightday-dev",
        location="us-central1",
        tool_mode="inprocess",
    )
    app_instance.set_up()

    payload = request_payload()
    result = app_instance.query(payload)

    assert result["workflow_state"] == "NORMAL"
    assert result["approval_artifact"] is not None
    assert result["approval_artifact"]["approval_status"] == "PENDING"
    assert result["recommendation_package"]["proposal"]["proposal_status"] == "READY_FOR_REVIEW"
    assert len(result["recommendation_package"]["proposal"]["trades"]) > 0


@pytest.mark.gcp
@pytest.mark.skipif(
    not os.environ.get("RUN_GCP_TESTS"),
    reason="Set RUN_GCP_TESTS=1 to run smoke test against remotely deployed Agent Runtime",
)
def test_rebalance_graph_app_remote_smoke():
    """Verify remotely deployed Agent Runtime on GCP executes query and returns valid response."""
    import agentplatform

    project = os.environ.get("GCP_PROJECT", "mybrightday-dev")
    location = os.environ.get("GCP_LOCATION", "us-central1")

    resource_file = Path(__file__).resolve().parent.parent.parent / "app" / "agent_runtime" / "deployed_runtime.txt"
    if not resource_file.exists():
        pytest.skip(f"Deployed runtime resource file not found: {resource_file}")

    runtime_name = resource_file.read_text().strip()
    client = agentplatform.Client(project=project, location=location)
    runtime = client.runtimes.get(name=runtime_name)

    payload = request_payload()
    response = runtime.query(request=payload)

    assert "workflow_state" in response, f"Invalid response from Agent Runtime: {response}"
    assert response["workflow_state"] in {"NORMAL", "LOW_CONFIDENCE"}
    assert response.get("approval_artifact") is not None
    assert len(response.get("recommendation_package", {}).get("proposal", {}).get("trades", [])) > 0
