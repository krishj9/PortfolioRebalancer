"""LangGraph workflow nodes."""

import logging
from datetime import datetime
from typing import Any, Optional

from app.contracts.analysis import (
    ApprovalArtifact,
    ExecutionProposalResponse,
    PolicyVerdictStatus,
    RecommendationPackage,
)
from app.contracts.common import WorkflowState
from app.contracts.domain import PortfolioRecord
from app.persistence.memory_store import WorkflowStore
from app.services.langgraph_state import WorkflowGraphState

logger = logging.getLogger(__name__)


# ============================================================================
# Validation and Initialization Nodes
# ============================================================================


async def validate_request(state: WorkflowGraphState) -> dict:
    """
    Validate request schema and business rules.

    Args:
        state: Current workflow state

    Returns:
        Delta dictionary with validation results
    """
    logger.info(f"Validating request {state['request_id']}")

    request = state["request"]
    errors = []

    # Validate allocation targets sum to 100% (support both 100-scale and 1.0-scale)
    if hasattr(request, "allocation_target"):
        total = sum(request.allocation_target.asset_class_targets.values())
        total_flt = float(total)
        if abs(total_flt - 100.0) > 1.0 and abs(total_flt - 1.0) > 0.01:
            errors.append(f"Allocation targets sum to {total_flt}, expected 100%")

    # Validate holdings have positive quantities
    if hasattr(request, "portfolio_snapshot"):
        for holding in request.portfolio_snapshot.holdings:
            if holding.quantity <= 0:
                errors.append(f"Invalid quantity for {holding.symbol}: {holding.quantity}")

    if errors:
        return {
            "validation_error": {"errors": errors, "timestamp": datetime.now().isoformat()},
            "workflow_state": WorkflowState.BLOCKED,
            "blockers": ["Request validation failed"],
        }

    return {}


async def initialize_context_and_trace(state: WorkflowGraphState) -> dict:
    """
    Initialize tracing context and correlation metadata.

    Args:
        state: Current workflow state

    Returns:
        Delta dictionary with trace context
    """
    logger.info(f"Initializing trace context for {state['request_id']}")

    trace_provider = state.get("trace_provider", "bedrock_agentcore")
    provider_trace_url = None

    if trace_provider == "bedrock_agentcore":
        provider_trace_url = (
            f"https://console.aws.amazon.com/cloudwatch/home?"
            f"region=us-east-1#logsV2:logs-insights?queryDetail=~(source~'{state['trace_id']}')"
        )
    elif trace_provider == "langsmith":
        provider_trace_url = (
            f"https://smith.langchain.com/o/org/projects/p/project/r/{state['trace_id']}"
        )
    elif trace_provider == "gcp_cloud_trace":
        provider_trace_url = (
            f"https://console.cloud.google.com/traces/list?project=mybrightday-dev&tid={state['trace_id']}"
        )

    logger.info(f"Trace URL: {provider_trace_url}")

    return {"provider_trace_url": provider_trace_url}


async def log_request_audit_event(
    state: WorkflowGraphState,
    store: Optional[WorkflowStore] = None,
    tool_client: Optional[Any] = None,
) -> dict:
    """
    Log request received audit event and save portfolio.

    Args:
        state: Current workflow state
        store: Optional injected persistence store
        tool_client: Optional injected tool client

    Returns:
        Delta dictionary with audit event ID
    """
    logger.info(f"Logging audit event for {state['request_id']}")

    request = state["request"]
    event_id = f"audit-{state['request_id']}-request-received"

    if tool_client is not None:
        await tool_client.audit(
            event_type="REQUEST_RECEIVED",
            correlation=request.correlation,
            actor_id=request.actor.actor_id,
            outcome="ACCEPTED" if not state.get("validation_error") else "REJECTED",
        )
    elif store is not None:
        store.add_audit_event(
            event_type="REQUEST_RECEIVED",
            correlation=request.correlation,
            actor_id=request.actor.actor_id,
            outcome="ACCEPTED" if not state.get("validation_error") else "REJECTED",
        )

    if store is not None:
        store.save_portfolio(
            PortfolioRecord(
                client_profile=request.client_profile,
                account_profile=request.account_profile,
                portfolio_snapshot=request.portfolio_snapshot,
                allocation_target=request.allocation_target,
                risk_profile=request.risk_profile,
                updated_at=request.portfolio_snapshot.as_of,
                source="rebalance_request",
            )
        )

    return {"audit_event_ids": [event_id]}


# ============================================================================
# Output Processing Nodes
# ============================================================================


