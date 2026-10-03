"""Tests for actor extraction from IAP and resource authorization (Task P3-06)."""

import pytest
from fastapi.testclient import TestClient

from app.contracts.analysis import ApprovalAction, ApprovalActionRequest
from app.contracts.common import ActorContext
from app.contracts.workflow import PortfolioRebalanceRequest
from app.core.config import Settings
from app.main import create_app
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import InMemoryWorkflowStore
from tests.test_rebalance import request_payload


@pytest.fixture
def store():
    return InMemoryWorkflowStore()


def test_auth_mode_none_local_owner_access(store):
    """In AUTH_MODE=none, requests without headers default to local_owner and succeed."""
    app = create_app()
    app.dependency_overrides[get_workflow_store] = lambda: store

    client = TestClient(app)
    response = client.get("/portfolios/acct_demo")
    assert response.status_code == 200
    assert response.json()["account_profile"]["account_id"] == "acct_demo"


def test_iap_auth_mode_missing_header_rejected(store):
    """When AUTH_MODE=iap, requests missing IAP header receive 401 Unauthorized."""
    from app.core.config import get_settings
    app = create_app()
    app.dependency_overrides[get_workflow_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: Settings(auth_mode="iap")

    client = TestClient(app)
    response = client.get("/portfolios/acct_demo")
    assert response.status_code == 401
    assert "missing IAP identity header" in response.json()["detail"]


def test_iap_header_parsing_and_authorized_access(store, monkeypatch):
    """Valid IAP email header is parsed and owner can access their own portfolio."""
    monkeypatch.setenv("AUTH_MODE", "iap")
    app = create_app()
    app.dependency_overrides[get_workflow_store] = lambda: store

    client = TestClient(app)
    headers = {"x-goog-authenticated-user-email": "accounts.google.com:income-investor@example.com"}

    # Owner accesses income account
    response = client.get("/portfolios/acct_income", headers=headers)
    assert response.status_code == 200
    assert response.json()["account_profile"]["account_id"] == "acct_income"


def test_unauthorized_user_forbidden_403(store, monkeypatch):
    """Actor attempting to access another user's account receives 403 Forbidden."""
    monkeypatch.setenv("AUTH_MODE", "iap")
    app = create_app()
    app.dependency_overrides[get_workflow_store] = lambda: store

    client = TestClient(app)
    headers = {"x-goog-authenticated-user-email": "accounts.google.com:attacker@example.com"}

    # Attacker tries to read income portfolio
    response = client.get("/portfolios/acct_income", headers=headers)
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"].lower()


def test_rebalance_endpoint_enforces_resource_authorization(store, monkeypatch):
    """Rebalance request for another user's portfolio is rejected with 403 Forbidden."""
    monkeypatch.setenv("AUTH_MODE", "iap")
    app = create_app()
    app.dependency_overrides[get_workflow_store] = lambda: store

    client = TestClient(app)
    headers = {"x-goog-authenticated-user-email": "accounts.google.com:unauthorized@example.com"}

    payload = request_payload()
    # Target acct_income owned by income-investor@example.com
    payload["account_profile"]["account_id"] = "acct_income"
    payload["client_profile"]["client_id"] = "client_income"

    response = client.post("/rebalance", json=payload, headers=headers)
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"].lower()


def test_preferences_endpoint_enforces_resource_authorization(store, monkeypatch):
    """Preferences get/put for another client is rejected with 403 Forbidden."""
    monkeypatch.setenv("AUTH_MODE", "iap")
    app = create_app()
    app.dependency_overrides[get_workflow_store] = lambda: store

    client = TestClient(app)
    headers = {"x-goog-authenticated-user-email": "accounts.google.com:unauthorized@example.com"}

    response = client.get("/preferences/client_income", headers=headers)
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"].lower()
