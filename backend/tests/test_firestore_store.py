"""Tests for FirestoreWorkflowStore and persistence dependencies."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from google.cloud import firestore

from app.contracts.analysis import (
    ApprovalAction,
    ApprovalActionRequest,
    ApprovalArtifact,
    ApprovalTransitionResult,
    AuditEvent,
)
from app.contracts.common import ActorContext, CorrelationMetadata
from app.contracts.domain import (
    AccountProfile,
    AllocationTarget,
    ClientProfile,
    PortfolioHolding,
    PortfolioRecord,
    PortfolioSnapshot,
    RiskProfile,
)
from app.core.config import Settings
from app.persistence.dependencies import get_workflow_store
from app.persistence.firestore_store import FirestoreWorkflowStore
from app.persistence.memory_store import InMemoryWorkflowStore, WorkflowStore


def create_sample_portfolio(account_id: str = "acct_test") -> PortfolioRecord:
    from app.persistence.memory_store import default_portfolios

    portfolio = default_portfolios()[0]
    account_profile = portfolio.account_profile.model_copy(update={"account_id": account_id})
    snapshot = portfolio.portfolio_snapshot.model_copy(update={"account_id": account_id})
    return portfolio.model_copy(update={"account_profile": account_profile, "portfolio_snapshot": snapshot})


def create_sample_approval(approval_id: str = "apr_test") -> ApprovalArtifact:
    import asyncio
    from app.contracts.workflow import PortfolioRebalanceRequest
    from app.services.orchestrator import Orchestrator
    from tests.test_rebalance import request_payload

    req = PortfolioRebalanceRequest.model_validate(request_payload())
    orch = Orchestrator(InMemoryWorkflowStore())
    res = asyncio.run(orch.run(req))
    approval = res.approval_artifact
    return approval.model_copy(update={"approval_id": approval_id})


def test_firestore_workflow_store_protocol_conformance():
    """Verify FirestoreWorkflowStore conforms to WorkflowStore Protocol."""
    assert issubclass(FirestoreWorkflowStore, WorkflowStore)


def test_firestore_store_crud_mocked():
    """Verify save, get, and list operations using mocked Firestore client."""
    mock_client = MagicMock(spec=firestore.Client)
    mock_portfolios_col = MagicMock()
    mock_approvals_col = MagicMock()
    mock_audit_col = MagicMock()

    def get_collection(name: str):
        if "portfolio" in name:
            return mock_portfolios_col
        elif "approval" in name:
            return mock_approvals_col
        return mock_audit_col

    mock_client.collection.side_effect = get_collection

    settings = Settings(
        persistence_mode="firestore",
        seed_default_portfolios=False,
    )
    store = FirestoreWorkflowStore(settings=settings, client=mock_client)

    # 1. Test save_portfolio & get_portfolio
    portfolio = create_sample_portfolio("acct_mock_1")
    store.save_portfolio(portfolio)
    mock_portfolios_col.document.assert_called_with("acct_mock_1")
    doc_ref = mock_portfolios_col.document("acct_mock_1")
    doc_ref.set.assert_called()

    # Mock get_portfolio
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {"portfolio_json": portfolio.model_dump_json()}
    doc_ref.get.return_value = mock_doc

    fetched = store.get_portfolio("acct_mock_1")
    assert fetched is not None
    assert fetched.account_profile.account_id == "acct_mock_1"

    # 2. Test save_approval & get_approval
    approval = create_sample_approval("apr_mock_1")
    store.save_approval(approval)
    mock_approvals_col.document.assert_called_with("apr_mock_1")
    appr_doc_ref = mock_approvals_col.document("apr_mock_1")
    appr_doc_ref.set.assert_called()

    mock_appr_doc = MagicMock()
    mock_appr_doc.exists = True
    mock_appr_doc.to_dict.return_value = {"artifact_json": approval.model_dump_json()}
    appr_doc_ref.get.return_value = mock_appr_doc

    fetched_appr = store.get_approval("apr_mock_1")
    assert fetched_appr is not None
    assert fetched_appr.approval_id == "apr_mock_1"

    # 3. Test list_portfolios and list_approvals
    mock_portfolios_col.stream.return_value = [mock_doc]
    portfolios_list = store.list_portfolios()
    assert len(portfolios_list) == 1

    mock_approvals_col.stream.return_value = [mock_appr_doc]
    approvals_list = store.list_approvals()
    assert len(approvals_list) == 1

    # 4. Test add_audit_event & list_audit_events
    event = store.add_audit_event(
        event_type="TEST_EVENT",
        correlation=approval.correlation,
        outcome="SUCCESS",
        actor_id="tester",
    )
    assert event.event_type == "TEST_EVENT"
    mock_audit_col.document.assert_called()


def test_firestore_update_approval_mocked():
    """Verify update_approval handles transitions and persists audit events."""
    mock_client = MagicMock(spec=firestore.Client)
    # Simulate direct mode (no transaction on mock)
    del mock_client.transaction

    mock_portfolios_col = MagicMock()
    mock_approvals_col = MagicMock()
    mock_audit_col = MagicMock()

    def get_collection(name: str):
        if "portfolio" in name:
            return mock_portfolios_col
        elif "approval" in name:
            return mock_approvals_col
        return mock_audit_col

    mock_client.collection.side_effect = get_collection

    settings = Settings(persistence_mode="firestore", seed_default_portfolios=False)
    store = FirestoreWorkflowStore(settings=settings, client=mock_client)

    approval = create_sample_approval("apr_tx_1")
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {"artifact_json": approval.model_dump_json()}
    mock_approvals_col.document("apr_tx_1").get.return_value = mock_doc

    action = ApprovalActionRequest(
        action=ApprovalAction.APPROVE,
        actor_id="test_pm",
        expected_recommendation_hash=approval.recommendation_hash,
    )
    result = store.update_approval("apr_tx_1", action)

    assert result.previous_status == "PENDING"
    assert result.next_status == "APPROVED"
    assert result.audit_event_id is not None


def test_firestore_update_approval_not_found():
    """Verify KeyError is raised when updating non-existent approval."""
    mock_client = MagicMock(spec=firestore.Client)
    del mock_client.transaction

    mock_approvals_col = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_approvals_col.document.return_value.get.return_value = mock_doc
    mock_client.collection.return_value = mock_approvals_col

    settings = Settings(persistence_mode="firestore", seed_default_portfolios=False)
    store = FirestoreWorkflowStore(settings=settings, client=mock_client)

    action = ApprovalActionRequest(
        action=ApprovalAction.APPROVE,
        actor_id="test_pm",
        expected_recommendation_hash="hash_test",
    )
    with pytest.raises(KeyError, match="Approval artifact not found"):
        store.update_approval("apr_missing", action)


def test_dependencies_returns_firestore_store():
    """Verify get_workflow_store returns FirestoreWorkflowStore when PERSISTENCE_MODE=firestore."""
    get_workflow_store.cache_clear()
    with patch("app.persistence.dependencies.get_settings") as mock_settings:
        mock_settings.return_value.persistence_mode = "firestore"
        mock_settings.return_value.firestore_project_id = "mybrightday-dev"
        mock_settings.return_value.firestore_database = "portfolio-rebalancer"
        mock_settings.return_value.seed_default_portfolios = False
        mock_settings.return_value.portfolios_collection = "portfolios"
        mock_settings.return_value.approvals_collection = "approvals"
        mock_settings.return_value.audit_events_collection = "audit_events"

        with patch("app.persistence.firestore_store.firestore.Client"):
            store = get_workflow_store()
            assert isinstance(store, FirestoreWorkflowStore)
    get_workflow_store.cache_clear()


@pytest.mark.skipif(
    not __import__("os").environ.get("RUN_GCP_TESTS"),
    reason="Set RUN_GCP_TESTS=1 to run live Firestore tests against GCP",
)
def test_firestore_live_integration():
    """Live verification against GCP Firestore in project mybrightday-dev."""
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

    test_acct = f"acct_live_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    test_apr = f"apr_live_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"

    try:
        # 1. Portfolio save & get
        p = create_sample_portfolio(test_acct)
        store.save_portfolio(p)
        fetched_p = store.get_portfolio(test_acct)
        assert fetched_p is not None
        assert fetched_p.account_profile.account_id == test_acct

        # 2. Approval save & get
        a = create_sample_approval(test_apr)
        store.save_approval(a)
        fetched_a = store.get_approval(test_apr)
        assert fetched_a is not None
        assert fetched_a.approval_id == test_apr

        # 3. Transactional update
        action = ApprovalActionRequest(
            action=ApprovalAction.APPROVE,
            actor_id="test_pm_live",
            expected_recommendation_hash=a.recommendation_hash,
        )
        res = store.update_approval(test_apr, action)
        assert res.next_status == "APPROVED"

        updated_a = store.get_approval(test_apr)
        assert updated_a is not None
        assert updated_a.approval_status == "APPROVED"

        # 4. Audit events
        events = store.list_audit_events()
        assert any(e.event_id == res.audit_event_id for e in events)

    finally:
        # Cleanup
        try:
            store.portfolios_collection.document(test_acct).delete()
            store.approvals_collection.document(test_apr).delete()
            if "res" in locals() and res.audit_event_id:
                store.audit_events_collection.document(res.audit_event_id).delete()
        except Exception:
            pass

