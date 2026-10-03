"""Vertex AI Agent Runtime client for executing portfolio rebalancing workflows (Task P1-09)."""

import asyncio
import logging
from pathlib import Path
import uuid
from typing import Any

from fastapi import HTTPException

from app.contracts.common import ErrorSeverity, StructuredError
from app.contracts.workflow import OrchestrationResponse, PortfolioRebalanceRequest
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class RuntimeClient:
    """Client for delegating workflow execution to Vertex AI Agent Runtime."""

    def __init__(
        self,
        project: str | None = None,
        location: str | None = None,
        resource_name: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.project = project or self.settings.firestore_project_id or "mybrightday-dev"
        self.location = location or self.settings.llm_config.gemini_location or "us-central1"
        self.resource_name = resource_name or self.settings.agent_runtime_resource_name

        if not self.resource_name:
            # Fallback to local deployed_runtime.txt if present
            deployed_file = (
                Path(__file__).resolve().parent.parent
                / "agent_runtime"
                / "deployed_runtime.txt"
            )
            if deployed_file.exists():
                self.resource_name = deployed_file.read_text().strip()

        self._client = None
        self._runtime = None

    def _get_runtime(self) -> Any:
        if self._runtime is None:
            if not self.resource_name:
                raise ValueError("AGENT_RUNTIME_RESOURCE_NAME is not configured or found")
            import agentplatform

            logger.info(
                f"Initializing agentplatform.Client for project={self.project}, location={self.location}"
            )
            self._client = agentplatform.Client(project=self.project, location=self.location)
            self._runtime = self._client.runtimes.get(name=self.resource_name)
        return self._runtime

    def _execute_sync(self, request_payload: dict, run_id: str, session_id: str | None) -> dict:
        from app.adapters.telemetry import get_current_trace_context

        runtime = self._get_runtime()
        kwargs: dict[str, Any] = {
            "request": request_payload,
            "run_id": run_id,
        }
        if session_id:
            kwargs["session_id"] = session_id

        trace_ctx = get_current_trace_context()
        if trace_ctx.get("traceparent"):
            kwargs["traceparent"] = trace_ctx["traceparent"]

        return runtime.query(**kwargs)

    async def run(
        self,
        request: PortfolioRebalanceRequest,
        session_id: str | None = None,
    ) -> OrchestrationResponse:
        """Execute portfolio rebalancing request on Vertex AI Agent Runtime."""
        from app.adapters.telemetry import telemetry_span

        run_id = f"run_{uuid.uuid4().hex[:16]}"
        request_payload = request.model_dump(mode="json")

        try:
            with telemetry_span(
                "agent_runtime.client_call",
                attributes={
                    "run_id": run_id,
                    "session_id": session_id,
                    "resource_name": self.resource_name,
                },
                run_id=run_id,
            ):
                raw_response = await asyncio.to_thread(
                    self._execute_sync, request_payload, run_id, session_id
                )
                return OrchestrationResponse.model_validate(raw_response)
        except Exception as exc:
            logger.error(
                f"Agent Runtime execution failed for run_id={run_id}, resource={self.resource_name}: {exc}",
                exc_info=True,
            )
            structured_error = StructuredError(
                code="AGENT_RUNTIME_EXECUTION_FAILED",
                message=f"Agent Runtime failed to process rebalancing request: {exc}",
                severity=ErrorSeverity.ERROR,
                retryable=True,
                source="agent_runtime",
                correlation=request.correlation,
                details={
                    "run_id": run_id,
                    "resource_name": self.resource_name,
                    "retry_hint": "This operation is retryable. Please retry with the same Idempotency-Key.",
                },
            )
            raise HTTPException(
                status_code=502,
                detail=structured_error.model_dump(mode="json"),
            ) from exc