async def apply_output_guardrails(state: WorkflowGraphState) -> dict:
    """
    Apply guardrails to recommendation output.

    Args:
        state: Current workflow state

    Returns:
        Delta dictionary with guardrail results
    """
    logger.info(f"Applying guardrails for {state['request_id']}")

    guardrail_result = {
        "action": "NONE",
        "assessments": [],
        "timestamp": datetime.now().isoformat(),
    }

    recommendation = state.get("recommendation_package")
    if recommendation:
        summary = recommendation.summary.lower()
        sensitive_keywords = ["password", "ssn", "credit card"]
        if any(keyword in summary for keyword in sensitive_keywords):
            guardrail_result["action"] = "BLOCKED"
            guardrail_result["assessments"].append(
                {"type": "SENSITIVE_INFORMATION", "action": "BLOCKED"}
            )

    if guardrail_result["action"] == "BLOCKED":
        return {
            "guardrail_result": guardrail_result,
            "workflow_state": WorkflowState.BLOCKED,
            "blockers": ["GUARDRAIL_VIOLATION"],
        }

    return {"guardrail_result": guardrail_result}


async def assemble_recommendation(state: WorkflowGraphState) -> dict:
    """
    Assemble final recommendation package from agent outputs.

    Args:
        state: Current workflow state

    Returns:
        Delta dictionary with recommendation package and workflow state
    """
    logger.info(f"Assembling recommendation for {state['request_id']}")

    request = state["request"]
    rebalancing = state.get("rebalancing_output", {})
    risk_policy = state.get("risk_policy_output")
    raw_proposal = state.get("trade_proposal_output")

    if isinstance(raw_proposal, ExecutionProposalResponse):
        proposal = raw_proposal
    elif isinstance(raw_proposal, dict):
        proposal = ExecutionProposalResponse.model_validate(raw_proposal)
    else:
        proposal = ExecutionProposalResponse(proposal_status="UNKNOWN")

    current_allocation = rebalancing.get("current_allocation", {})
    target_allocation = request.allocation_target.asset_class_targets
    proposed_allocation = proposal.estimated_impact or current_allocation

    # Determine workflow state and approval eligibility
    workflow_state = state.get("workflow_state", WorkflowState.NORMAL)
    approval_eligibility = proposal.proposal_status in {"READY_FOR_REVIEW", "NO_ACTION_NEEDED"}

    if proposal.proposal_status == "BLOCKED" or state.get("blockers"):
        workflow_state = WorkflowState.BLOCKED
        approval_eligibility = False
    elif proposal.proposal_status == "NO_ACTION_NEEDED":
        workflow_state = WorkflowState.LOW_CONFIDENCE
    elif state.get("degraded_reasons"):
        workflow_state = WorkflowState.DEGRADED

    # Create recommendation package
    recommendation = RecommendationPackage(
        summary=_generate_summary(state, proposal.proposal_status),
        agent_stages=list(state.get("agent_stages", [])),
        current_allocation=current_allocation,
        target_allocation=target_allocation,
        proposed_allocation=proposed_allocation,
        risk_policy=risk_policy,
        proposal=proposal,
        workflow_state=workflow_state,
        approval_eligibility=approval_eligibility,
        evidence=risk_policy.evidence if risk_policy else [],
        sentiment_output=state.get("sentiment_output"),
        research_output=state.get("research_output"),
    )

    return {
        "recommendation_package": recommendation,
        "workflow_state": workflow_state,
    }


async def create_approval_artifact(state: WorkflowGraphState) -> dict:
    """
    Create approval artifact for human review using HumanApprovalWorkflowAgent.

    Args:
        state: Current workflow state

    Returns:
        Delta dictionary with approval artifact and stage result
    """
    logger.info(f"Creating approval artifact for {state['request_id']}")

    from app.agents.human_approval import HumanApprovalWorkflowAgent

    recommendation = state.get("recommendation_package")
    request = state["request"]

    approval_agent = HumanApprovalWorkflowAgent()
    approval_stage, approval = await approval_agent.run(
        request.correlation, recommendation, request
    )
    approval.recommendation = recommendation

    return {
        "approval_artifact": approval,
        "agent_stages": [approval_stage],
    }


async def persist_workflow_artifacts(
    state: WorkflowGraphState,
    store: Optional[WorkflowStore] = None,
    tool_client: Optional[Any] = None,
) -> dict:
    """
    Persist workflow artifacts to storage.

    Args:
        state: Current workflow state
        store: Optional injected persistence store
        tool_client: Optional injected tool client

    Returns:
        Delta dictionary with persisted approval artifact
    """
    logger.info(f"Persisting artifacts for {state['request_id']}")

    approval = state.get("approval_artifact")
    if approval is not None:
        if tool_client is not None:
            persisted = await tool_client.persist_proposal(approval)
            return {"approval_artifact": persisted}
        elif store is not None:
            persisted = store.save_approval(approval)
            return {"approval_artifact": persisted}

    return {}


