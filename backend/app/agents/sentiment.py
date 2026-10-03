"""Sentiment Analysis Agent with MCP integration."""

import logging
from typing import Any

import httpx

from app.agents.base import completed_stage, failed_stage
from app.contracts.analysis import AgentStageResult
from app.contracts.workflow import PortfolioRebalanceRequest
from app.core.config import Settings, get_feature_flags, get_settings

logger = logging.getLogger(__name__)


class SentimentAnalysisAgent:
    """
    Sentiment Analysis Agent with MCP integration.
    
    Features:
    - MCP JSON-RPC 2.0 protocol for remote sentiment analysis
    - LLM-enhanced sentiment classification
    - Theme identification
    - Graceful fallback to neutral sentiment
    """

    name = "Sentiment Analysis Agent"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.feature_flags = get_feature_flags()

    async def run(self, request: PortfolioRebalanceRequest) -> tuple[AgentStageResult, dict]:
        """
        Execute sentiment analysis.
        
        If MCP is enabled:
        1. Invoke MCP server for each symbol
        2. Aggregate sentiment results
        
        If MCP is disabled or fails:
        - Fall back to neutral sentiment
        """
        symbols = [holding.symbol for holding in request.portfolio_snapshot.holdings]
        
        if self.settings.sentiment_mcp_enabled:
            try:
                return await self._run_mcp(symbols)
            except Exception as e:
                logger.error(f"MCP sentiment analysis failed: {e}", exc_info=True)
                
                # Fall back to local if enabled
                if self.feature_flags.fallback_on_llm_failure:
                    logger.info("Falling back to neutral sentiment")
                    return self._run_local(symbols)
                else:
                    return failed_stage(self.name, f"Sentiment analysis failed: {str(e)}"), {}
        
        return self._run_local(symbols)

    async def _run_mcp(self, symbols: list[str]) -> tuple[AgentStageResult, dict]:
        """
        Execute sentiment analysis via MCP server.
        
        Args:
            symbols: List of symbols to analyze
            
        Returns:
            Tuple of (stage_result, payload)
        """
        logger.info(f"Analyzing sentiment for {len(symbols)} symbols via MCP")
        
        # Analyze each symbol
        symbol_sentiments = []
        
        async with httpx.AsyncClient(timeout=self.settings.remote_agent_timeout_seconds) as client:
            for symbol in symbols:
                try:
                    # Invoke MCP tool
                    response = await client.post(
                        self.settings.sentiment_mcp_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": f"sentiment-{symbol}",
                            "method": "analyze_symbol_news_sentiment",
                            "params": {
                                "symbol": symbol,
                            },
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                    
                    # Extract result
                    if "result" in body:
                        symbol_sentiments.append(body["result"])
                    elif "error" in body:
                        logger.warning(f"MCP error for {symbol}: {body['error']}")
                        symbol_sentiments.append(self._neutral_sentiment(symbol))
                    else:
                        logger.warning(f"Unexpected MCP response for {symbol}")
                        symbol_sentiments.append(self._neutral_sentiment(symbol))
                        
                except Exception as e:
                    logger.error(f"Failed to analyze sentiment for {symbol}: {e}")
                    symbol_sentiments.append(self._neutral_sentiment(symbol))
        
        # Aggregate results
        payload = self._aggregate_sentiments(symbol_sentiments)
        
        # Build summary
        positive_count = sum(1 for s in symbol_sentiments if s.get("overall_sentiment") == "POSITIVE")
        negative_count = sum(1 for s in symbol_sentiments if s.get("overall_sentiment") == "NEGATIVE")
        neutral_count = len(symbol_sentiments) - positive_count - negative_count
        
        summary = f"Analyzed {len(symbols)} symbols: {positive_count} positive, {negative_count} negative, {neutral_count} neutral"
        
        stage = completed_stage(
            self.name,
            summary,
            protocol="MCP",
            execution_location="mcp_server",
        )
        
        logger.info(f"MCP sentiment analysis completed: {summary}")
        
        return stage, payload

    def _run_local(self, symbols: list[str]) -> tuple[AgentStageResult, dict]:
        """
        Fallback sentiment analysis (neutral).
        
        Args:
            symbols: List of symbols
            
        Returns:
            Tuple of (stage_result, payload)
        """
        payload = {
            "symbols": symbols,
            "overall_sentiment": "NEUTRAL",
            "symbol_sentiments": [self._neutral_sentiment(symbol) for symbol in symbols],
            "summary": "No live news feed is configured yet; sentiment defaults to neutral.",
        }
        
        return (
            completed_stage(
                self.name,
                "Generated neutral fallback sentiment.",
                protocol="LOCAL",
                execution_location="in_process_fallback",
            ),
            payload,
        )

    def _neutral_sentiment(self, symbol: str) -> dict[str, Any]:
        """Generate neutral sentiment for a symbol."""
        return {
            "symbol": symbol,
            "overall_sentiment": "NEUTRAL",
            "sentiment_score": 0.0,
            "themes": [],
            "confidence": 0.5,
            "sources_analyzed": 0,
        }

    def _aggregate_sentiments(self, symbol_sentiments: list[dict]) -> dict[str, Any]:
        """
        Aggregate sentiment results across symbols.
        
        Args:
            symbol_sentiments: List of sentiment results per symbol
            
        Returns:
            Aggregated sentiment payload
        """
        if not symbol_sentiments:
            return {
                "symbols": [],
                "overall_sentiment": "NEUTRAL",
                "symbol_sentiments": [],
                "summary": "No symbols analyzed",
            }
        
        # Calculate average sentiment score
        avg_score = sum(s.get("sentiment_score", 0.0) for s in symbol_sentiments) / len(symbol_sentiments)
        
        # Determine overall sentiment
        if avg_score > 0.2:
            overall_sentiment = "POSITIVE"
        elif avg_score < -0.2:
            overall_sentiment = "NEGATIVE"
        else:
            overall_sentiment = "NEUTRAL"
        
        # Extract all themes
        all_themes = []
        for sentiment in symbol_sentiments:
            all_themes.extend(sentiment.get("themes", []))
        
        return {
            "symbols": [s.get("symbol") for s in symbol_sentiments],
            "overall_sentiment": overall_sentiment,
            "average_sentiment_score": avg_score,
            "symbol_sentiments": symbol_sentiments,
            "all_themes": all_themes,
            "summary": f"Aggregated sentiment across {len(symbol_sentiments)} symbols",
        }


# Standard alias
SentimentAgent = SentimentAnalysisAgent
