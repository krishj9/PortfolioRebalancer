"""Tests for deterministic tool service endpoints (/tools/*)."""

import os
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.contracts.analysis import (
    ApprovalAction,
    ApprovalActionRequest,
    ApprovalArtifact,
    AuditEvent,
    ExecutionProposalResponse,
    RiskPolicyResponse,
)
from app.tools.models import ComputeDriftResponse
from app.contracts.common import ActorContext, CorrelationMetadata
from app.contracts.domain import PortfolioRecord
from app.contracts.workflow import PortfolioRebalanceRequest
from app.main import app, create_app
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import InMemoryWorkflowStore, default_portfolios
from app.services.orchestrator import Orchestrator
from app.services.policy import evaluate_policy
from app.services.portfolio import calculate_asset_allocation, calculate_drift
from app.services.proposal import generate_execution_proposal
from tests.test_rebalance import request_payload


@pytest.fixture
def test_setup():
    store = InMemoryWorkflowStore()
    app.dependency_overrides[get_workflow_store] = lambda: store
    client = TestClient(app)
    req = PortfolioRebalanceRequest.model_validate(request_payload())
    yield client, store, req
    app.dependency_overrides.clear()


def test_tools_get_portfolio_parity(test_setup):
    client, store, req = test_setup
    account_id = req.account_profile.account_id

    direct_portfolio = store.get_portfolio(account_id)
    assert direct_portfolio is not None

    response = client.post("/tools/get_portfolio", json={"account_id": account_id})
    assert response.status_code == 200

    api_portfolio = PortfolioRecord.model_validate(response.json())
    assert api_portfolio == direct_portfolio

    # Missing account returns 404
    missing_resp = client.post("/tools/get_portfolio", json={"account_id": "nonexistent"})
    assert missing_resp.status_code == 404


def test_tools_compute_drift_parity(test_setup):
    client, store, req = test_setup
    snapshot = req.portfolio_snapshot
    target = req.allocation_target

    direct_alloc = calculate_asset_allocation(snapshot)
    direct_drift = calculate_drift(snapshot, target)

    response = client.post(
        "/tools/compute_drift",
        json={
            "portfolio_snapshot": snapshot.model_dump(mode="json"),
            "allocation_target": target.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200
    data = response.json()

    # Compare allocations and drift
    api_alloc = {k: Decimal(str(v)) for k, v in data["current_allocation"].items()}
    assert api_alloc == direct_alloc
    assert len(data["drift"]) == len(direct_drift)
    for api_d, direct_d in zip(data["drift"], direct_drift):
        assert api_d["key"] == direct_d.key
        assert Decimal(str(api_d["drift_pct"])) == direct_d.drift_pct
        assert api_d["within_tolerance"] == direct_d.within_tolerance


def test_tools_evaluate_policy_parity(test_setup):
    client, store, req = test_setup
    snapshot = req.portfolio_snapshot
    target = req.allocation_target
    risk_profile = req.risk_profile

    drift = calculate_drift(snapshot, target)
    direct_policy = evaluate_policy(snapshot, drift, risk_profile)

    response = client.post(
        "/tools/evaluate_policy",
        json={
            "portfolio_snapshot": snapshot.model_dump(mode="json"),
            "drift": [d.model_dump(mode="json") for d in drift],
            "risk_profile": risk_profile.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200
    api_policy = RiskPolicyResponse.model_validate(response.json())

    assert api_policy.verdict == direct_policy.verdict
    assert len(api_policy.rule_results) == len(direct_policy.rule_results)


def test_tools_generate_proposal_parity(test_setup):
    client, store, req = test_setup
    snapshot = req.portfolio_snapshot
    target = req.allocation_target
    risk_profile = req.risk_profile

    drift = calculate_drift(snapshot, target)
    policy = evaluate_policy(snapshot, drift, risk_profile)
    direct_proposal = generate_execution_proposal(snapshot, policy)

    response = client.post(
        "/tools/generate_proposal",
        json={
            "portfolio_snapshot": snapshot.model_dump(mode="json"),
            "risk_policy": policy.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200
    api_proposal = ExecutionProposalResponse.model_validate(response.json())

    assert api_proposal.proposal_status == direct_proposal.proposal_status
    assert len(api_proposal.trades) == len(direct_proposal.trades)
    for api_t, dir_t in zip(api_proposal.trades, direct_proposal.trades):
        assert api_t.symbol == dir_t.symbol
        assert api_t.action == dir_t.action
        assert api_t.estimated_value == dir_t.estimated_value


@pytest.mark.asyncio
async def test_tools_persist_proposal_parity(test_setup):
    client, store, req = test_setup

    orch = Orchestrator(store)
    orch_res = await orch.run(req)
    artifact = orch_res.approval_artifact
    artifact.approval_id = "apr_tools_test"

    response = client.post(
        "/tools/persist_proposal",
        json={"approval_artifact": artifact.model_dump(mode="json")},
    )
    assert response.status_code == 200
    persisted = ApprovalArtifact.model_validate(response.json())
    assert persisted.approval_id == "apr_tools_test"

    stored = store.get_approval("apr_tools_test")
    assert stored is not None
    assert stored.approval_id == "apr_tools_test"


def test_tools_audit_parity(test_setup):
    client, store, req = test_setup

    correlation = req.correlation
    response = client.post(
        "/tools/audit",
        json={
            "event_type": "TOOLS_TEST_EVENT",
            "correlation": correlation.model_dump(mode="json"),
            "outcome": "ACCEPTED",
            "actor_id": "test_actor",
            "details": {"key": "value"},
        },
    )
    assert response.status_code == 200
    event = AuditEvent.model_validate(response.json())
    assert event.event_type == "TOOLS_TEST_EVENT"
    assert event.outcome == "ACCEPTED"
    assert event.details == {"key": "value"}

    events = store.list_audit_events()
    assert any(e.event_id == event.event_id for e in events)


def test_app_role_tools_isolation():
    with patch.dict(os.environ, {"APP_ROLE": "tools"}):
        tools_app = create_app()
        tools_client = TestClient(tools_app)

        # Health is mounted
        health_resp = tools_client.get("/health")
        assert health_resp.status_code == 200

        # Tools are mounted
        drift_resp = tools_client.post("/tools/compute_drift", json={})
        # 422 Unprocessable Entity means the endpoint exists and validated input schema
        assert drift_resp.status_code == 422

        # Rebalance API route is NOT mounted
        rebal_resp = tools_client.post("/api/rebalance", json={})
        assert rebal_resp.status_code == 404