async def emit_workflow_audit_event(
    state: WorkflowGraphState,
    store: Optional[WorkflowStore] = None,
    tool_client: Optional[Any] = None,
) -> dict:
    """
    Emit final workflow audit event.

    Args:
        state: Current workflow state
        store: Optional injected persistence store
        tool_client: Optional injected tool client

    Returns:
        Delta dictionary with audit event ID
    """
    logger.info(f"Emitting workflow audit event for {state['request_id']}")

    request = state["request"]
    approval = state.get("approval_artifact")
    event_id = f"audit-{state['request_id']}-workflow-completed"

    if approval is not None:
        if tool_client is not None:
            await tool_client.audit(
                event_type="APPROVAL_ARTIFACT_CREATED",
                correlation=request.correlation,
                actor_id=request.actor.actor_id,
                outcome=approval.approval_status,
                details={"approval_id": approval.approval_id},
            )
        elif store is not None:
            store.add_audit_event(
                event_type="APPROVAL_ARTIFACT_CREATED",
                correlation=request.correlation,
                actor_id=request.actor.actor_id,
                outcome=approval.approval_status,
                details={"approval_id": approval.approval_id},
            )

    return {"audit_event_ids": [event_id]}


async def return_response(state: WorkflowGraphState) -> dict:
    """
    Prepare final response (terminal node).

    Args:
        state: Current workflow state

    Returns:
        Delta dictionary (empty)
    """
    logger.info(f"Returning response for {state['request_id']}")
    return {}


# ============================================================================
# Helper Functions
# ============================================================================


def _generate_summary(state: WorkflowGraphState, proposal_status: Optional[str] = None) -> str:
    """Generate summary based on workflow state, incorporating sentiment signals."""
    if state.get("blockers"):
        return f"Recommendation is blocked: {', '.join(state['blockers'])}"

    status = proposal_status or "UNKNOWN"
    if status == "BLOCKED":
        return "Recommendation is blocked by deterministic policy checks."
    elif status == "NO_ACTION_NEEDED":
        return "Portfolio is already within configured allocation tolerances."
    elif state.get("degraded_reasons"):
        return f"Recommendation generated with degraded quality: {', '.join(state['degraded_reasons'])}"

    # Base summary for READY_FOR_REVIEW
    base = "Portfolio has drift outside tolerance and is ready for manual review."

    # Append sentiment context if available and relevant
    sentiment_notes = _sentiment_notes(state)
    if sentiment_notes:
        return f"{base} {sentiment_notes}"

    return base


def _sentiment_notes(state: WorkflowGraphState) -> str:
    """Build sentiment context notes for recommendation summary."""
    sentiment_output = state.get("sentiment_output")
    trade_proposal = state.get("trade_proposal_output")

    if not sentiment_output:
        return ""

    symbol_sentiments: dict[str, dict] = {}
    for item in sentiment_output.get("symbol_sentiments", []):
        sym = item.get("symbol", "").upper()
        if sym:
            symbol_sentiments[sym] = item

    overall = sentiment_output.get("overall_sentiment", "NEUTRAL")

    if isinstance(trade_proposal, ExecutionProposalResponse):
        trades = trade_proposal.trades
    elif isinstance(trade_proposal, dict):
        trades = trade_proposal.get("trades", [])
    else:
        trades = []

    if not trades:
        if overall in ("POSITIVE", "NEGATIVE"):
            return f"Market sentiment is currently {overall.lower()} across monitored asset classes."
        return ""

    notes = []
    for trade in trades:
        symbol = (trade.symbol if hasattr(trade, "symbol") else trade.get("symbol", "")).upper()
        action = (
            trade.action.value
            if hasattr(trade, "action") and hasattr(trade.action, "value")
            else str(trade.get("action", "") if isinstance(trade, dict) else trade.action)
        ).upper()
        sentiment = symbol_sentiments.get(symbol, {})
        sent_label = sentiment.get("overall_sentiment", overall)

        if action == "SELL" and sent_label == "POSITIVE":
            notes.append(
                f"Sentiment for {symbol} is positive — this sell is driven by allocation drift, "
                f"not market weakness."
            )
        elif action == "BUY" and sent_label == "NEGATIVE":
            notes.append(
                f"Sentiment for {symbol} is currently negative — consider timing this buy carefully."
            )
        elif action == "BUY" and sent_label == "POSITIVE":
            notes.append(f"Sentiment for {symbol} is positive, supporting this buy.")

    if notes:
        return "Sentiment context: " + " ".join(notes)

    if overall in ("POSITIVE", "NEGATIVE", "MIXED"):
        return f"Overall market sentiment: {overall.lower()}."

    return ""
