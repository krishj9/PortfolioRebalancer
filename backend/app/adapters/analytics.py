"""Analytics adapter for proposal events (BigQuery & In-Memory).

Provides best-effort, non-blocking telemetry and operational analytics
for proposal generation, approvals, and rejections.
"""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
import logging
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class BaseAnalyticsAdapter(ABC):
    """Abstract interface for proposal event analytics."""

    @abstractmethod
    def emit_proposal_event(
        self,
        event_ts: datetime,
        proposal_id: str,
        account_id: str,
        event_type: str,
        workflow_state: str,
        max_abs_drift_pct: float,
        trade_count: int,
        run_id: str | None = None,
    ) -> bool:
        """Emit a proposal lifecycle event. Returns True if emitted, False otherwise."""
        pass


class InMemoryAnalyticsAdapter(BaseAnalyticsAdapter):
    """In-memory analytics adapter for local execution and unit tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit_proposal_event(
        self,
        event_ts: datetime,
        proposal_id: str,
        account_id: str,
        event_type: str,
        workflow_state: str,
        max_abs_drift_pct: float,
        trade_count: int,
        run_id: str | None = None,
    ) -> bool:
        event = {
            "event_ts": event_ts.isoformat(),
            "proposal_id": proposal_id,
            "account_id": account_id,
            "event_type": event_type,
            "workflow_state": workflow_state,
            "max_abs_drift_pct": float(max_abs_drift_pct),
            "trade_count": int(trade_count),
            "run_id": run_id,
        }
        self.events.append(event)
        logger.debug("Recorded in-memory proposal event: %s", event)
        return True

    def clear(self) -> None:
        self.events.clear()


class BigQueryAnalyticsAdapter(BaseAnalyticsAdapter):
    """BigQuery analytics adapter using streaming row inserts."""

    def __init__(
        self,
        project_id: str | None = None,
        dataset_id: str | None = None,
        table_id: str | None = None,
        client: Any = None,
    ) -> None:
        settings = get_settings()
        self.project_id = project_id or settings.project_id
        self.dataset_id = dataset_id or settings.bigquery_dataset
        self.table_id = table_id or settings.bigquery_table
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from google.cloud import bigquery
                self._client = bigquery.Client(project=self.project_id)
            except Exception as exc:
                logger.warning("Could not initialize BigQuery client: %s", exc)
                return None
        return self._client

    @property
    def full_table_id(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{self.table_id}"

    def emit_proposal_event(
        self,
        event_ts: datetime,
        proposal_id: str,
        account_id: str,
        event_type: str,
        workflow_state: str,
        max_abs_drift_pct: float,
        trade_count: int,
        run_id: str | None = None,
    ) -> bool:
        if not self.client:
            logger.warning("BigQuery client not available; skipping analytics event")
            return False

        row = {
            "event_ts": event_ts.isoformat(),
            "proposal_id": proposal_id,
            "account_id": account_id,
            "event_type": event_type,
            "workflow_state": workflow_state,
            "max_abs_drift_pct": float(max_abs_drift_pct),
            "trade_count": int(trade_count),
            "run_id": run_id,
        }

        try:
            errors = self.client.insert_rows_json(self.full_table_id, [row])
            if errors:
                logger.warning("BigQuery insert errors: %s", errors)
                return False
            logger.info("Successfully emitted proposal event to BigQuery: %s (%s)", proposal_id, event_type)
            return True
        except Exception as exc:
            logger.warning("Failed to emit proposal event to BigQuery: %s", exc)
            return False


_global_analytics_adapter: BaseAnalyticsAdapter | None = None


def get_analytics_adapter() -> BaseAnalyticsAdapter:
    """Dependency provider for analytics adapter."""
    global _global_analytics_adapter
    if _global_analytics_adapter is not None:
        return _global_analytics_adapter

    settings = get_settings()
    if settings.analytics_mode == "bigquery":
        return BigQueryAnalyticsAdapter(
            project_id=settings.project_id,
            dataset_id=settings.bigquery_dataset,
            table_id=settings.bigquery_table,
        )
    return InMemoryAnalyticsAdapter()


def set_analytics_adapter(adapter: BaseAnalyticsAdapter | None) -> None:
    """Override the global analytics adapter (for testing)."""
    global _global_analytics_adapter
    _global_analytics_adapter = adapter
