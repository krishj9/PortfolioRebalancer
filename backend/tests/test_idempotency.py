"""Tests for idempotent proposal generation and persistence (Task P1-07, Defect D4)."""

import hashlib
import os
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists

from app.agents.human_approval import HumanApprovalWorkflowAgent
from app.contracts.analysis import (
    ApprovalArtifact,
    ExecutionProposalResponse,
    PolicyVerdictStatus,
    RecommendationPackage,
    RiskPolicyResponse,
)
from app.contracts.common import ActorContext, CorrelationMetadata, WorkflowState
from app.contracts.workflow import PortfolioRebalanceRequest
from app.core.config import Settings
from app.main import app
from app.persistence.dependencies import get_workflow_store
from app.persistence.firestore_store import FirestoreWorkflowStore
from app.persistence.memory_store import InMemoryWorkflowStore
from app.services.orchestrator import Orchestrator
from tests.test_rebalance import request_payload


def make_recommendation() -> RecommendationPackage:
    return RecommendationPackage(
        summary="Test summary",
        agent_stages=[],
        current_allocation={},
        target_allocation={},
        proposed_allocation={},
        approval_eligibility=True,
        risk_policy=RiskPolicyResponse(verdict=PolicyVerdictStatus.COMPLIANT, drift=[]),
        proposal=ExecutionProposalResponse(proposal_status="READY_FOR_REVIEW"),
        workflow_state=WorkflowState.NORMAL,
    )


@pytest.fixture
def rebalance_request() -> PortfolioRebalanceRequest:
    return PortfolioRebalanceRequest.model_validate(request_payload())


def test_approval_id_deterministic_derivation(rebalance_request: PortfolioRebalanceRequest):
    """Verify that approval_id is derived deterministically from account_id and idempotency_key."""
    agent = HumanApprovalWorkflowAgent()
    recommendation = make_recommendation()

    account_id = rebalance_request.account_profile.account_id
    idempotency_key = "idem_test_key_123"
    rebalance_request.correlation.idempotency_key = idempotency_key

    expected_hash = hashlib.sha256((account_id + idempotency_key).encode("utf-8")).hexdigest()[:20]
    expected_id = f"apr_{expected_hash}"

    # First run
    _, artifact1 = pytest.importorskip("asyncio").run(
        agent.run(rebalance_request.correlation, recommendation, rebalance_request)
    )
    assert artifact1.approval_id == expected_id

    # Second run with same correlation and request
    _, artifact2 = pytest.importorskip("asyncio").run(
        agent.run(rebalance_request.correlation, recommendation, rebalance_request)
    )
    assert artifact2.approval_id == expected_id
    assert artifact1.approval_id == artifact2.approval_id

    # Different key produces different ID
    rebalance_request.correlation.idempotency_key = "different_key"
    _, artifact3 = pytest.importorskip("asyncio").run(
        agent.run(rebalance_request.correlation, recommendation, rebalance_request)
    )
    assert artifact3.approval_id != expected_id


def test_in_memory_store_idempotent_save(rebalance_request: PortfolioRebalanceRequest):
    """Verify that InMemoryWorkflowStore.save_approval is idempotent."""
    store = InMemoryWorkflowStore()
    agent = HumanApprovalWorkflowAgent()
    recommendation = make_recommendation()
    rebalance_request.correlation.idempotency_key = "idem_mem_test"
    _, artifact = pytest.importorskip("asyncio").run(
        agent.run(rebalance_request.correlation, recommendation, rebalance_request)
    )

    # Save once
    saved1 = store.save_approval(artifact)
    assert len(store.approvals) == 1

    # Save twice
    saved2 = store.save_approval(artifact)
    assert len(store.approvals) == 1
    assert saved1.approval_id == saved2.approval_id


