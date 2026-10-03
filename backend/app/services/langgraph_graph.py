"""LangGraph workflow graph definition."""

import functools
import logging
from typing import Optional

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from app.contracts.workflow import OrchestrationResponse, PortfolioRebalanceRequest
from app.persistence.memory_store import WorkflowStore
from app.tools.client import ToolClient
from app.services.langgraph_nodes import (
    apply_output_guardrails,
    assemble_recommendation,
    create_approval_artifact,
    emit_workflow_audit_event,
    initialize_context_and_trace,
    log_request_audit_event,
    persist_workflow_artifacts,
    return_response,
    validate_request,
)
from app.services.langgraph_routing import (
    route_after_guardrails,
    route_after_risk_policy,
    route_after_validation,
)
from app.services.langgraph_state import WorkflowGraphState, create_initial_state

logger = logging.getLogger(__name__)


# ============================================================================
# Graph Builder
# ============================================================================


def build_workflow_graph(
    store: Optional[WorkflowStore] = None,
    tool_client: Optional[ToolClient] = None,
) -> CompiledStateGraph:
    """
    Build LangGraph workflow with all nodes and edges.

    Args:
        store: Optional persistence store for operational records and audit events
        tool_client: Optional tool client for calculations and remote persistence

    Returns:
        Compiled StateGraph ready for execution
    """
    effective_tool_client = tool_client or ToolClient(store=store)
    graph = StateGraph(WorkflowGraphState)

    # ========================================================================
    # Add Nodes
    # ========================================================================

    # Validation and initialization
    graph.add_node("validate_request", validate_request)
    graph.add_node("initialize_context_and_trace", initialize_context_and_trace)
    graph.add_node(
        "log_request_audit_event",
        functools.partial(log_request_audit_event, store=store, tool_client=effective_tool_client),
    )

    # Agent execution nodes
    graph.add_node("hydrate_memory", hydrate_memory_node)
    graph.add_node("run_research", _placeholder_research_node)
    graph.add_node("run_sentiment_analysis", _placeholder_sentiment_node)
    graph.add_node(
        "run_portfolio_rebalancing",
        functools.partial(_placeholder_rebalancing_node, tool_client=effective_tool_client),
    )
    graph.add_node(
        "run_risk_policy",
        functools.partial(_placeholder_risk_node, tool_client=effective_tool_client),
    )
    graph.add_node(
        "generate_execution_proposal",
        functools.partial(_placeholder_trade_proposal_node, tool_client=effective_tool_client),
    )

    # Output processing
    graph.add_node("assemble_recommendation", assemble_recommendation)
    graph.add_node("apply_output_guardrails", apply_output_guardrails)
    graph.add_node("create_approval_artifact", create_approval_artifact)
    graph.add_node(
        "persist_workflow_artifacts",
        functools.partial(persist_workflow_artifacts, store=store, tool_client=effective_tool_client),
    )
    graph.add_node(
        "emit_workflow_audit_event",
        functools.partial(emit_workflow_audit_event, store=store, tool_client=effective_tool_client),
    )
    graph.add_node("return_response", return_response)

    # ========================================================================
    # Set Entry Point
    # ========================================================================
    graph.set_entry_point("validate_request")

    # ========================================================================
    # Add Edges
    # ========================================================================

    # Validation flow with conditional routing
    graph.add_conditional_edges(
        "validate_request",
        route_after_validation,
        {
            "emit_workflow_audit_event": "emit_workflow_audit_event",
            "initialize_context_and_trace": "initialize_context_and_trace",
        },
    )

    # Initialization flow
    graph.add_edge("initialize_context_and_trace", "log_request_audit_event")

    # Parallel fan-out: memory and research run in parallel
    graph.add_edge("log_request_audit_event", "hydrate_memory")
    graph.add_edge("log_request_audit_event", "run_research")

    # Both fan into sentiment analysis
    graph.add_edge("hydrate_memory", "run_sentiment_analysis")
    graph.add_edge("run_research", "run_sentiment_analysis")

    # Sequential agent execution
    graph.add_edge("run_sentiment_analysis", "run_portfolio_rebalancing")
    graph.add_edge("run_portfolio_rebalancing", "run_risk_policy")

    # Conditional routing after risk policy
    graph.add_conditional_edges(
        "run_risk_policy",
        route_after_risk_policy,
        {
            "generate_execution_proposal": "generate_execution_proposal",
            "assemble_recommendation": "assemble_recommendation",
        },
    )

    # Trade proposal to recommendation assembly
    graph.add_edge("generate_execution_proposal", "assemble_recommendation")

    # Recommendation assembly to guardrails
    graph.add_edge("assemble_recommendation", "apply_output_guardrails")

    # Conditional routing after guardrails
    graph.add_conditional_edges(
        "apply_output_guardrails",
        route_after_guardrails,
        {
            "create_approval_artifact": "create_approval_artifact",
            "emit_workflow_audit_event": "emit_workflow_audit_event",
        },
    )

    # Approval artifact to persistence
    graph.add_edge("create_approval_artifact", "persist_workflow_artifacts")

    # Persistence to audit
    graph.add_edge("persist_workflow_artifacts", "emit_workflow_audit_event")

    # Audit to response
    graph.add_edge("emit_workflow_audit_event", "return_response")

    # Response is terminal
    graph.add_edge("return_response", END)

    # ========================================================================
    # Compile Graph
    # ========================================================================
    return graph.compile()


