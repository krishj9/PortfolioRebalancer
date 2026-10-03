"""Unit tests for ToolClient in inprocess and remote modes."""

import pytest
import httpx
from unittest.mock import AsyncMock, patch

from app.contracts.analysis import (
    ApprovalArtifact,
    ExecutionProposalResponse,
    PolicyVerdictStatus,
    RiskPolicyResponse,
)
from app.contracts.common import ActorContext, CorrelationMetadata, new_id
from app.contracts.domain import PortfolioRecord
from app.contracts.workflow import PortfolioRebalanceRequest
from app.main import app
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import InMemoryWorkflowStore
from app.services.orchestrator import Orchestrator
from app.tools.client import ToolClient
from tests.test_rebalance import request_payload


@pytest.fixture
def store():
    return InMemoryWorkflowStore()


@pytest.fixture
def rebalance_request():
    return PortfolioRebalanceRequest.model_validate(request_payload())


@pytest.mark.asyncio
async def test_tool_client_inprocess(store, rebalance_request):
    """Test all ToolClient methods in inprocess mode."""
    client = ToolClient(mode="inprocess", store=store)
    account_id = rebalance_request.account_profile.account_id

    # 1. get_portfolio
    portfolio = await client.get_portfolio(account_id)
    assert portfolio is not None
    assert portfolio.account_profile.account_id == account_id

    missing = await client.get_portfolio("nonexistent")
    assert missing is None

    # 2. compute_drift
    drift_resp = await client.compute_drift(
        rebalance_request.portfolio_snapshot,
        rebalance_request.allocation_target,
    )
    assert len(drift_resp.current_allocation) > 0
    assert len(drift_resp.drift) > 0

    # 3. evaluate_policy
    risk_resp = await client.evaluate_policy(
        rebalance_request.portfolio_snapshot,
        drift_resp.drift,
        rebalance_request.risk_profile,
    )
    assert risk_resp.verdict in {
        PolicyVerdictStatus.COMPLIANT,
        PolicyVerdictStatus.NON_COMPLIANT,
        PolicyVerdictStatus.UNRESOLVED,
    }
    assert len(risk_resp.rule_results) > 0

    # 4. generate_proposal
    proposal_resp = await client.generate_proposal(
        rebalance_request.portfolio_snapshot,
        risk_resp,
    )
    assert proposal_resp.proposal_status in {"READY_FOR_REVIEW", "NO_ACTION_NEEDED", "BLOCKED"}
    assert len(proposal_resp.trades) > 0

    # 5. persist_proposal
    orch = Orchestrator(store)
    orch_res = await orch.run(rebalance_request)
    artifact = orch_res.approval_artifact
    artifact.approval_id = "apr_inprocess_test"
    saved = await client.persist_proposal(artifact)
    assert saved.approval_id == "apr_inprocess_test"
    assert store.get_approval("apr_inprocess_test") is not None

    # 6. audit
    evt = await client.audit(
        event_type="TEST_EVENT",
        correlation=rebalance_request.correlation,
        outcome="SUCCESS",
        actor_id="test_actor",
    )
    assert evt.event_type == "TEST_EVENT"
    assert evt.outcome == "SUCCESS"
    assert len(store.list_audit_events()) >= 1


@pytest.mark.asyncio
async def test_tool_client_remote(store, rebalance_request):
    """Test all ToolClient methods in remote mode via ASGI transport."""
    app.dependency_overrides[get_workflow_store] = lambda: store
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            client = ToolClient(mode="remote", http_client=http_client)
            account_id = rebalance_request.account_profile.account_id

            # 1. get_portfolio
            portfolio = await client.get_portfolio(account_id)
            assert portfolio is not None
            assert portfolio.account_profile.account_id == account_id

            missing = await client.get_portfolio("nonexistent")
            assert missing is None

            # 2. compute_drift
            drift_resp = await client.compute_drift(
                rebalance_request.portfolio_snapshot,
                rebalance_request.allocation_target,
            )
            assert len(drift_resp.current_allocation) > 0
            assert len(drift_resp.drift) > 0

            # 3. evaluate_policy
            risk_resp = await client.evaluate_policy(
                rebalance_request.portfolio_snapshot,
                drift_resp.drift,
                rebalance_request.risk_profile,
            )
            assert risk_resp.verdict in {
                PolicyVerdictStatus.COMPLIANT,
                PolicyVerdictStatus.NON_COMPLIANT,
                PolicyVerdictStatus.UNRESOLVED,
            }
            assert len(risk_resp.rule_results) > 0

            # 4. generate_proposal
            proposal_resp = await client.generate_proposal(
                rebalance_request.portfolio_snapshot,
                risk_resp,
            )
            assert proposal_resp.proposal_status in {"READY_FOR_REVIEW", "NO_ACTION_NEEDED", "BLOCKED"}
            assert len(proposal_resp.trades) > 0

            # 5. persist_proposal
            orch = Orchestrator(store)
            orch_res = await orch.run(rebalance_request)
            artifact = orch_res.approval_artifact
            artifact.approval_id = "apr_remote_test"
            saved = await client.persist_proposal(artifact)
            assert saved.approval_id == "apr_remote_test"
            assert store.get_approval("apr_remote_test") is not None

            # 6. audit
            evt = await client.audit(
                event_type="TEST_EVENT",
                correlation=rebalance_request.correlation,
                outcome="SUCCESS",
                actor_id="test_actor",
            )
            assert evt.event_type == "TEST_EVENT"
            assert evt.outcome == "SUCCESS"
            assert len(store.list_audit_events()) >= 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_tool_client_retry_on_500():
    """Verify ToolClient retries on 500 error and succeeds on second attempt."""
    mock_http_client = AsyncMock()
    # 1st call returns 500, 2nd call returns 200 with JSON
    resp_500 = httpx.Response(status_code=500, request=httpx.Request("POST", "http://test/tools/audit"))
    resp_200 = httpx.Response(
        status_code=200,
        request=httpx.Request("POST", "http://test/tools/audit"),
        json={
            "event_id": "evt_123",
            "event_type": "AUDIT_TEST",
            "correlation": {
                "request_id": "req-1",
                "session_id": "ses-1",
                "trace_id": "tr-1",
            },
            "outcome": "OK",
            "actor_id": "user",
            "details": {},
            "created_at": "2026-10-03T12:00:00Z",
        },
    )
    mock_http_client.post.side_effect = [resp_500, resp_200]
    client = ToolClient(mode="remote", base_url="http://test", http_client=mock_http_client)

    result = await client.audit(
        event_type="AUDIT_TEST",
        correlation=CorrelationMetadata(request_id="req-1", session_id="ses-1", trace_id="tr-1"),
        outcome="OK",
    )
    assert result.event_id == "evt_123"
    assert mock_http_client.post.call_count == 2


@pytest.mark.asyncio
async def test_tool_client_no_retry_on_4xx():
    """Verify ToolClient does not retry on 4xx client errors."""
    mock_http_client = AsyncMock()
    resp_400 = httpx.Response(
        status_code=400,
        request=httpx.Request("POST", "http://test/tools/compute_drift"),
        text="Bad Request",
    )
    mock_http_client.post.return_value = resp_400
    client = ToolClient(mode="remote", base_url="http://test", http_client=mock_http_client)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client._post_with_retry("compute_drift", {})
    assert exc_info.value.response.status_code == 400
    assert mock_http_client.post.call_count == 1
