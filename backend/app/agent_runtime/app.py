"""Vertex AI Agent Runtime application for Portfolio Rebalancer."""

import asyncio
import concurrent.futures
import logging
import os
from typing import Any, Optional

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
    ) -> None:
        self.project = project
        self.location = location
        self.tool_mode = tool_mode
        self.tools_url = tools_url
        self.llm_provider = llm_provider
        self.gemini_model_id = gemini_model_id
        self._orchestrator = None

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
        logger.info(
            f"RebalanceGraphApp set_up completed (tool_mode={effective_tool_mode}, tools_url={self.tools_url})"
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
            if session_id:
                req.correlation.session_id = session_id

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    resp = pool.submit(asyncio.run, self._orchestrator.run(req)).result()
            else:
                resp = asyncio.run(self._orchestrator.run(req))

            return resp.model_dump(mode="json")
        except Exception as e:
            import traceback

            logger.error(f"RebalanceGraphApp query failed: {e}", exc_info=True)
            return {
                "workflow_state": "BLOCKED",
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