# ============================================================================
# Agent Nodes
# ============================================================================


async def hydrate_memory_node(state: WorkflowGraphState) -> dict:
    """Execute Memory Agent to retrieve and synthesize client context."""
    logger.info(f"Executing Memory Agent for {state['request_id']}")

    from app.adapters.model_factory import get_model_adapter
    from app.adapters.prompts import PromptTemplateLoader
    from app.adapters.validation import ResponseValidator
    from app.agents.memory import MemoryPersonalizationAgent
    from app.core.config import get_feature_flags, get_settings

    try:
        settings = get_settings()
        if settings.memory_mode == "memory_bank":
            from app.adapters.memory import MemoryBankAdapter

            memory_adapter = MemoryBankAdapter(
                project=settings.project_id,
                location=settings.gcp_location,
                runtime_name=settings.agent_runtime_resource_name or None,
            )
        else:
            from app.adapters.memory import LocalMemoryAdapter

            memory_adapter = LocalMemoryAdapter()

        feature_flags = get_feature_flags()
        if feature_flags.memory_agent_llm_enabled:
            agent = MemoryPersonalizationAgent(
                memory_adapter=memory_adapter,
                bedrock_adapter=get_model_adapter(),
                prompt_loader=PromptTemplateLoader(),
                validator=ResponseValidator(),
            )
        else:
            agent = MemoryPersonalizationAgent(memory_adapter=memory_adapter)

        stage_result, payload = await agent.run(state["request"])
        return {
            "memory_output": payload,
            "agent_stages": [stage_result],
        }

    except Exception as e:
        logger.error(f"Memory Agent failed: {e}", exc_info=True)
        from app.agents.base import failed_stage

        stage = failed_stage("Memory Agent", f"Memory retrieval failed: {str(e)}")
        return {
            "memory_output": {"items": [], "conflicts": []},
            "agent_stages": [stage],
        }


async def _placeholder_research_node(state: WorkflowGraphState) -> dict:
    """Execute Research Agent (remote A2A with local fallback)."""
    logger.info(f"Research agent for {state['request_id']}")

    from app.agents.research import ResearchAgent

    try:
        agent = ResearchAgent()
        stage_result, payload = await agent.run(state["request"])
        return {
            "research_output": payload,
            "agent_stages": [stage_result],
        }
    except Exception as e:
        logger.error(f"Research agent failed: {e}", exc_info=True)
        from app.agents.base import failed_stage

        stage = failed_stage("Research Agent", f"Research failed: {str(e)}")
        return {
            "research_output": {"market_context": "Research unavailable.", "key_insights": []},
            "agent_stages": [stage],
        }


async def _placeholder_sentiment_node(state: WorkflowGraphState) -> dict:
    """Execute Sentiment Analysis Agent (MCP with local fallback)."""
    logger.info(f"Sentiment agent for {state['request_id']}")

    from app.agents.sentiment import SentimentAnalysisAgent

    try:
        agent = SentimentAnalysisAgent()
        stage_result, payload = await agent.run(state["request"])
        return {
            "sentiment_output": payload,
            "agent_stages": [stage_result],
        }
    except Exception as e:
        logger.error(f"Sentiment agent failed: {e}", exc_info=True)
        from app.agents.base import failed_stage

        stage = failed_stage("Sentiment Analysis Agent", f"Sentiment analysis failed: {str(e)}")
        return {
            "sentiment_output": {"overall_sentiment": "NEUTRAL", "symbol_sentiments": []},
            "agent_stages": [stage],
        }


async def _placeholder_rebalancing_node(
    state: WorkflowGraphState, tool_client: Optional[ToolClient] = None
) -> dict:
    """Execute Portfolio Rebalancing Agent with deterministic drift calculation."""
    logger.info(f"Rebalancing agent for {state['request_id']}")

    from app.adapters.model_factory import get_model_adapter
    from app.adapters.prompts import PromptTemplateLoader
    from app.adapters.validation import ResponseValidator
    from app.agents.rebalancing import PortfolioRebalancingAgent
    from app.core.config import get_feature_flags

    feature_flags = get_feature_flags()
    if feature_flags.rebalancing_agent_llm_enabled:
        agent = PortfolioRebalancingAgent(
            bedrock_adapter=get_model_adapter(),
            prompt_loader=PromptTemplateLoader(),
            validator=ResponseValidator(),
            tool_client=tool_client,
        )
    else:
        agent = PortfolioRebalancingAgent(tool_client=tool_client)
    stage_result, payload = await agent.run(state["request"])
    return {
        "rebalancing_output": payload,
        "agent_stages": [stage_result],
    }