def make_sample_artifact(approval_id: str, req: PortfolioRebalanceRequest) -> ApprovalArtifact:
    return ApprovalArtifact(
        approval_id=approval_id,
        correlation=req.correlation,
        recommendation_hash="rec_hash_1",
        recommendation=make_recommendation(),
        client_profile=req.client_profile,
        account_profile=req.account_profile,
        original_portfolio_snapshot=req.portfolio_snapshot,
        allocation_target=req.allocation_target,
        risk_profile=req.risk_profile,
    )


def test_firestore_store_idempotent_save_mock(rebalance_request: PortfolioRebalanceRequest):
    """Verify that FirestoreWorkflowStore uses create() and returns existing artifact on AlreadyExists."""
    mock_client = MagicMock()
    mock_col = MagicMock()
    mock_doc = MagicMock()
    mock_client.collection.return_value = mock_col
    mock_col.document.return_value = mock_doc

    settings = Settings(persistence_mode="firestore", seed_default_portfolios=False)
    store = FirestoreWorkflowStore(settings=settings, client=mock_client)

    artifact = make_sample_artifact("apr_idem_mock", rebalance_request)

    # Simulate AlreadyExists on create()
    mock_doc.create.side_effect = AlreadyExists("Document already exists")
    mock_existing_doc = MagicMock()
    mock_existing_doc.exists = True
    mock_existing_doc.to_dict.return_value = {"artifact_json": artifact.model_dump_json()}
    mock_doc.get.return_value = mock_existing_doc

    result = store.save_approval(artifact)
    assert result.approval_id == "apr_idem_mock"
    mock_doc.create.assert_called_once()
    mock_doc.get.assert_called_once()


def test_rebalance_endpoint_idempotency_key_header():
    """Verify that two identical POST /api/rebalance requests with Idempotency-Key return the same approval_id."""
    store = InMemoryWorkflowStore()
    app.dependency_overrides[get_workflow_store] = lambda: store
    client = TestClient(app)

    payload = request_payload()
    headers = {"Idempotency-Key": "client-idempotency-key-42"}

    # First request
    resp1 = client.post("/api/rebalance", json=payload, headers=headers)
    assert resp1.status_code == 200
    data1 = resp1.json()
    approval_id1 = data1["approval_artifact"]["approval_id"]

    # Second request with same idempotency key
    resp2 = client.post("/api/rebalance", json=payload, headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    approval_id2 = data2["approval_artifact"]["approval_id"]

    assert approval_id1 == approval_id2
    # Only 1 approval in store
    assert len(store.approvals) == 1

    app.dependency_overrides.clear()


@pytest.mark.skipif(
    not os.environ.get("RUN_GCP_TESTS"),
    reason="Set RUN_GCP_TESTS=1 to run live Firestore idempotency test against GCP",
)
def test_firestore_live_idempotency(rebalance_request: PortfolioRebalanceRequest):
    """Live verification that two identical save_approval calls against GCP Firestore produce 1 document."""
    settings = Settings(
        persistence_mode="firestore",
        firestore_project_id="mybrightday-dev",
        firestore_database="portfolio-rebalancer",
        seed_default_portfolios=False,
        portfolios_collection="portfolios_test",
        approvals_collection="approvals_test",
        audit_events_collection="audit_events_test",
    )
    store = FirestoreWorkflowStore(settings=settings)

    idem_key = f"live_idem_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    acct_id = rebalance_request.account_profile.account_id
    approval_id = "apr_" + hashlib.sha256((acct_id + idem_key).encode("utf-8")).hexdigest()[:20]

    rebalance_request.correlation.idempotency_key = idem_key
    artifact = make_sample_artifact(approval_id, rebalance_request)

    try:
        # 1. First save creates the document
        res1 = store.save_approval(artifact)
        assert res1.approval_id == approval_id

        # 2. Second save catches AlreadyExists and returns the stored document
        res2 = store.save_approval(artifact)
        assert res2.approval_id == approval_id

        # 3. Document exists and is exactly one
        doc = store.approvals_collection.document(approval_id).get()
        assert doc.exists

    finally:
        try:
            store.approvals_collection.document(approval_id).delete()
        except Exception:
            pass
