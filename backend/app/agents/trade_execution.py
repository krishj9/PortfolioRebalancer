"""Trade Execution Proposal Agent with LLM-enhanced rationale."""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.adapters.bedrock import BedrockModelAdapter
from app.adapters.prompts import PromptTemplateLoader
from app.adapters.validation import ResponseValidator
from app.agents.base import blocked_stage, completed_stage, failed_stage
from app.contracts.analysis import AgentStageResult, ExecutionProposalResponse, RiskPolicyResponse
from app.contracts.domain import PortfolioSnapshot
from app.core.config import get_feature_flags, get_llm_config
from app.services.proposal import generate_execution_proposal

logger = logging.getLogger(__name__)


class TradeExecutionProposalAgent:
    """
    Trade Execution Proposal Agent with LLM-enhanced rationale.
    
    Features:
    - Deterministic trade proposal generation (always performed)
    - LLM-enhanced proposal rationale and impact explanation
    - Validation for allocation math consistency
    - Graceful fallback to template-based proposals
    """

    name = "Trade Execution Proposal Agent"

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
        
        # Load prompt templates if prompt loader is provided
        if self.prompt_loader:
            self._load_templates()

    def _load_templates(self) -> None:
        """Load prompt templates for Trade Proposal Agent."""
        try:
            template_path = Path("prompts/trade-proposal-agent/v1.0.0.yaml")
            self.templates = self.prompt_loader.load_from_file(template_path)
            logger.info("Loaded Trade Proposal Agent prompt templates v1.0.0")
        except Exception as e:
            logger.warning(f"Failed to load Trade Proposal Agent templates: {e}. LLM features will be disabled.")
            self.feature_flags.trade_proposal_agent_llm_enabled = False

    async def run(
        self,
        snapshot: PortfolioSnapshot,
        risk_policy: RiskPolicyResponse,
        memory: Optional[dict[str, Any]] = None,
    ) -> tuple[AgentStageResult, ExecutionProposalResponse]:
        """
        Execute trade proposal generation.
        
        Always generates deterministic trade proposal.
        If LLM is enabled, adds rationale and impact explanation.
        """
        try:
            # Always generate proposal deterministically
            if self.tool_client:
                proposal = await self.tool_client.generate_proposal(snapshot, risk_policy)
            else:
                proposal = generate_execution_proposal(snapshot, risk_policy)
            
            # Check if blocked
            if proposal.proposal_status == "BLOCKED":
                stage = blocked_stage(self.name, "Trade proposal skipped because policy blocked.")
                return stage, proposal
            
            # Add LLM-enhanced rationale if enabled
            if self.feature_flags.trade_proposal_agent_llm_enabled and self.bedrock_adapter:
                try:
                    llm_analysis = await self._generate_llm_analysis(
                        snapshot, risk_policy, proposal, memory=memory
                    )
                    if llm_analysis and llm_analysis.get("proposal_rationale"):
                        rationale = llm_analysis["proposal_rationale"]
                        logger.info(f"LLM proposal rationale: {rationale.get('proposal_summary')}")
                        if "proposal_summary" in rationale:
                            proposal.estimated_impact["proposal_summary"] = rationale["proposal_summary"]
                            proposal.estimated_impact["explanation"] = rationale["proposal_summary"]
                except Exception as e:
                    logger.error(f"LLM analysis failed: {e}", exc_info=True)
                    if not self.feature_flags.fallback_on_llm_failure:
                        raise
            
            stage = completed_stage(self.name, f"Trade proposal status: {proposal.proposal_status}.")
            return stage, proposal
            
        except Exception as e:
            logger.error(f"Trade Execution Proposal Agent failed: {e}", exc_info=True)
            return failed_stage(self.name, f"Trade proposal generation failed: {str(e)}"), ExecutionProposalResponse(
                proposal_status="BLOCKED",
                trades=[],
                estimated_impact={},
            )

    async def _generate_llm_analysis(
        self,
        snapshot: PortfolioSnapshot,
        risk_policy: RiskPolicyResponse,
        proposal: ExecutionProposalResponse,
        memory: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Generate LLM-enhanced proposal rationale and impact explanation."""
        
        # Generate proposal rationale
        proposal_rationale = await self.generate_proposal_rationale(
            snapshot, risk_policy, proposal, memory=memory
        )
        
        # Generate estimated impact explanation
        estimated_impact = await self.explain_estimated_impact(
            snapshot, proposal
        )
        
        return {
            "proposal_rationale": proposal_rationale,
            "estimated_impact": estimated_impact,
        }

    async def generate_proposal_rationale(
        self,
        snapshot: PortfolioSnapshot,
        risk_policy: RiskPolicyResponse,
        proposal: ExecutionProposalResponse,
        memory: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Generate LLM-enhanced proposal rationale.
        
        Args:
            snapshot: Portfolio snapshot
            risk_policy: Risk policy verdict
            proposal: Deterministic trade proposal
            memory: Optional retrieved memory/preferences context
            
        Returns:
            Dictionary with proposal rationale
        """
        try:
            # Extract non-authoritative presentation preferences
            user_prefs_text = "No specific presentation preferences provided."
            if memory and isinstance(memory, dict):
                items = memory.get("items", [])
                pref_facts = []
                for item in items:
                    fact = (
                        item.get("content")
                        or item.get("summary")
                        if isinstance(item, dict)
                        else getattr(item, "content", getattr(item, "summary", ""))
                    )
                    if fact:
                        pref_facts.append(f"- {fact}")
                if pref_facts:
                    user_prefs_text = "\n".join(pref_facts)

            trades_list = []
            for t in proposal.trades:
                action = t.action.value if hasattr(t.action, "value") else str(t.action)
                sym = t.symbol
                val = float(t.estimated_value) if hasattr(t, "estimated_value") else 0.0
                trades_list.append({
                    "action": action,
                    "quantity": 1,
                    "symbol": sym,
                    "price": val,
                    "current_quantity": 0,
                    "target_quantity": 1,
                })

            # Prepare template inputs
            template_inputs = {
                "portfolio_id": getattr(snapshot, "portfolio_id", getattr(snapshot, "account_id", snapshot.snapshot_id)),
                "portfolio_value": float(snapshot.total_value),
                "risk_tolerance": "MODERATE",  # TODO: Get from request
                "user_preferences": user_prefs_text,
                "drift_summary": "Portfolio drift analysis",  # TODO: Get from rebalancing agent
                "policy_verdict": risk_policy.verdict.value if hasattr(risk_policy.verdict, "value") else str(risk_policy.verdict),
                "proposed_trades": trades_list or [
                    {
                        "action": "BUY",
                        "quantity": 0,
                        "symbol": "UNKNOWN",
                        "price": 0.0,
                        "current_quantity": 0,
                        "target_quantity": 0,
                    }
                ],
            }
            
            # Render prompt
            template = self.templates["proposal_rationale"]
            rendered = self.prompt_loader.render_template(template, template_inputs)
            
            # Invoke LLM
            response = await self.bedrock_adapter.invoke_model(
                model_id=self.llm_config.trade_proposal_agent_model,
                system_prompt=rendered.system_prompt,
                prompt=rendered.user_prompt,
                temperature=0.5,
                max_tokens=1500,
            )
            
            # Validate response
            if self.validator:
                if hasattr(self.validator, "validate_response"):
                    validation_result = await self.validator.validate_response(
                        response.content,
                        template["validation"]["output_schema"],
                        confidence_threshold=template["validation"]["confidence_threshold"],
                    )
                else:
                    validation_result = await self.validator.validate(
                        response,
                        confidence_threshold=template["validation"]["confidence_threshold"],
                    )
                
                if not validation_result.is_valid:
                    logger.warning(f"Proposal rationale validation failed: {validation_result.violations}")
                    return None
            
            # Parse response
            result = json.loads(response.content)
            
            logger.info(f"Generated proposal rationale with confidence {result.get('confidence', 0.0)}")
            return result
            
        except Exception as e:
            logger.error(f"Proposal rationale generation failed: {e}", exc_info=True)
            return None

    async def explain_estimated_impact(
        self,
        snapshot: PortfolioSnapshot,
        proposal: ExecutionProposalResponse,
    ) -> Optional[dict[str, Any]]:
        """
        Generate LLM-enhanced estimated impact explanation.
        
        Args:
            snapshot: Portfolio snapshot
            proposal: Deterministic trade proposal
            
        Returns:
            Dictionary with estimated impact explanation
        """
        try:
            # Prepare template inputs
            template_inputs = {
                "portfolio_id": getattr(snapshot, "portfolio_id", getattr(snapshot, "account_id", snapshot.snapshot_id)),
                "portfolio_value": float(snapshot.total_value),
                "current_allocation": [],  # TODO: Get from rebalancing agent
                "target_allocation": [],  # TODO: Get from request
                "proposed_trades": [],  # TODO: Extract from proposal
            }
            
            # Render prompt
            template = self.templates["estimated_impact"]
            rendered = self.prompt_loader.render_template(template, template_inputs)
            
            # Invoke LLM
            response = await self.bedrock_adapter.invoke_model(
                model_id=self.llm_config.trade_proposal_agent_model,
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
                    logger.warning(f"Estimated impact validation failed: {validation_result.violations}")
                    return None
            
            # Parse response
            result = json.loads(response.content)
            
            logger.info(f"Generated estimated impact explanation with confidence {result.get('confidence', 0.0)}")
            return result
            
        except Exception as e:
            logger.error(f"Estimated impact explanation failed: {e}", exc_info=True)
            return None


# Standard aliases
TradeProposalAgent = TradeExecutionProposalAgent
TradeExecutionAgent = TradeExecutionProposalAgent