async def _placeholder_risk_node(
    state: WorkflowGraphState, tool_client: Optional[ToolClient] = None
) -> dict:
    """Execute Risk & Compliance Agent with deterministic policy evaluation."""
    logger.info(f"Risk agent for {state['request_id']}")

    from app.adapters.model_factory import get_model_adapter
    from app.adapters.prompts import PromptTemplateLoader
    from app.adapters.validation import ResponseValidator
    from app.agents.risk_compliance import RiskComplianceAgent
    from app.core.config import get_feature_flags

    request = state["request"]
    drift = state.get("rebalancing_output", {}).get("drift", [])

    feature_flags = get_feature_flags()
    if feature_flags.risk_agent_llm_enabled:
        agent = RiskComplianceAgent(
            bedrock_adapter=get_model_adapter(),
            prompt_loader=PromptTemplateLoader(),
            validator=ResponseValidator(),
            tool_client=tool_client,
        )
    else:
        agent = RiskComplianceAgent(tool_client=tool_client)
    stage_result, result = await agent.run(
        request.portfolio_snapshot, drift, request.risk_profile
    )
    return {
        "risk_policy_output": result,
        "agent_stages": [stage_result],
    }


async def _placeholder_trade_proposal_node(
    state: WorkflowGraphState, tool_client: Optional[ToolClient] = None
) -> dict:
    """Execute Trade Execution Proposal Agent with real deterministic logic."""
    logger.info(f"Trade proposal agent for {state['request_id']}")

    from app.adapters.model_factory import get_model_adapter
    from app.adapters.prompts import PromptTemplateLoader
    from app.adapters.validation import ResponseValidator
    from app.agents.base import failed_stage
    from app.agents.trade_execution import TradeExecutionProposalAgent
    from app.contracts.analysis import ExecutionProposalResponse
    from app.core.config import get_feature_flags

    risk_policy = state.get("risk_policy_output")
    if not risk_policy:
        stage = failed_stage("Trade Execution Proposal Agent", "No risk policy output available.")
        return {
            "trade_proposal_output": ExecutionProposalResponse(proposal_status="BLOCKED"),
            "agent_stages": [stage],
        }

    try:
        feature_flags = get_feature_flags()
        request = state["request"]

        if feature_flags.trade_proposal_agent_llm_enabled:
            agent = TradeExecutionProposalAgent(
                bedrock_adapter=get_model_adapter(),
                prompt_loader=PromptTemplateLoader(),
                validator=ResponseValidator(),
                tool_client=tool_client,
            )
        else:
            agent = TradeExecutionProposalAgent(tool_client=tool_client)

        stage_result, proposal = await agent.run(
            request.portfolio_snapshot,
            risk_policy,
            memory=state.get("memory_output"),
        )
        return {
            "trade_proposal_output": proposal,
            "agent_stages": [stage_result],
        }

    except Exception as e:
        logger.error(f"Trade proposal agent failed: {e}", exc_info=True)
        stage = failed_stage("Trade Execution Proposal Agent", f"Trade proposal failed: {str(e)}")
        return {
            "trade_proposal_output": ExecutionProposalResponse(proposal_status="BLOCKED"),
            "agent_stages": [stage],
        }


# ============================================================================
# Orchestrator Class
# ============================================================================


class LangGraphOrchestrator:
    """LangGraph-based orchestrator for portfolio rebalancing workflow."""

    def __init__(
        self,
        store: Optional[WorkflowStore] = None,
        tool_client: Optional[ToolClient] = None,
    ):
        """Initialize orchestrator with compiled graph and optional persistence store / tool client."""
        self.store = store
        self.tool_client = tool_client or ToolClient(store=store)
        self.graph = build_workflow_graph(store=store, tool_client=self.tool_client)
        logger.info("LangGraph orchestrator initialized")

    async def run(self, request: PortfolioRebalanceRequest) -> OrchestrationResponse:
        """
        Execute workflow for portfolio rebalance request.

        Args:
            request: Portfolio rebalance request

        Returns:
            Orchestration response with recommendation and approval artifact
        """
        logger.info(f"Starting workflow for request {request.correlation.request_id}")

        initial_state = create_initial_state(request)
        final_state = await self.graph.ainvoke(initial_state)

        response = OrchestrationResponse(
            correlation=request.correlation,
            version=request.version,
            workflow_state=final_state.get("workflow_state"),
            recommendation_package=final_state.get("recommendation_package"),
            approval_artifact=final_state.get("approval_artifact"),
            research_output=final_state.get("research_output"),
            sentiment_output=final_state.get("sentiment_output"),
        )

        logger.info(
            f"Workflow completed for {request.correlation.request_id} "
            f"with state {response.workflow_state}"
        )

        return response
