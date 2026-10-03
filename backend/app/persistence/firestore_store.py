import json
import logging
from datetime import UTC, datetime
from typing import Any, Optional

from google.cloud import firestore

from app.contracts.analysis import (
    ApprovalActionRequest,
    ApprovalArtifact,
    ApprovalTransitionResult,
    AuditEvent,
)
from app.contracts.common import CorrelationMetadata, new_id
from app.contracts.domain import PortfolioRecord
from app.core.config import Settings, get_settings
from app.persistence.dynamodb_store import to_jsonable
from app.persistence.memory_store import InMemoryWorkflowStore, default_portfolios

logger = logging.getLogger(__name__)


class FirestoreWorkflowStore:
    """Production-ready Firestore implementation of WorkflowStore."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[firestore.Client] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.fallback = InMemoryWorkflowStore()

        self.db = client or firestore.Client(
            project=getattr(self.settings, "firestore_project_id", "mybrightday-dev"),
            database=getattr(self.settings, "firestore_database", "portfolio-rebalancer"),
        )
        self.portfolios_collection = self.db.collection(
            getattr(self.settings, "portfolios_collection", "portfolios")
        )
        self.approvals_collection = self.db.collection(
            getattr(self.settings, "approvals_collection", "approvals")
        )
        self.audit_events_collection = self.db.collection(
            getattr(self.settings, "audit_events_collection", "audit_events")
        )

        if self.settings.seed_default_portfolios:
            self._seed_portfolios_if_empty()

    def save_portfolio(self, portfolio: PortfolioRecord) -> PortfolioRecord:
        """Persist or update portfolio record in Firestore."""
        account_id = portfolio.account_profile.account_id
        data = {
            "account_id": account_id,
            "client_id": portfolio.client_profile.client_id,
            "display_label": portfolio.client_profile.display_label,
            "updated_at": portfolio.updated_at.isoformat(),
            "portfolio_json": portfolio.model_dump_json(),
        }
        self.portfolios_collection.document(account_id).set(data)
        return portfolio

    def get_portfolio(self, account_id: str) -> PortfolioRecord | None:
        """Retrieve portfolio record by account ID."""
        doc = self.portfolios_collection.document(account_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        if not data or "portfolio_json" not in data:
            return None
        return PortfolioRecord.model_validate_json(data["portfolio_json"])

    def list_portfolios(self) -> list[PortfolioRecord]:
        """List all portfolios sorted by client display label."""
        docs = self.portfolios_collection.stream()
        portfolios = []
        for doc in docs:
            data = doc.to_dict()
            if data and "portfolio_json" in data:
                portfolios.append(PortfolioRecord.model_validate_json(data["portfolio_json"]))
        return sorted(portfolios, key=lambda p: p.client_profile.display_label)

    def save_approval(self, artifact: ApprovalArtifact) -> ApprovalArtifact:
        """Persist or return existing approval artifact in Firestore idempotently."""
        doc_ref = self.approvals_collection.document(artifact.approval_id)
        data = {
            "approval_id": artifact.approval_id,
            "request_id": artifact.correlation.request_id,
            "trace_id": artifact.correlation.trace_id,
            "approval_status": artifact.approval_status,
            "recommendation_hash": artifact.recommendation_hash,
            "artifact_json": artifact.model_dump_json(),
        }
        try:
            if hasattr(doc_ref, "create"):
                doc_ref.create(data)
            else:
                existing = self.get_approval(artifact.approval_id)
                if existing:
                    return existing
                doc_ref.set(data)
            return artifact
        except Exception as e:
            from google.api_core.exceptions import AlreadyExists

            if (
                isinstance(e, AlreadyExists)
                or "AlreadyExists" in type(e).__name__
                or "already exists" in str(e).lower()
                or getattr(e, "code", None) == 409
            ):
                existing = self.get_approval(artifact.approval_id)
                if existing:
                    return existing
            raise

    def get_approval(self, approval_id: str) -> ApprovalArtifact | None:
        """Retrieve approval artifact by approval ID."""
        doc = self.approvals_collection.document(approval_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        if not data or "artifact_json" not in data:
            return None
        return ApprovalArtifact.model_validate_json(data["artifact_json"])

    def list_approvals(self) -> list[ApprovalArtifact]:
        """List all approval artifacts."""
        docs = self.approvals_collection.stream()
        approvals = []
        for doc in docs:
            data = doc.to_dict()
            if data and "artifact_json" in data:
                approvals.append(ApprovalArtifact.model_validate_json(data["artifact_json"]))
        return approvals

    def list_audit_events(self) -> list[AuditEvent]:
        """List all audit events sorted by timestamp descending."""
        docs = self.audit_events_collection.stream()
        events = []
        for doc in docs:
            data = doc.to_dict()
            if data and "event_json" in data:
                events.append(AuditEvent.model_validate_json(data["event_json"]))
        return sorted(events, key=lambda e: e.created_at, reverse=True)

    def add_audit_event(
        self,
        event_type: str,
        correlation: CorrelationMetadata,
        outcome: str,
        actor_id: str | None = None,
        details: dict[str, str] | None = None,
    ) -> AuditEvent:
        """Append an immutable audit event record."""
        event = AuditEvent(
            event_id=new_id("evt"),
            event_type=event_type,
            correlation=correlation,
            actor_id=actor_id,
            outcome=outcome,
            details=details or {},
            created_at=datetime.now(UTC),
        )
        data = {
            "event_id": event.event_id,
            "request_id": event.correlation.request_id,
            "trace_id": event.correlation.trace_id,
            "event_type": event.event_type,
            "timestamp": event.created_at.isoformat(),
            "event_json": event.model_dump_json(),
        }
        self.audit_events_collection.document(event.event_id).set(data)
        return event

    def update_approval(
        self, approval_id: str, action: ApprovalActionRequest
    ) -> ApprovalTransitionResult:
        """
        Update approval state in a Firestore transaction.
        Transitions state, updates approval artifact, and appends an audit event.
        """
        approval_ref = self.approvals_collection.document(approval_id)
        audit_event_id = new_id("evt")
        audit_ref = self.audit_events_collection.document(audit_event_id)

        # If transaction method exists on client, execute in transaction
        if hasattr(self.db, "transaction"):
            transaction = self.db.transaction()
            return self._update_in_transaction(
                transaction, approval_ref, audit_ref, approval_id, action, audit_event_id
            )
        else:
            # Fallback for simple mocks without transaction support
            return self._update_direct(
                approval_ref, audit_ref, approval_id, action, audit_event_id
            )

    def _update_in_transaction(
        self,
        transaction: firestore.Transaction,
        approval_ref: Any,
        audit_ref: Any,
        approval_id: str,
        action: ApprovalActionRequest,
        audit_event_id: str,
    ) -> ApprovalTransitionResult:
        @firestore.transactional
        def _exec_tx(tx: firestore.Transaction) -> ApprovalTransitionResult:
            doc = approval_ref.get(transaction=tx)
            if not doc.exists:
                raise KeyError(f"Approval artifact not found: {approval_id}")

            data = doc.to_dict()
            if not data or "artifact_json" not in data:
                raise KeyError(f"Approval artifact corrupted: {approval_id}")

            approval = ApprovalArtifact.model_validate_json(data["artifact_json"])

            # Compute transition using domain logic
            self.fallback.save_approval(approval)
            transition = self.fallback.update_approval(approval_id, action)
            updated = self.fallback.get_approval(approval_id)
            if updated is None:
                return transition

            event = AuditEvent(
                event_id=audit_event_id,
                event_type="APPROVAL_ACTION",
                correlation=updated.correlation,
                outcome=transition.next_status,
                actor_id=action.actor_id,
                details={"approval_id": approval_id, "action": action.action},
                created_at=datetime.now(UTC),
            )

            # Persist in transaction
            tx.set(
                approval_ref,
                {
                    "approval_id": updated.approval_id,
                    "request_id": updated.correlation.request_id,
                    "trace_id": updated.correlation.trace_id,
                    "approval_status": updated.approval_status,
                    "recommendation_hash": updated.recommendation_hash,
                    "artifact_json": updated.model_dump_json(),
                },
            )
            tx.set(
                audit_ref,
                {
                    "event_id": event.event_id,
                    "request_id": event.correlation.request_id,
                    "trace_id": event.correlation.trace_id,
                    "event_type": event.event_type,
                    "timestamp": event.created_at.isoformat(),
                    "event_json": event.model_dump_json(),
                },
            )
            return transition.model_copy(update={"audit_event_id": event.event_id})

        return _exec_tx(transaction)

    def _update_direct(
        self,
        approval_ref: Any,
        audit_ref: Any,
        approval_id: str,
        action: ApprovalActionRequest,
        audit_event_id: str,
    ) -> ApprovalTransitionResult:
        doc = approval_ref.get()
        if not doc.exists:
            raise KeyError(f"Approval artifact not found: {approval_id}")

        data = doc.to_dict()
        if not data or "artifact_json" not in data:
            raise KeyError(f"Approval artifact corrupted: {approval_id}")

        approval = ApprovalArtifact.model_validate_json(data["artifact_json"])
        self.fallback.save_approval(approval)
        transition = self.fallback.update_approval(approval_id, action)
        updated = self.fallback.get_approval(approval_id)
        if updated is None:
            return transition

        event = AuditEvent(
            event_id=audit_event_id,
            event_type="APPROVAL_ACTION",
            correlation=updated.correlation,
            outcome=transition.next_status,
            actor_id=action.actor_id,
            details={"approval_id": approval_id, "action": action.action},
            created_at=datetime.now(UTC),
        )
        approval_ref.set(
            {
                "approval_id": updated.approval_id,
                "request_id": updated.correlation.request_id,
                "trace_id": updated.correlation.trace_id,
                "approval_status": updated.approval_status,
                "recommendation_hash": updated.recommendation_hash,
                "artifact_json": updated.model_dump_json(),
            }
        )
        audit_ref.set(
            {
                "event_id": event.event_id,
                "request_id": event.correlation.request_id,
                "trace_id": event.correlation.trace_id,
                "event_type": event.event_type,
                "timestamp": event.created_at.isoformat(),
                "event_json": event.model_dump_json(),
            }
        )
        return transition.model_copy(update={"audit_event_id": event.event_id})

    def _seed_portfolios_if_empty(self) -> None:
        """Seed default sample portfolios if the collection is empty."""
        try:
            if self.list_portfolios():
                return
            for portfolio in default_portfolios():
                self.save_portfolio(portfolio)
        except Exception as e:
            logger.warning(f"Could not seed default portfolios into Firestore: {e}")
