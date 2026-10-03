"""
Sentiment Agent MCP Server with LLM Integration.

This server implements the Model Context Protocol (MCP) for the Sentiment Agent,
providing sentiment analysis using AWS Bedrock LLMs.
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
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

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
    title="Sentiment Agent MCP Server",
    description="Model Context Protocol server for sentiment analysis with LLM integration",
    version="1.0.0",
)


# ============================================================================
# MCP Request/Response Models
# ============================================================================


class MCPRequest(BaseModel):
    """MCP JSON-RPC 2.0 request."""

    jsonrpc: str = Field(default="2.0", description="JSON-RPC version")
    id: str = Field(..., description="Request ID")
    method: str = Field(..., description="Method name")
    params: dict = Field(default_factory=dict, description="Method parameters")


class MCPResponse(BaseModel):
    """MCP JSON-RPC 2.0 response."""

    jsonrpc: str = Field(default="2.0", description="JSON-RPC version")
    id: str = Field(..., description="Request ID")
    result: Optional[dict] = None
    error: Optional[dict] = None


class MCPError(BaseModel):
    """MCP JSON-RPC 2.0 error."""

    code: int
    message: str
    data: Optional[dict] = None


# ============================================================================
# Sentiment Agent LLM Implementation
# ============================================================================


class SentimentAgentLLM:
    """Sentiment Agent with LLM-enhanced sentiment analysis."""

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
        if self.feature_flags.sentiment_agent_llm_enabled and self.prompt_loader:
            self._load_templates()

    def _load_templates(self) -> None:
        """Load prompt templates for Sentiment Agent."""
        try:
            template_path = Path("prompts/sentiment-agent/v1.0.0.yaml")
            self.templates = self.prompt_loader.load_from_file(template_path)
            logger.info("Loaded Sentiment Agent prompt templates v1.0.0")
        except Exception as e:
            logger.warning(f"Failed to load Sentiment Agent templates: {e}. LLM features will be disabled.")
            self.feature_flags.sentiment_agent_llm_enabled = False

    async def analyze_symbol_news_sentiment(
        self,
        symbol: str,
        news_items: Optional[list[dict]] = None,
        social_posts: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """
        Analyze sentiment for a symbol using LLM.

        Args:
            symbol: Stock symbol
            news_items: Optional news items
            social_posts: Optional social media posts

        Returns:
            Dictionary with sentiment analysis
        """
        try:
            # Check if LLM is enabled
            if not self.feature_flags.sentiment_agent_llm_enabled or not self.bedrock_adapter:
                return self._fallback_sentiment(symbol)

            # Prepare template inputs
            template_inputs = {
                "symbol": symbol,
                "news_items": news_items or self._mock_news_items(symbol),
                "social_posts": social_posts or self._mock_social_posts(symbol),
            }

            # Render prompt
            template = self.templates["sentiment_analysis"]
            rendered = self.prompt_loader.render_template(template, template_inputs)

            # Invoke LLM
            response = await self.bedrock_adapter.invoke(
                model_id=self.llm_config.sentiment_agent_model,
                system_prompt=rendered.system_prompt,
                user_prompt=rendered.user_prompt,
                temperature=0.3,  # Low temperature for consistent sentiment classification
                max_tokens=1000,
            )

            # Validate response
            if self.validator:
                validation_result = await self.validator.validate_response(
                    response.content,
                    template["validation"]["output_schema"],
                    confidence_threshold=template["validation"]["confidence_threshold"],
                )

                if not validation_result.is_valid:
                    logger.warning(f"Sentiment analysis validation failed: {validation_result.violations}")
                    return self._fallback_sentiment(symbol)

            # Parse response
            result = json.loads(response.content)

            logger.info(f"Analyzed sentiment for {symbol} with confidence {result.get('confidence', 0.0)}")
            return result

        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}", exc_info=True)
            return self._fallback_sentiment(symbol)

    def _fallback_sentiment(self, symbol: str) -> dict[str, Any]:
        """Fallback sentiment when LLM is unavailable."""
        return {
            "symbol": symbol,
            "overall_sentiment": "NEUTRAL",
            "sentiment_score": 0.0,
            "themes": [],
            "confidence": 0.5,
            "sources_analyzed": 0,
            "source": "fallback",
        }

    def _mock_news_items(self, symbol: str) -> list[dict]:
        """Generate mock news items for testing."""
        return [
            {
                "headline": f"Market update for {symbol}",
                "source": "Market Data Provider",
                "published_date": datetime.now().isoformat(),
                "summary": "No recent news available",
                "sentiment_indicators": "neutral",
            }
        ]

    def _mock_social_posts(self, symbol: str) -> list[dict]:
        """Generate mock social posts for testing."""
        return [
            {
                "platform": "Twitter",
                "content": f"Watching {symbol} today",
                "engagement": 0,
                "date": datetime.now().isoformat(),
            }
        ]


# ============================================================================
# MCP Tool Handlers
# ============================================================================


# Initialize Sentiment Agent (singleton)
sentiment_agent: Optional[SentimentAgentLLM] = None


def get_sentiment_agent() -> SentimentAgentLLM:
    """Get or create Sentiment Agent instance."""
    global sentiment_agent

    if sentiment_agent is None:
        feature_flags = get_feature_flags()

        if feature_flags.sentiment_agent_llm_enabled:
            bedrock_adapter = BedrockModelAdapter()
            prompt_loader = PromptTemplateLoader()
            validator = ResponseValidator()

            sentiment_agent = SentimentAgentLLM(
                bedrock_adapter=bedrock_adapter,
                prompt_loader=prompt_loader,
                validator=validator,
            )
        else:
            sentiment_agent = SentimentAgentLLM()

    return sentiment_agent


async def handle_analyze_symbol_news_sentiment(params: dict) -> dict:
    """
    Handle analyze_symbol_news_sentiment tool invocation.

    Args:
        params: Tool parameters

    Returns:
        Sentiment analysis result
    """
    symbol = params.get("symbol")
    if not symbol:
        raise ValueError("Missing required parameter: symbol")

    news_items = params.get("news_items")
    social_posts = params.get("social_posts")

    agent = get_sentiment_agent()
    result = await agent.analyze_symbol_news_sentiment(
        symbol=symbol,
        news_items=news_items,
        social_posts=social_posts,
    )

    return result


# MCP tool registry
MCP_TOOLS = {
    "analyze_symbol_news_sentiment": handle_analyze_symbol_news_sentiment,
}


# ============================================================================
# MCP Endpoints
# ============================================================================


@app.post("/mcp", response_model=MCPResponse)
async def mcp_endpoint(request: MCPRequest) -> MCPResponse:
    """
    MCP JSON-RPC 2.0 endpoint.

    Args:
        request: MCP request

    Returns:
        MCP response
    """
    logger.info(f"Received MCP request {request.id} for method {request.method}")

    try:
        # Check if method exists
        if request.method not in MCP_TOOLS:
            return MCPResponse(
                id=request.id,
                error=MCPError(
                    code=-32601,
                    message=f"Method not found: {request.method}",
                ).model_dump(),
            )

        # Invoke tool handler
        handler = MCP_TOOLS[request.method]
        result = await handler(request.params)

        # Build response
        response = MCPResponse(
            id=request.id,
            result=result,
        )

        logger.info(f"Completed MCP request {request.id}")
        return response

    except ValueError as e:
        logger.error(f"MCP request {request.id} validation error: {e}")
        return MCPResponse(
            id=request.id,
            error=MCPError(
                code=-32602,
                message=f"Invalid params: {str(e)}",
            ).model_dump(),
        )

    except Exception as e:
        logger.error(f"MCP request {request.id} failed: {e}", exc_info=True)
        return MCPResponse(
            id=request.id,
            error=MCPError(
                code=-32603,
                message=f"Internal error: {str(e)}",
            ).model_dump(),
        )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "agent": "Sentiment Agent",
        "protocol": "MCP",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/tools")
async def list_tools():
    """List available MCP tools."""
    return {
        "tools": [
            {
                "name": "analyze_symbol_news_sentiment",
                "description": "Analyze sentiment for a symbol from news and social media",
                "parameters": {
                    "symbol": {"type": "string", "required": True},
                    "news_items": {"type": "array", "required": False},
                    "social_posts": {"type": "array", "required": False},
                },
            }
        ]
    }


# ============================================================================
# Main
# ============================================================================


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8201)
