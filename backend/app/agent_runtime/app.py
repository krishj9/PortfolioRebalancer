"""Vertex AI Agent Runtime application for Portfolio Rebalancer."""

import asyncio
import concurrent.futures
import logging
import os
from typing import Any, Optional

from app.contracts.common import WorkflowState
from app.contracts.workflow import OrchestrationResponse, PortfolioRebalanceRequest
from app.core.config import Settings
from app.services.langgraph_graph import LangGraphOrchestrator
from app.tools.client import ToolClient

logger = logging.getLogger(__name__)


class RebalanceGraphApp:
    """Agent Runtime custom agent wrapper hosting the LangGraph rebalance workflow."""

    def __init__(
        self,
        project: str = "mybrightday-dev",
        location: str = "us-central1",
        tool_mode: str = "remote",
        tools_url: str = "http://localhost:8000",
        llm_provider: str = "gemini",
        gemini_model_id: str = "gemini-2.5-flash",
        sessions_mode: str = "local",
        memory_generation_enabled: bool | None = None,
        client: Optional[Any] = None,
    ) -> None:
        self.project = project
        self.location = location
        self.tool_mode = tool_mode
        self.tools_url = tools_url
        self.llm_provider = llm_provider
        self.gemini_model_id = gemini_model_id
        self.sessions_mode = sessions_mode
        self.memory_generation_enabled = (
            (sessions_mode == "agent_platform")
            if memory_generation_enabled is None
            else memory_generation_enabled
        )
        self._client = client
        self._orchestrator = None
        self._sessions_adapter = None

    def set_up(self) -> None:
        """Initialize settings, ToolClient, and compile LangGraph workflow."""
        from app.services.langgraph_state import WorkflowGraphState

        # Explicitly materialize annotations in the running container for Python 3.14
        WorkflowGraphState.__annotations__ = dict(WorkflowGraphState.__annotations__)

        effective_tool_mode = self.tool_mode
        if effective_tool_mode == "remote" and ("localhost" in self.tools_url or "127.0.0.1" in self.tools_url):
            logger.info("Localhost tools_url detected in container; using inprocess tool mode")
            effective_tool_mode = "inprocess"

        os.environ["PROJECT_ID"] = self.project
        os.environ["TOOL_MODE"] = effective_tool_mode
        os.environ["TOOLS_URL"] = self.tools_url
        os.environ["LLM_PROVIDER"] = self.llm_provider
        os.environ["GEMINI_MODEL_ID"] = self.gemini_model_id
        os.environ["RESEARCH_AGENT_REMOTE_ENABLED"] = "false"
        os.environ["SENTIMENT_AGENT_REMOTE_ENABLED"] = "false"

        settings = Settings(
            project_id=self.project,
            tool_mode=effective_tool_mode,
            tools_url=self.tools_url,
            llm_provider=self.llm_provider,
            gemini_model_id=self.gemini_model_id,
        )

        tool_client = ToolClient(
            mode=effective_tool_mode,
            base_url=self.tools_url,
            settings=settings,
        )

        self._orchestrator = LangGraphOrchestrator(
            store=None,
            tool_client=tool_client,
        )

        from app.adapters.sessions import get_sessions_adapter

        self._sessions_adapter = get_sessions_adapter(
            mode=self.sessions_mode,
            project=self.project,
            location=self.location,
        )

        logger.info(
            f"RebalanceGraphApp set_up completed (tool_mode={effective_tool_mode}, tools_url={self.tools_url}, sessions_mode={self.sessions_mode})"
        )

    def query(
        self,
        request: dict[str, Any],
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute LangGraph rebalancing workflow for request payload.

        Args:
            request: Portfolio rebalance request payload dictionary.
            run_id: Optional execution run/trace ID.
            session_id: Optional conversational session ID.

        Returns:
            Dict[str, Any]: Serialized OrchestrationResponse dictionary.
        """
        try:
            if self._orchestrator is None:
                self.set_up()

            req = PortfolioRebalanceRequest.model_validate(request)
            if run_id:
                req.correlation.trace_id = run_id

            # Session lifecycle (Task P2-01)
            effective_session_id = session_id or req.correlation.session_id
            session_name = None
            if self._sessions_adapter:
                try:
                    actor_id = req.actor.actor_id if req.actor else "local_owner"
                    session_name = self._sessions_adapter.create_or_get_session(
                        user_id=actor_id,
                        session_id=effective_session_id,
                    )
                    req.correlation.session_id = session_name

                    user_event_text = (
                        f"Rebalance review requested for account '{req.account_profile.account_id}' "
                        f"(client '{req.client_profile.client_id}')."
                    )
                    self._sessions_adapter.append_user_event(
                        session_name=session_name,
                        text=user_event_text,
                        invocation_id=run_id,
                    )
                except Exception as sess_err:
                    logger.warning(f"Failed to record session request event: {sess_err}")

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    resp = pool.submit(asyncio.run, self._orchestrator.run(req)).result()
            else:
                resp = asyncio.run(self._orchestrator.run(req))

            if session_name:
                resp.correlation.session_id = session_name
                if self._sessions_adapter:
                    try:
                        summary_text = (
                            resp.recommendation_package.summary
                            if resp.recommendation_package and resp.recommendation_package.summary
                            else f"Rebalance workflow completed with state: {resp.workflow_state}"
                        )
                        self._sessions_adapter.append_summary_event(
                            session_name=session_name,
                            text=summary_text,
                            invocation_id=run_id,
                        )
                    except Exception as sess_err:
                        logger.warning(f"Failed to record session summary event: {sess_err}")

                # Trigger asynchronous memory generation (Task P2-03)
                if self.memory_generation_enabled:
                    import threading

                    actor_id = req.actor.actor_id if req.actor else "local_owner"
                    threading.Thread(
                        target=self._trigger_memory_generation,
                        args=(session_name, actor_id),
                        daemon=True,
                    ).start()

            if resp.workflow_state == WorkflowState.BLOCKED:
                from app.contracts.common import ErrorSeverity, StructuredError
                if not resp.structured_error:
                    resp.structured_error = StructuredError(
                        code="CONTENT_BLOCKED",
                        message="Request blocked by content safety policy: potential prompt injection or unsafe content detected.",
                        severity=ErrorSeverity.CRITICAL,
                        source="model_armor",
                    )

            return resp.model_dump(mode="json")
        except Exception as e:
            import traceback

            logger.error(f"RebalanceGraphApp query failed: {e}", exc_info=True)
            return {
                "workflow_state": "BLOCKED",
                "error": str(e),
                "traceback": traceback.format_exc(),
            }

    def _trigger_memory_generation(
        self,
        session_name: str,
        actor_id: str,
        client: Optional[Any] = None,
    ) -> None:
        """Trigger asynchronous memory generation from session events (Task P2-03)."""
        try:
            effective_client = client or self._client or getattr(self._sessions_adapter, "_client", None)
            if effective_client is None:
                import agentplatform

                effective_client = agentplatform.Client(project=self.project, location=self.location)

            runtime_name = (
                getattr(self._sessions_adapter, "_runtime_name", None)
                or getattr(self._sessions_adapter, "runtime_name", None)
            )
            if not runtime_name:
                from pathlib import Path

                deployed_file = (
                    Path(__file__).resolve().parent / "deployed_runtime.txt"
                )
                if deployed_file.exists():
                    runtime_name = deployed_file.read_text().strip()

            if runtime_name and session_name:
                logger.info(
                    f"Triggering asynchronous memory generation for session: {session_name} (user: {actor_id})"
                )
                effective_client.memory_banks.memories.generate(
                    name=runtime_name,
                    vertex_session_source={"session": session_name},
                    scope={"user_id": actor_id},
                )
        except Exception as e:
            logger.warning(
                f"Asynchronous memory generation trigger failed for session {session_name}: {e}"
            )
