import json
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.contracts.analysis import (
    ApprovalActionRequest,
    ApprovalArtifact,
    ApprovalTransitionResult,
    AuditEvent,
)
from app.contracts.common import CorrelationMetadata, new_id
from app.contracts.domain import PortfolioRecord
from app.core.config import Settings
from app.persistence.memory_store import InMemoryWorkflowStore, default_portfolios


class DynamoDBWorkflowStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.fallback = InMemoryWorkflowStore()
        resource_kwargs: dict[str, str] = {"region_name": settings.aws_region}
        if settings.dynamodb_endpoint_url:
            resource_kwargs["endpoint_url"] = settings.dynamodb_endpoint_url
            resource_kwargs["aws_access_key_id"] = "local"
            resource_kwargs["aws_secret_access_key"] = "local"
        self.dynamodb = boto3.resource("dynamodb", **resource_kwargs)
        self.approvals_table = self.dynamodb.Table(settings.approvals_table_name)
        self.audit_table = self.dynamodb.Table(settings.audit_events_table_name)
        self.portfolios_table = self.dynamodb.Table(settings.portfolios_table_name)
        if settings.persistence_mode == "local":
            self._ensure_local_tables()
        if settings.seed_default_portfolios:
            self._seed_portfolios_if_empty()

    def save_portfolio(self, portfolio: PortfolioRecord) -> PortfolioRecord:
        self.portfolios_table.put_item(
            Item={
                "account_id": portfolio.account_profile.account_id,
                "client_id": portfolio.client_profile.client_id,
                "display_label": portfolio.client_profile.display_label,
                "updated_at": portfolio.updated_at.isoformat(),
                "portfolio_json": portfolio.model_dump_json(),
            }
        )
        return portfolio

    def get_portfolio(self, account_id: str) -> PortfolioRecord | None:
        response = self.portfolios_table.get_item(Key={"account_id": account_id})
        item = response.get("Item")
        if not item:
            return None
        return PortfolioRecord.model_validate_json(item["portfolio_json"])

    def list_portfolios(self) -> list[PortfolioRecord]:
        response = self.portfolios_table.scan()
        return sorted(
            [
                PortfolioRecord.model_validate_json(item["portfolio_json"])
                for item in response.get("Items", [])
            ],
            key=lambda portfolio: portfolio.client_profile.display_label,
        )

    def save_approval(self, artifact: ApprovalArtifact) -> ApprovalArtifact:
        item = {
            "approval_id": artifact.approval_id,
            "request_id": artifact.correlation.request_id,
            "trace_id": artifact.correlation.trace_id,
            "approval_status": artifact.approval_status,
            "recommendation_hash": artifact.recommendation_hash,
            "artifact_json": artifact.model_dump_json(),
        }
        self.approvals_table.put_item(Item=item)
        return artifact

    def get_approval(self, approval_id: str) -> ApprovalArtifact | None:
        response = self.approvals_table.get_item(Key={"approval_id": approval_id})
        item = response.get("Item")
        if not item:
            return None
        return ApprovalArtifact.model_validate_json(item["artifact_json"])

    def list_approvals(self) -> list[ApprovalArtifact]:
        response = self.approvals_table.scan()
        return [
            ApprovalArtifact.model_validate_json(item["artifact_json"])
            for item in response.get("Items", [])
        ]

    def list_audit_events(self) -> list[AuditEvent]:
        response = self.audit_table.scan()
        return sorted(
            [
                AuditEvent.model_validate_json(item["event_json"])
                for item in response.get("Items", [])
            ],
            key=lambda e: e.created_at,
            reverse=True,
        )

    def update_approval(
        self, approval_id: str, action: ApprovalActionRequest
    ) -> ApprovalTransitionResult:
        approval = self.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"Approval artifact not found: {approval_id}")
        result = self.fallback.save_approval(approval)
        _ = result
        transition = self.fallback.update_approval(approval_id, action)
        updated = self.fallback.get_approval(approval_id)
        if updated is not None:
            self.save_approval(updated)
            event = self.add_audit_event(
                "APPROVAL_ACTION",
                updated.correlation,
                transition.next_status,
                action.actor_id,
                {"approval_id": approval_id, "action": action.action},
            )
            return transition.model_copy(update={"audit_event_id": event.event_id})
        return transition

    def add_audit_event(
        self,
        event_type: str,
        correlation: CorrelationMetadata,
        outcome: str,
        actor_id: str | None = None,
        details: dict[str, str] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=new_id("evt"),
            event_type=event_type,
            correlation=correlation,
            actor_id=actor_id,
            outcome=outcome,
            details=details or {},
            created_at=datetime.now(UTC),
        )
        self.audit_table.put_item(
            Item={
                "event_id": event.event_id,
                "request_id": event.correlation.request_id,
                "trace_id": event.correlation.trace_id,
                "event_type": event.event_type,
                "timestamp": event.created_at.isoformat(),
                "event_json": event.model_dump_json(),
            }
        )
        return event

    def _ensure_local_tables(self) -> None:
        existing = set(self.dynamodb.meta.client.list_tables().get("TableNames", []))
        self._create_table_if_missing(existing, self.settings.approvals_table_name, "approval_id")
        self._create_table_if_missing(existing, self.settings.audit_events_table_name, "event_id")
        self._create_table_if_missing(existing, self.settings.portfolios_table_name, "account_id")
        self._create_table_if_missing(existing, self.settings.sessions_table_name, "session_id")
        self._create_table_if_missing(existing, self.settings.memory_queue_table_name, "task_id")

    def _seed_portfolios_if_empty(self) -> None:
        if self.list_portfolios():
            return
        for portfolio in default_portfolios():
            self.save_portfolio(portfolio)

    def _create_table_if_missing(self, existing: set[str], table_name: str, hash_key: str) -> None:
        if table_name in existing:
            return
        try:
            table = self.dynamodb.create_table(
                TableName=table_name,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": hash_key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": hash_key, "AttributeType": "S"}],
            )
            table.wait_until_exists()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceInUseException":
                raise


def to_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
