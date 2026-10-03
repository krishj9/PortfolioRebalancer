"""Unit tests for RuntimeClient and agent_runtime orchestration mode (Task P1-09)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.contracts.common import ErrorSeverity
from app.contracts.workflow import OrchestrationResponse, PortfolioRebalanceRequest
from app.core.config import Settings
from app.main import create_app
from app.persistence.memory_store import InMemoryWorkflowStore
from app.services.orchestrator import Orchestrator
from app.services.runtime_client import RuntimeClient
from tests.test_rebalance import request_payload


@pytest.fixture
def sample_orchestration_response() -> OrchestrationResponse:
    req = PortfolioRebalanceRequest.model_validate(request_payload())
    orchestrator = Orchestrator(InMemoryWorkflowStore())
    return asyncio.run(orchestrator.run(req))


def test_runtime_client_success(sample_orchestration_response: OrchestrationResponse):
    """Verify RuntimeClient successfully invokes remote runtime and returns OrchestrationResponse."""
    mock_runtime = MagicMock()
    mock_app = MagicMock()
    mock_app.runtimes.get.return_value = mock_runtime

    req = PortfolioRebalanceRequest.model_validate(request_payload())
    mock_runtime.query.return_value = sample_orchestration_response.model_dump(mode="json")

    with patch("agentplatform.Client", return_value=mock_app):
        client = RuntimeClient(
            project="test-proj",
            location="us-central1",
            resource_name="projects/123/locations/us-central1/reasoningEngines/456",
        )
        response = asyncio.run(client.run(req))

        assert isinstance(response, OrchestrationResponse)
        assert response.workflow_state.value == "NORMAL"
        assert response.approval_artifact.approval_id == sample_orchestration_response.approval_artifact.approval_id
        mock_runtime.query.assert_called_once()


def test_runtime_client_error_mapping_to_502():
    """Verify runtime query failure is caught and mapped to 502 with StructuredError."""
    from fastapi import HTTPException
    mock_runtime = MagicMock()
    mock_runtime.query.side_effect = RuntimeError("Reasoning Engine unavailable")
    mock_app = MagicMock()
    mock_app.runtimes.get.return_value = mock_runtime

    with patch("agentplatform.Client", return_value=mock_app):
        client = RuntimeClient(
            project="test-proj",
            location="us-central1",
            resource_name="projects/123/locations/us-central1/reasoningEngines/456",
        )
        req = PortfolioRebalanceRequest.model_validate(request_payload())

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(client.run(req))

        assert exc_info.value.status_code == 502
        detail = exc_info.value.detail
        assert detail["code"] == "AGENT_RUNTIME_EXECUTION_FAILED"
        assert detail["retryable"] is True
        assert "Reasoning Engine unavailable" in detail["message"]
        assert "retry_hint" in detail["details"]


def test_route_orchestration_mode_agent_runtime(sample_orchestration_response: OrchestrationResponse):
    """Verify /api/rebalance delegates to RuntimeClient when orchestration_mode is agent_runtime."""
    payload = request_payload()
    app = create_app()

    mock_runtime_client = MagicMock(spec=RuntimeClient)
    mock_runtime_client.run = AsyncMock()
    mock_runtime_client.run.return_value = sample_orchestration_response

    from app.api.routes.rebalance import get_runtime_client
    from app.core.config import get_settings
    from app.persistence.dependencies import get_workflow_store

    test_settings = Settings(orchestration_mode="agent_runtime", persistence_mode="memory")
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_workflow_store] = lambda: InMemoryWorkflowStore()
    app.dependency_overrides[get_runtime_client] = lambda: mock_runtime_client

    client = TestClient(app)
    response = client.post("/api/rebalance", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["approval_artifact"]["approval_id"] == sample_orchestration_response.approval_artifact.approval_id
    mock_runtime_client.run.assert_awaited_once()
