"""Portfolio Rebalancing Agent with LLM-enhanced drift explanation."""

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from app.adapters.bedrock import BedrockModelAdapter
from app.adapters.prompts import PromptTemplateLoader
from app.adapters.validation import ResponseValidator
from app.agents.base import completed_stage, failed_stage
from app.contracts.analysis import AgentStageResult, DriftItem
from app.contracts.workflow import PortfolioRebalanceRequest
from app.core.config import get_feature_flags, get_llm_config
from app.services.portfolio import calculate_asset_allocation, calculate_drift

logger = logging.getLogger(__name__)


class PortfolioRebalancingAgent:
    """
    Portfolio Rebalancing Agent with LLM-enhanced drift explanation.
    
    Features:
    - Deterministic drift calculation (always performed)
    - LLM-enhanced drift explanation and rationale
    - Strategy recommendation based on drift and constraints
    - Validation against deterministic calculations
    - Graceful fallback to template explanations
    """

    name = "Portfolio Rebalancing Agent"

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
        if self.feature_flags.rebalancing_agent_llm_enabled and self.prompt_loader:
            self._load_templates()

    def _load_templates(self) -> None:
        """Load prompt templates for Rebalancing Agent."""
        try:
            template_path = Path(".kiro/prompts/rebalancing-agent/v1.0.0.yaml")
            self.templates = self.prompt_loader.load_from_file(template_path)
            logger.info("Loaded Rebalancing Agent prompt templates v1.0.0")
        except Exception as e:
            logger.warning(f"Failed to load Rebalancing Agent templates: {e}. LLM features will be disabled.")
            self.feature_flags.rebalancing_agent_llm_enabled = False

    async def run(
        self, request: PortfolioRebalanceRequest
    ) -> tuple[AgentStageResult, dict[str, dict[str, Decimal] | list[DriftItem]]]:
        """
        Execute rebalancing analysis.
        
        Always performs deterministic drift calculation.
        If LLM is enabled, adds explanation and strategy recommendation.
        """
        try:
            # Always calculate drift deterministically
            current_allocation = calculate_asset_allocation(request.portfolio_snapshot)
            drift = calculate_drift(request.portfolio_snapshot, request.allocation_target)
            out_of_tolerance = [item for item in drift if not item.within_tolerance]
            
            # Base payload with deterministic calculations
            payload = {
                "current_allocation": current_allocation,
                "target_allocation": request.allocation_target.asset_class_targets,
                "drift": drift,
            }
            
            # Add LLM-enhanced explanation if enabled
            if self.feature_flags.rebalancing_agent_llm_enabled and self.bedrock_adapter:
                try:
                    llm_analysis = await self._generate_llm_analysis(
                        request, current_allocation, drift, out_of_tolerance
                    )
                    if llm_analysis:
                        payload["llm_explanation"] = llm_analysis.get("drift_explanation")
                        payload["llm_strategy"] = llm_analysis.get("strategy_recommendation")
                except Exception as e:
                    logger.error(f"LLM analysis failed: {e}", exc_info=True)
                    if not self.feature_flags.fallback_on_llm_failure:
                        raise
            
            # Build summary
            summary = f"Calculated allocation drift with {len(out_of_tolerance)} target(s) outside tolerance"
            if payload.get("llm_explanation"):
                summary += " with LLM-enhanced explanation"
            
            return completed_stage(self.name, summary + "."), payload
            
        except Exception as e:
            logger.error(f"Rebalancing Agent failed: {e}", exc_info=True)
            return failed_stage(self.name, f"Rebalancing analysis failed: {str(e)}"), {}

    async def _generate_llm_analysis(
        self,
        request: PortfolioRebalanceRequest,
        current_allocation: dict[str, Decimal],
        drift: list[DriftItem],
        out_of_tolerance: list[DriftItem],
    ) -> Optional[dict[str, Any]]:
        """Generate LLM-enhanced drift explanation and strategy recommendation."""
        
        # Generate drift explanation
        drift_explanation = await self.explain_drift(
            request, current_allocation, drift, out_of_tolerance
        )
        
        # Generate strategy recommendation
        strategy_recommendation = await self.recommend_strategy(
            request, current_allocation, drift, drift_explanation
        )
        
        return {
            "drift_explanation": drift_explanation,
            "strategy_recommendation": strategy_recommendation,
        }

    async def explain_drift(
        self,
        request: PortfolioRebalanceRequest,
        current_allocation: dict[str, Decimal],
        drift: list[DriftItem],
        out_of_tolerance: list[DriftItem],
    ) -> Optional[dict[str, Any]]:
        """
        Generate LLM-enhanced drift explanation.
        
        Args:
            request: Portfolio rebalance request
            current_allocation: Current asset allocation
            drift: List of drift items
            out_of_tolerance: Drift items outside tolerance
            
        Returns:
            Dictionary with drift explanation
        """
        try:
            # Calculate drift metrics
            total_drift = sum(abs(float(getattr(item, "drift_pct", getattr(item, "drift_amount", 0.0)))) for item in drift)
            max_drift = max((abs(float(getattr(item, "drift_pct", getattr(item, "drift_amount", 0.0)))) for item in drift), default=0.0)
            
            # Prepare template inputs
            template_inputs = {
                "portfolio_id": getattr(request, "portfolio_id", getattr(request.account_profile, "account_id", "PORT-001")),
                "risk_tolerance": getattr(request.risk_profile, "risk_level", getattr(request.client_profile, "risk_tolerance", "MODERATE")),
                "current_allocation": [
                    {"asset_class": k, "percentage": float(v) * (100 if float(v) <= 1.0 else 1)}
                    for k, v in current_allocation.items()
                ],
                "target_allocation": [
                    {"asset_class": k, "percentage": float(v) * (100 if float(v) <= 1.0 else 1)}
                    for k, v in request.allocation_target.asset_class_targets.items()
                ],
                "total_drift": round(total_drift, 2),
                "max_drift": round(max_drift, 2),
                "drift_threshold": 5.0,  # TODO: Get from config
                "holdings": [
                    {
                        "symbol": holding.symbol,
                        "return_percentage": 0.0,  # TODO: Calculate actual returns
                    }
                    for holding in request.portfolio_snapshot.holdings
                ],
            }
            
            # Render prompt
            template = self.templates["drift_explanation"]
            rendered = self.prompt_loader.render_template(template, template_inputs)
            
            # Invoke LLM
            response = await self.bedrock_adapter.invoke_model(
                model_id=self.llm_config.rebalancing_agent_model,
                system_prompt=rendered.system_prompt,
                prompt=rendered.user_prompt,
                temperature=0.5,
                max_tokens=1500,
            )
            
            # Validate response
            if self.validator:
                validation_result = await self.validator.validate_response(
                    response.content,
                    template["validation"]["output_schema"],
                    confidence_threshold=template["validation"]["confidence_threshold"],
                )
                
                if not validation_result.is_valid:
                    logger.warning(f"Drift explanation validation failed: {validation_result.violations}")
                    return None
            
            # Parse and validate against deterministic calculations
            result = json.loads(response.content)
            
            # Validate drift amounts match deterministic calculations
            if not self._validate_drift_consistency(result, drift):
                logger.warning("LLM drift explanation inconsistent with deterministic calculations")
                return None
            
            logger.info(f"Generated drift explanation with confidence {result.get('confidence', 0.0)}")
            return result
            
        except Exception as e:
            logger.error(f"Drift explanation failed: {e}", exc_info=True)
            return None

    async def recommend_strategy(
        self,
        request: PortfolioRebalanceRequest,
        current_allocation: dict[str, Decimal],
        drift: list[DriftItem],
        drift_explanation: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """
        Generate LLM-enhanced strategy recommendation.
        
        Args:
            request: Portfolio rebalance request
            current_allocation: Current asset allocation
            drift: List of drift items
            drift_explanation: Drift explanation from LLM
            
        Returns:
            Dictionary with strategy recommendation
        """
        try:
            if not drift_explanation:
                return None
            
            # Prepare template inputs
            template_inputs = {
                "portfolio_id": getattr(request, "portfolio_id", getattr(request.account_profile, "account_id", "PORT-001")),
                "portfolio_value": float(request.portfolio_snapshot.total_value),
                "risk_tolerance": getattr(request.risk_profile, "risk_level", getattr(request.client_profile, "risk_tolerance", "MODERATE")),
                "drift_summary": drift_explanation.get("drift_summary", ""),
                "drifted_assets": drift_explanation.get("drifted_assets", []),
                "constraints": getattr(request.client_profile, "constraints", []) or [],
                "market_context": "Market conditions are stable",  # TODO: Get from research agent
            }
            
            # Render prompt
            template = self.templates["strategy_recommendation"]
            rendered = self.prompt_loader.render_template(template, template_inputs)
            
            # Invoke LLM
            response = await self.bedrock_adapter.invoke_model(
                model_id=self.llm_config.rebalancing_agent_model,
                system_prompt=rendered.system_prompt,
                prompt=rendered.user_prompt,
                temperature=0.5,
                max_tokens=1500,
            )
            
            # Validate response
            if self.validator:
                validation_result = await self.validator.validate_response(
                    response.content,
                    template["validation"]["output_schema"],
                    confidence_threshold=template["validation"]["confidence_threshold"],
                )
                
                if not validation_result.is_valid:
                    logger.warning(f"Strategy recommendation validation failed: {validation_result.violations}")
                    return None
            
            # Parse response
            result = json.loads(response.content)
            
            logger.info(f"Generated strategy recommendation with confidence {result.get('confidence', 0.0)}")
            return result
            
        except Exception as e:
            logger.error(f"Strategy recommendation failed: {e}", exc_info=True)
            return None

    def _validate_drift_consistency(
        self, llm_result: dict[str, Any], drift: list[DriftItem]
    ) -> bool:
        """
        Validate that LLM drift explanation is consistent with deterministic calculations.
        
        Args:
            llm_result: LLM drift explanation
            drift: Deterministic drift calculations
            
        Returns:
            True if consistent, False otherwise
        """
        try:
            # Check that all drifted assets in LLM result match deterministic calculations
            llm_drifted = {item["asset_class"]: item for item in llm_result.get("drifted_assets", [])}
            
            for drift_item in drift:
                if drift_item.asset_class in llm_drifted:
                    llm_item = llm_drifted[drift_item.asset_class]
                    
                    # Check drift amount is within tolerance (allow 1% difference for rounding)
                    llm_drift = abs(llm_item.get("drift_amount", 0.0))
                    actual_drift = abs(float(drift_item.drift_amount))
                    
                    if abs(llm_drift - actual_drift) > 0.01:
                        logger.warning(
                            f"Drift inconsistency for {drift_item.asset_class}: "
                            f"LLM={llm_drift}, Actual={actual_drift}"
                        )
                        return False
            
            return True
            
        except Exception as e:
            logger.error(f"Drift consistency validation failed: {e}")
            return False
