"""LangGraph conditional routing logic."""

import logging
from typing import Literal

from app.contracts.analysis import PolicyVerdictStatus
from app.contracts.common import WorkflowState
from app.services.langgraph_state import WorkflowGraphState

logger = logging.getLogger(__name__)


# ============================================================================
# Routing Functions
# ============================================================================


def route_after_validation(
    state: WorkflowGraphState,
) -> Literal["emit_workflow_audit_event", "initialize_context_and_trace"]:
    """
    Route based on validation result.

    If validation fails, skip to audit emission and error response.
    Otherwise, proceed with initialization.

    Args:
        state: Current workflow state

    Returns:
        Next node name
    """
    if state.get("validation_error"):
        logger.warning(
            f"Validation failed for {state['request_id']}: "
            f"{state['validation_error']}"
        )
        return "emit_workflow_audit_event"

    return "initialize_context_and_trace"


def route_after_risk_policy(
    state: WorkflowGraphState,
) -> Literal["generate_execution_proposal", "assemble_recommendation"]:
    """
    Route based on policy verdict.

    If policy is NON_COMPLIANT or UNRESOLVED, skip trade proposal generation
    and mark workflow as BLOCKED.

    Args:
        state: Current workflow state

    Returns:
        Next node name
    """
    policy = state.get("risk_policy_output")

    if not policy:
        logger.warning(f"No risk policy output for {state['request_id']}")
        return "assemble_recommendation"

    verdict = policy.verdict

    if verdict in [PolicyVerdictStatus.NON_COMPLIANT, PolicyVerdictStatus.UNRESOLVED]:
        logger.warning(
            f"Policy verdict {verdict} blocks trade proposal for {state['request_id']}"
        )
        state["workflow_state"] = WorkflowState.BLOCKED
        from app.services.langgraph_state import add_blocker
        add_blocker(state, f"Policy verdict: {verdict.value}")
        # Skip trade proposal generation
        return "assemble_recommendation"

    # Policy allows trade proposal
    return "generate_execution_proposal"


def route_after_guardrails(
    state: WorkflowGraphState,
) -> Literal["create_approval_artifact", "emit_workflow_audit_event"]:
    """
    Route based on guardrail result.

    If guardrails hard-block output, skip approval artifact creation.

    Args:
        state: Current workflow state

    Returns:
        Next node name
    """
    guardrail = state.get("guardrail_result")

    if not guardrail:
        logger.warning(f"No guardrail result for {state['request_id']}")
        return "create_approval_artifact"

    action = guardrail.get("action")

    if action == "BLOCKED":
        logger.warning(f"Guardrails blocked output for {state['request_id']}")
        state["workflow_state"] = WorkflowState.BLOCKED
        from app.services.langgraph_state import add_blocker
        add_blocker(state, "GUARDRAIL_VIOLATION")
        # Skip approval artifact creation
        return "emit_workflow_audit_event"

    # Guardrails passed
    return "create_approval_artifact"


def should_run_parallel_agents(state: WorkflowGraphState) -> bool:
    """
    Determine if parallel agent execution should proceed.

    Args:
        state: Current workflow state

    Returns:
        True if parallel agents should run
    """
    # Don't run agents if validation failed
    if state.get("validation_error"):
        return False

    # Don't run agents if already blocked
    if state.get("workflow_state") == WorkflowState.BLOCKED:
        return False

    return True


def route_to_sentiment_or_skip(
    state: WorkflowGraphState,
) -> Literal["run_sentiment_analysis", "run_portfolio_rebalancing"]:
    """
    Route to sentiment analysis or skip if parallel agents didn't run.

    Args:
        state: Current workflow state

    Returns:
        Next node name
    """
    # Check if memory and research completed
    if state.get("memory_output") is not None or state.get("research_output") is not None:
        return "run_sentiment_analysis"

    # Skip sentiment if parallel agents didn't run
    logger.warning(f"Skipping sentiment analysis for {state['request_id']}")
    return "run_portfolio_rebalancing"


# ============================================================================
# Conditional Edge Helpers
# ============================================================================


def create_conditional_edge(
    source: str, condition_fn: callable, destinations: dict[str, str]
) -> dict:
    """
    Create conditional edge configuration.

    Args:
        source: Source node name
        condition_fn: Condition function that returns destination key
        destinations: Map of condition results to destination nodes

    Returns:
        Conditional edge configuration
    """
    return {"source": source, "condition": condition_fn, "destinations": destinations}


# ============================================================================
# Routing Decision Logging
# ============================================================================


def log_routing_decision(
    state: WorkflowGraphState, source: str, destination: str, reason: str
) -> None:
    """
    Log routing decision for debugging.

    Args:
        state: Current workflow state
        source: Source node
        destination: Destination node
        reason: Reason for routing decision
    """
    logger.info(
        f"Routing decision for {state['request_id']}: "
        f"{source} -> {destination} (reason: {reason})"
    )
