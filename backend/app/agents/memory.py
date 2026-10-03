"""Memory Agent with LLM-enhanced semantic retrieval and synthesis."""

import logging
from pathlib import Path
from typing import Any, Optional

from app.adapters.bedrock import BedrockModelAdapter, ModelInvocationError
from app.adapters.memory import LocalMemoryAdapter
from app.adapters.prompts import PromptTemplateLoader
from app.adapters.validation import ResponseValidator
from app.agents.base import completed_stage, failed_stage
from app.contracts.analysis import AgentStageResult
from app.contracts.workflow import PortfolioRebalanceRequest
from app.core.config import get_feature_flags, get_llm_config

logger = logging.getLogger(__name__)


class MemoryPersonalizationAgent:
    """
    Memory Agent with LLM-enhanced capabilities.
    
    Features:
    - Semantic query generation for better memory retrieval
    - Memory synthesis for coherent context
    - Conflict detection for identifying inconsistencies
    - Graceful fallback to keyword-based retrieval
    """

    name = "Memory / Personalization Agent"

    def __init__(
        self,
        memory_adapter: LocalMemoryAdapter | None = None,
        bedrock_adapter: BedrockModelAdapter | None = None,
        prompt_loader: PromptTemplateLoader | None = None,
        validator: ResponseValidator | None = None,
    ) -> None:
        self.memory_adapter = memory_adapter or LocalMemoryAdapter()
        self.bedrock_adapter = bedrock_adapter
        self.prompt_loader = prompt_loader
        self.validator = validator
        
        # Load configuration
        self.feature_flags = get_feature_flags()
        self.llm_config = get_llm_config()
        
        # Load prompt templates if LLM is enabled
        if self.feature_flags.memory_agent_llm_enabled and self.prompt_loader:
            self._load_templates()

    def _load_templates(self) -> None:
        """Load prompt templates for Memory Agent."""
        try:
            template_path = Path("prompts/memory-agent/v1.0.0.yaml")
            self.templates = self.prompt_loader.load_from_file(template_path)
            logger.info("Loaded Memory Agent prompt templates v1.0.0")
        except Exception as e:
            logger.warning(f"Failed to load Memory Agent templates: {e}. LLM features will be disabled.")
            self.feature_flags.memory_agent_llm_enabled = False

    async def run(self, request: PortfolioRebalanceRequest) -> tuple[AgentStageResult, dict]:
        """
        Execute memory retrieval and synthesis.
        
        If LLM is enabled:
        1. Generate semantic query using LLM
        2. Retrieve memories using semantic search
        3. Synthesize memories into coherent context
        4. Detect conflicts in memories
        
        If LLM is disabled or fails:
        - Fall back to keyword-based retrieval
        - Return raw memories without synthesis
        """
        try:
            # Check if LLM is enabled
            if self.feature_flags.memory_agent_llm_enabled and self.bedrock_adapter:
                return await self._run_with_llm(request)
            else:
                return await self._run_deterministic(request)
        except Exception as e:
            logger.error(f"Memory Agent failed: {e}", exc_info=True)
            
            # Fall back to deterministic if enabled
            if self.feature_flags.fallback_on_llm_failure:
                logger.info("Falling back to deterministic memory retrieval")
                return await self._run_deterministic(request)
            else:
                return failed_stage(self.name, f"Memory retrieval failed: {str(e)}"), {}

    async def _run_deterministic(self, request: PortfolioRebalanceRequest) -> tuple[AgentStageResult, dict]:
        """Deterministic memory retrieval (original implementation)."""
        user_id = (
            request.actor.actor_id
            if (request.actor and request.actor.actor_id)
            else request.client_profile.client_id
        )
        memories = await self.memory_adapter.retrieve(user_id)
        payload = {"items": [memory.__dict__ for memory in memories], "conflicts": []}
        return completed_stage(self.name, f"Retrieved {len(memories)} memory item(s)."), payload

    async def _run_with_llm(self, request: PortfolioRebalanceRequest) -> tuple[AgentStageResult, dict]:
        """LLM-enhanced memory retrieval and synthesis."""
        try:
            # Step 1: Generate semantic query
            semantic_query_result = await self.generate_semantic_query(request)

            if not semantic_query_result:
                logger.warning("Semantic query generation failed, falling back to deterministic")
                return await self._run_deterministic(request)

            # Step 2: Retrieve memories (using semantic query or keywords)
            user_id = (
                request.actor.actor_id
                if (request.actor and request.actor.actor_id)
                else request.client_profile.client_id
            )
            memories = await self.memory_adapter.retrieve(
                user_id,
                semantic_query=semantic_query_result.get("semantic_query"),
                keywords=semantic_query_result.get("keywords"),
            )
            
            if not memories:
                return completed_stage(self.name, "No relevant memories found."), {
                    "items": [],
                    "conflicts": [],
                    "synthesis": None,
                }
            
            # Step 3: Synthesize memories
            synthesis_result = await self.synthesize_memory_items(memories, request)
            
            # Step 4: Detect conflicts
            conflicts_result = await self.detect_conflicts(memories)
            
            # Build payload
            payload = {
                "items": [memory.__dict__ for memory in memories],
                "synthesis": synthesis_result,
                "conflicts": conflicts_result.get("conflicts", []) if conflicts_result else [],
                "semantic_query": semantic_query_result,
            }
            
            # Build summary message
            summary_parts = [f"Retrieved {len(memories)} memory item(s)"]
            if synthesis_result:
                summary_parts.append("synthesized context")
            if conflicts_result and conflicts_result.get("conflicts_detected"):
                summary_parts.append(f"{len(conflicts_result['conflicts'])} conflict(s) detected")
            
            return completed_stage(self.name, ", ".join(summary_parts) + "."), payload
            
        except Exception as e:
            logger.error(f"LLM-enhanced memory retrieval failed: {e}", exc_info=True)
            
            # Fall back to deterministic
            if self.feature_flags.fallback_on_llm_failure:
                logger.info("Falling back to deterministic memory retrieval")
                return await self._run_deterministic(request)
            else:
                raise

    async def generate_semantic_query(self, request: PortfolioRebalanceRequest) -> Optional[dict[str, Any]]:
        """
        Generate semantic query for memory retrieval using LLM.
        
        Args:
            request: Portfolio rebalance request
            
        Returns:
            Dictionary with semantic_query, keywords, and confidence
        """
        try:
            # Render prompt
            template = self.templates["semantic_query"]
            rendered = self.prompt_loader.render_template(
                template,
                {
                    "client_id": request.client_profile.client_id,
                    "risk_tolerance": getattr(request.risk_profile, "risk_level", getattr(request.client_profile, "risk_tolerance", "MODERATE")),
                    "investment_horizon": getattr(request.client_profile, "investment_horizon", "LONG_TERM"),
                    "constraints": ", ".join(getattr(request.client_profile, "constraints", []) or []),
                    "portfolio_id": getattr(request, "portfolio_id", getattr(request.account_profile, "account_id", "PORT-001")),
                    "trigger_type": getattr(request, "trigger_type", "rebalance"),
                    "request_context": f"Portfolio rebalancing triggered by {getattr(request, 'trigger_type', 'rebalance')}",
                },
            )
            
            # Invoke LLM
            response = await self.bedrock_adapter.invoke_model(
                model_id=self.llm_config.memory_agent_model,
                system_prompt=rendered.system_prompt,
                prompt=rendered.user_prompt,
                temperature=0.3,  # Low temperature for consistent query generation
                max_tokens=500,
            )
            
            # Validate response
            if self.validator:
                validation_result = await self.validator.validate_response(
                    response.content,
                    template["validation"]["output_schema"],
                    confidence_threshold=template["validation"]["confidence_threshold"],
                )
                
                if not validation_result.is_valid:
                    logger.warning(f"Semantic query validation failed: {validation_result.violations}")
                    return None
            
            # Parse response
            import json
            result = json.loads(response.content)
            
            logger.info(f"Generated semantic query with confidence {result.get('confidence', 0.0)}")
            return result
            
        except Exception as e:
            logger.error(f"Semantic query generation failed: {e}", exc_info=True)
            return None

    async def synthesize_memory_items(
        self, memories: list[Any], request: PortfolioRebalanceRequest
    ) -> Optional[dict[str, Any]]:
        """
        Synthesize memory items into coherent context using LLM.
        
        Args:
            memories: List of retrieved memory items
            request: Portfolio rebalance request
            
        Returns:
            Dictionary with summary, key_preferences, historical_patterns, relevant_decisions, and confidence
        """
        try:
            # Prepare memory items for template
            memory_items = [
                {
                    "timestamp": getattr(memory, "timestamp", "Unknown"),
                    "memory_type": getattr(memory, "memory_type", "UNKNOWN"),
                    "content": getattr(memory, "content", str(memory)),
                    "relevance_score": getattr(memory, "relevance_score", 0.0),
                }
                for memory in memories
            ]
            
            # Render prompt
            template = self.templates["memory_synthesis"]
            rendered = self.prompt_loader.render_template(
                template,
                {
                    "memory_items": memory_items,
                    "client_id": request.client_profile.client_id,
                    "portfolio_id": request.portfolio_id,
                    "trigger_type": request.trigger_type,
                },
            )
            
            # Invoke LLM
            response = await self.bedrock_adapter.invoke_model(
                model_id=self.llm_config.memory_agent_model,
                system_prompt=rendered.system_prompt,
                prompt=rendered.user_prompt,
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
                    logger.warning(f"Memory synthesis validation failed: {validation_result.violations}")
                    return None
            
            # Parse response
            import json
            result = json.loads(response.content)
            
            logger.info(f"Synthesized {len(memories)} memories with confidence {result.get('confidence', 0.0)}")
            return result
            
        except Exception as e:
            logger.error(f"Memory synthesis failed: {e}", exc_info=True)
            return None

    async def detect_conflicts(self, memories: list[Any]) -> Optional[dict[str, Any]]:
        """
        Detect conflicts or inconsistencies in memory items using LLM.
        
        Args:
            memories: List of retrieved memory items
            
        Returns:
            Dictionary with conflicts_detected, conflicts list, and confidence
        """
        try:
            # Prepare memory items for template
            memory_items = [
                {
                    "memory_id": getattr(memory, "memory_id", f"MEM-{i}"),
                    "timestamp": getattr(memory, "timestamp", "Unknown"),
                    "memory_type": getattr(memory, "memory_type", "UNKNOWN"),
                    "content": getattr(memory, "content", str(memory)),
                }
                for i, memory in enumerate(memories)
            ]
            
            # Render prompt
            template = self.templates["conflict_detection"]
            rendered = self.prompt_loader.render_template(
                template,
                {"memory_items": memory_items},
            )
            
            # Invoke LLM
            response = await self.bedrock_adapter.invoke_model(
                model_id=self.llm_config.memory_agent_model,
                system_prompt=rendered.system_prompt,
                prompt=rendered.user_prompt,
                temperature=0.3,  # Low temperature for consistent conflict detection
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
                    logger.warning(f"Conflict detection validation failed: {validation_result.violations}")
                    return None
            
            # Parse response
            import json
            result = json.loads(response.content)
            
            if result.get("conflicts_detected"):
                logger.warning(f"Detected {len(result.get('conflicts', []))} conflict(s) in memories")
            
            return result
            
        except Exception as e:
            logger.error(f"Conflict detection failed: {e}", exc_info=True)
            return None
