"""
Research Agent A2A Server with LLM Integration.

This server implements the Agent-to-Agent (A2A) protocol for the Research Agent,
providing market context synthesis using AWS Bedrock LLMs.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Add backend to path for imports
backend_dir = Path(__file__).resolve().parent.parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Ensure app package includes backend/app if local app package was resolved first
try:
    import app
    backend_app = backend_dir / "app"
    if backend_app.is_dir() and hasattr(app, "__path__") and str(backend_app) not in app.__path__:
        app.__path__.append(str(backend_app))
except Exception:
    pass

from app.adapters.bedrock import BedrockModelAdapter
from app.adapters.prompts import PromptTemplateLoader
from app.adapters.validation import ResponseValidator
from app.core.config import get_feature_flags, get_llm_config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Research Agent A2A Server",
    description="Agent-to-Agent server for market research with LLM integration",
    version="1.0.0",
)


# ============================================================================
# Request/Response Models
# ============================================================================


class A2ARequest(BaseModel):
    """A2A protocol request envelope."""

    task: str = Field(..., description="Task identifier")
    request_id: str = Field(..., description="Request correlation ID")
    symbols: list[str] = Field(..., description="List of symbols to research")
    portfolio_request: dict = Field(..., description="Full portfolio request context")


class A2AResponse(BaseModel):
    """A2A protocol response envelope."""

    request_id: str
    summary: str
    payload: dict
    timestamp: str
    agent_name: str = "Research Agent"
    protocol: str = "A2A"
    execution_location: str = "remote"


# ============================================================================
# Research Agent LLM Implementation
# ============================================================================


class ResearchAgentLLM:
    """Research Agent with LLM-enhanced market context synthesis."""

    def __init__(
        self,
        bedrock_adapter: Optional[BedrockModelAdapter] = None,
        prompt_loader: Optional[PromptTemplateLoader] = None,
        validator: Optional[ResponseValidator] = None,
    ):
        self.bedrock_adapter = bedrock_adapter
        self.prompt_loader = prompt_loader
        self.validator = validator

        # Load configuration
        self.feature_flags = get_feature_flags()
        self.llm_config = get_llm_config()

        # Load prompt templates if LLM is enabled
        if self.feature_flags.research_agent_llm_enabled and self.prompt_loader:
            self._load_templates()

    def _load_templates(self) -> None:
        """Load prompt templates for Research Agent."""
        try:
            template_path = Path("prompts/research-agent/v1.0.0.yaml")
            self.templates = self.prompt_loader.load_from_file(template_path)
            logger.info("Loaded Research Agent prompt templates v1.0.0")
        except Exception as e:
            logger.warning(f"Failed to load Research Agent templates: {e}. LLM features will be disabled.")
            self.feature_flags.research_agent_llm_enabled = False

    async def synthesize_market_context(
        self,
        symbols: list[str],
        portfolio_context: dict,
        market_data: Optional[list[dict]] = None,
        news_items: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """
        Synthesize market context using LLM.

        Args:
            symbols: List of symbols to research
            portfolio_context: Portfolio context from request
            market_data: Optional market data
            news_items: Optional news items

        Returns:
            Dictionary with market context synthesis
        """
        try:
            # Check if LLM is enabled
            if not self.feature_flags.research_agent_llm_enabled or not self.bedrock_adapter:
                return self._fallback_market_context(symbols, portfolio_context)

            # Prepare template inputs
            template_inputs = {
                "portfolio_id": portfolio_context.get("portfolio_id", "UNKNOWN"),
                "current_allocation": self._format_allocation(
                    portfolio_context.get("portfolio_snapshot", {})
                ),
                "target_allocation": self._format_allocation(
                    portfolio_context.get("allocation_target", {})
                ),
                "holdings": ", ".join(symbols),
                "market_data": market_data or self._mock_market_data(symbols),
                "news_items": news_items or self._mock_news_items(symbols),
            }

            # Render prompt
            template = self.templates["market_context_synthesis"]
            rendered = self.prompt_loader.render_template(template, template_inputs)

            # Invoke LLM
            response = await self.bedrock_adapter.invoke(
                model_id=self.llm_config.research_agent_model,
                system_prompt=rendered.system_prompt,
                user_prompt=rendered.user_prompt,
                temperature=0.5,  # Moderate temperature for synthesis
                max_tokens=2000,
            )

            # Validate response
            if self.validator:
                validation_result = await self.validator.validate_response(
                    response.content,
                    template["validation"]["output_schema"],
                    confidence_threshold=template["validation"]["confidence_threshold"],
                )

                if not validation_result.is_valid:
                    logger.warning(f"Market context validation failed: {validation_result.violations}")
                    return self._fallback_market_context(symbols, portfolio_context)

            # Parse response
            result = json.loads(response.content)

            logger.info(f"Synthesized market context with confidence {result.get('confidence', 0.0)}")
            return result

        except Exception as e:
            logger.error(f"Market context synthesis failed: {e}", exc_info=True)
            return self._fallback_market_context(symbols, portfolio_context)

    def _fallback_market_context(self, symbols: list[str], portfolio_context: dict) -> dict[str, Any]:
        """Fallback market context when LLM is unavailable."""
        return {
            "market_summary": f"Using request-provided market values for {', '.join(symbols)}.",
            "key_trends": [],
            "sector_insights": [],
            "risk_factors": ["LLM unavailable - using fallback context"],
            "opportunities": [],
            "confidence": 0.5,
            "source": "fallback",
        }

    def _format_allocation(self, allocation_data: dict) -> str:
        """Format allocation data for prompt."""
        if not allocation_data:
            return "Not specified"

        # Extract allocation targets if available
        targets = allocation_data.get("asset_class_targets", {})
        if targets:
            return ", ".join([f"{k}: {v*100:.1f}%" for k, v in targets.items()])

        return "Not specified"

    def _mock_market_data(self, symbols: list[str]) -> list[dict]:
        """Generate mock market data for testing."""
        return [
            {
                "symbol": symbol,
                "price": 100.0,
                "change_percent": 0.0,
                "volume": 1000000,
            }
            for symbol in symbols
        ]

    def _mock_news_items(self, symbols: list[str]) -> list[dict]:
        """Generate mock news items for testing."""
        return [
            {
                "headline": f"Market update for {symbols[0] if symbols else 'portfolio'}",
                "source": "Market Data Provider",
                "published_date": datetime.now().isoformat(),
                "summary": "No recent news available",
            }
        ]


# ============================================================================
# A2A Endpoint
# ============================================================================


# Initialize Research Agent (singleton)
research_agent: Optional[ResearchAgentLLM] = None


def get_research_agent() -> ResearchAgentLLM:
    """Get or create Research Agent instance."""
    global research_agent

    if research_agent is None:
        feature_flags = get_feature_flags()

        if feature_flags.research_agent_llm_enabled:
            bedrock_adapter = BedrockModelAdapter()
            prompt_loader = PromptTemplateLoader()
            validator = ResponseValidator()

            research_agent = ResearchAgentLLM(
                bedrock_adapter=bedrock_adapter,
                prompt_loader=prompt_loader,
                validator=validator,
            )
        else:
            research_agent = ResearchAgentLLM()

    return research_agent


@app.post("/a2a/research", response_model=A2AResponse)
async def research_endpoint(request: A2ARequest) -> A2AResponse:
    """
    A2A endpoint for market research.

    Args:
        request: A2A request envelope

    Returns:
        A2A response envelope with market context
    """
    logger.info(f"Received A2A request {request.request_id} for task {request.task}")

    try:
        # Get Research Agent
        agent = get_research_agent()

        # Synthesize market context
        result = await agent.synthesize_market_context(
            symbols=request.symbols,
            portfolio_context=request.portfolio_request,
        )

        # Build response
        response = A2AResponse(
            request_id=request.request_id,
            summary=result.get("market_summary", "Market context synthesized"),
            payload=result,
            timestamp=datetime.now().isoformat(),
        )

        logger.info(f"Completed A2A request {request.request_id}")
        return response

    except Exception as e:
        logger.error(f"A2A request {request.request_id} failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Research agent failed: {str(e)}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "agent": "Research Agent",
        "protocol": "A2A",
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================================
# Main
# ============================================================================


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8101)
