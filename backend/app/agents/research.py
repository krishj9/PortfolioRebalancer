import httpx

from app.agents.base import completed_stage
from app.contracts.analysis import AgentStageResult
from app.contracts.workflow import PortfolioRebalanceRequest
from app.core.config import Settings, get_settings


class ResearchAgent:
    name = "Research Agent"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def run(self, request: PortfolioRebalanceRequest) -> tuple[AgentStageResult, dict]:
        if self.settings.research_agent_remote_enabled:
            try:
                return await self._run_remote(request)
            except (httpx.HTTPError, ValueError):
                pass
        return self._run_local(request)

    async def _run_remote(
        self, request: PortfolioRebalanceRequest
    ) -> tuple[AgentStageResult, dict]:
        async with httpx.AsyncClient(timeout=self.settings.remote_agent_timeout_seconds) as client:
            response = await client.post(
                self.settings.research_agent_url,
                json={
                    "task": "portfolio_research",
                    "request_id": request.correlation.request_id,
                    "symbols": [holding.symbol for holding in request.portfolio_snapshot.holdings],
                    "portfolio_request": request.model_dump(mode="json"),
                },
            )
            response.raise_for_status()
            body = response.json()
        payload = body.get("payload", {})
        stage = completed_stage(
            self.name,
            body.get("summary", "Remote research agent returned market context."),
            protocol="A2A",
            execution_location="remote",
        )
        return stage, payload

    def _run_local(self, request: PortfolioRebalanceRequest) -> tuple[AgentStageResult, dict]:
        holdings = request.portfolio_snapshot.holdings
        symbols = ", ".join(holding.symbol for holding in holdings)
        payload = {
            "symbols": [holding.symbol for holding in holdings],
            "summary": f"Using request-provided market values for {symbols}.",
            "source": "request_payload",
        }
        stage = completed_stage(
            self.name,
            "Prepared local fallback market context from request holdings.",
            protocol="LOCAL",
            execution_location="in_process_fallback",
        )
        return stage, payload


# Standard alias
MarketResearchAgent = ResearchAgent
