"""Risk & Compliance Agent with LLM-enhanced policy verdict explanation."""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.adapters.bedrock import BedrockModelAdapter
from app.adapters.prompts import PromptTemplateLoader
from app.adapters.validation import ResponseValidator
from app.agents.base import blocked_stage, completed_stage, failed_stage
from app.contracts.analysis import (
    AgentStageResult,
    DriftItem,
    PolicyVerdictStatus,
    RiskPolicyResponse,
)
from app.contracts.domain import PortfolioSnapshot, RiskProfile
from app.core.config import get_feature_flags, get_llm_config
from app.services.policy import evaluate_policy

logger = logging.getLogger(__name__)


class RiskComplianceAgent:
    """
    Risk & Compliance Agent with LLM-enhanced policy verdict explanation.
    
    Features:
    - Deterministic policy evaluation (always performed)
    - LLM-enhanced verdict explanation and rationale
    - Corrective action recommendations for violations
    - Validation against deterministic policy evaluation
    - Graceful fallback to template explanations
    """

    name = "Risk & Compliance Agent"

    def __init__(
        self,
        bedrock_adapter: Optional[BedrockModelAdapter] = None,
        prompt_loader: Optional[PromptTemplateLoader] = None,
        validator: Optional[ResponseValidator] = None,
        tool_client: Optional[Any] = None,
    ):
        self.bedrock_adapter = bedrock_adapter
        self.prompt_loader = prompt_loader
        self.validator = validator
        self.tool_client = tool_client
        
        # Load configuration
        self.feature_flags = get_feature_flags()
        self.llm_config = get_llm_config()
        
        # Load prompt templates if LLM is enabled
        if self.feature_flags.risk_agent_llm_enabled and self.prompt_loader:
            self._load_templates()

    def _load_templates(self) -> None:
        """Load prompt templates for Risk Agent."""
        try:
            template_path = Path("prompts/risk-agent/v1.0.0.yaml")
            self.templates = self.prompt_loader.load_from_file(template_path)
            logger.info("Loaded Risk Agent prompt templates v1.0.0")
        except Exception as e:
            logger.warning(f"Failed to load Risk Agent templates: {e}. LLM features will be disabled.")
            self.feature_flags.risk_agent_llm_enabled = False

    async def run(
        self,
        snapshot: PortfolioSnapshot,
        drift: list[DriftItem],
        risk_profile: RiskProfile | None,
    ) -> tuple[AgentStageResult, RiskPolicyResponse]:
        """
        Execute risk and compliance evaluation.
        
        Always performs deterministic policy evaluation.
        If LLM is enabled, adds explanation and corrective actions.
        """
        try:
            # Always evaluate policy deterministically
            if self.tool_client:
                result = await self.tool_client.evaluate_policy(snapshot, drift, risk_profile)
            else:
                result = evaluate_policy(snapshot, drift, risk_profile)
            
            # Add LLM-enhanced explanation if enabled
            if self.feature_flags.risk_agent_llm_enabled and self.bedrock_adapter:
                try:
                    llm_analysis = await self._generate_llm_analysis(
                        snapshot, drift, risk_profile, result
                    )
                    if llm_analysis and llm_analysis.get("verdict_explanation"):
                        logger.info(f"LLM policy explanation: {llm_analysis.get('verdict_explanation', {}).get('verdict_summary')}")
                except Exception as e:
                    logger.error(f"LLM analysis failed: {e}", exc_info=True)
                    if not self.feature_flags.fallback_on_llm_failure:
                        raise
            
            # Build stage result
            if result.verdict == PolicyVerdictStatus.NON_COMPLIANT:
                stage = blocked_stage(self.name, "Policy violation blocks trade proposal approval.")
            else:
                stage = completed_stage(self.name, f"Policy verdict: {result.verdict}.")
            
            return stage, result
            
        except Exception as e:
            logger.error(f"Risk & Compliance Agent failed: {e}", exc_info=True)
            return failed_stage(self.name, f"Policy evaluation failed: {str(e)}"), RiskPolicyResponse(
                verdict=PolicyVerdictStatus.UNRESOLVED,
                blockers=[],
                warnings=[],
            )

    async def _generate_llm_analysis(
        self,
        snapshot: PortfolioSnapshot,
        drift: list[DriftItem],
        risk_profile: RiskProfile | None,
        policy_result: RiskPolicyResponse,
    ) -> Optional[dict[str, Any]]:
        """Generate LLM-enhanced policy verdict explanation and corrective actions."""
        
        # Generate verdict explanation
        verdict_explanation = await self.explain_policy_verdict(
            snapshot, drift, risk_profile, policy_result
        )
        
        # Generate corrective actions if non-compliant
        corrective_actions = None
        if policy_result.verdict == PolicyVerdictStatus.NON_COMPLIANT:
            corrective_actions = await self.recommend_corrective_actions(
                snapshot, policy_result, verdict_explanation
            )
        
        return {
            "verdict_explanation": verdict_explanation,
            "corrective_actions": corrective_actions,
        }

    async def explain_policy_verdict(
        self,
        snapshot: PortfolioSnapshot,
        drift: list[DriftItem],
        risk_profile: RiskProfile | None,
        policy_result: RiskPolicyResponse,
    ) -> Optional[dict[str, Any]]:
        """
        Generate LLM-enhanced policy verdict explanation.
        
        Args:
            snapshot: Portfolio snapshot
            drift: List of drift items
            risk_profile: Risk profile
            policy_result: Deterministic policy evaluation result
            
        Returns:
            Dictionary with verdict explanation
        """
        try:
            # Prepare template inputs
            template_inputs = {
                "portfolio_id": getattr(snapshot, "portfolio_id", getattr(snapshot, "account_id", snapshot.snapshot_id)),
                "risk_tolerance": getattr(risk_profile, "risk_level", getattr(risk_profile, "risk_tolerance", "MODERATE")) if risk_profile else "MODERATE",
                "verdict_status": policy_result.verdict.value,
                "policy_rules": [
                    {
                        "rule_id": f"RULE-{i}",
                        "description": blocker,
                        "result": "FAIL",
                        "details": blocker,
                    }
                    for i, blocker in enumerate(getattr(policy_result, "blockers", []))
                ] + [
                    {
                        "rule_id": f"WARN-{i}",
                        "description": warning,
                        "result": "PASS",
                        "details": warning,
                    }
                    for i, warning in enumerate(getattr(policy_result, "warnings", []))
                ],
                "proposed_trades": [],  # TODO: Get from rebalancing proposal
            }
            
            # Render prompt
            template = self.templates["policy_verdict_explanation"]
            rendered = self.prompt_loader.render_template(template, template_inputs)
            
            # Invoke LLM
            response = await self.bedrock_adapter.invoke_model(
                model_id=self.llm_config.risk_agent_model,
                system_prompt=rendered.system_prompt,
                prompt=rendered.user_prompt,
                temperature=0.3,  # Low temperature for consistent policy explanation
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
                    logger.warning(f"Policy verdict explanation validation failed: {validation_result.violations}")
                    return None
            
            # Parse and validate against deterministic verdict
            result = json.loads(response.content)
            
            # Validate verdict matches deterministic evaluation
            if result.get("verdict_status") != policy_result.verdict.value:
                logger.warning(
                    f"LLM verdict {result.get('verdict_status')} does not match "
                    f"deterministic verdict {policy_result.verdict.value}"
                )
                return None
            
            logger.info(f"Generated policy verdict explanation with confidence {result.get('confidence', 0.0)}")
            return result
            
        except Exception as e:
            logger.error(f"Policy verdict explanation failed: {e}", exc_info=True)
            return None

    async def recommend_corrective_actions(
        self,
        snapshot: PortfolioSnapshot,
        policy_result: RiskPolicyResponse,
        verdict_explanation: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """
        Generate LLM-enhanced corrective action recommendations.
        
        Args:
            snapshot: Portfolio snapshot
            policy_result: Deterministic policy evaluation result
            verdict_explanation: Verdict explanation from LLM
            
        Returns:
            Dictionary with corrective actions
        """
        try:
            if not verdict_explanation:
                return None
            
            # Prepare template inputs
            template_inputs = {
                "portfolio_id": getattr(snapshot, "portfolio_id", getattr(snapshot, "account_id", snapshot.snapshot_id)),
                "portfolio_value": float(snapshot.total_value),
                "violations": [
                    {
                        "rule_id": f"RULE-{i}",
                        "description": blocker,
                        "current_value": "Unknown",
                        "limit": "Unknown",
                        "excess": "Unknown",
                    }
                    for i, blocker in enumerate(getattr(policy_result, "blockers", []))
                ],
                "holdings": [
                    {
                        "symbol": holding.symbol,
                        "quantity": holding.quantity,
                        "value": float(holding.market_value),
                        "percentage": round(float(holding.market_value / snapshot.total_value) * 100, 2),
                    }
                    for holding in snapshot.holdings
                ],
            }
            
            # Render prompt
            template = self.templates["corrective_actions"]
            rendered = self.prompt_loader.render_template(template, template_inputs)
            
            # Invoke LLM
            response = await self.bedrock_adapter.invoke_model(
                model_id=self.llm_config.risk_agent_model,
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
                    logger.warning(f"Corrective actions validation failed: {validation_result.violations}")
                    return None
            
            # Parse response
            result = json.loads(response.content)
            
            logger.info(f"Generated corrective actions with confidence {result.get('confidence', 0.0)}")
            return result
            
        except Exception as e:
            logger.error(f"Corrective actions recommendation failed: {e}", exc_info=True)
            return None
