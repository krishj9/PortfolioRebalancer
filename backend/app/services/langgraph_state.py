import operator
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, TypedDict

from app.contracts.analysis import (
    AgentStageResult,
    ApprovalArtifact,
    ExecutionProposalResponse,
    RecommendationPackage,
    RiskPolicyResponse,
)
from app.contracts.common import WorkflowState
from app.contracts.workflow import PortfolioRebalanceRequest


# ============================================================================
# Workflow Graph State
# ============================================================================


class WorkflowGraphState(TypedDict, total=False):
    """
    State object for LangGraph workflow.

    This maintains all workflow state including correlation metadata,
    agent outputs, quality indicators, and final recommendation.
    """

    # ========================================================================
    # Correlation and Versioning
    # ========================================================================
    request_id: str
    session_id: str
    trace_id: str
    schema_version: str
    agent_version_set: str
    policy_version: str
    environment: str

    # ========================================================================
    # Tracing
    # ========================================================================
    trace_provider: Literal["bedrock_agentcore", "langsmith", "gcp_cloud_trace"]
    provider_trace_url: Optional[str]

    # ========================================================================
    # Input
    # ========================================================================
    request: PortfolioRebalanceRequest
    user_role_context: dict

    # ========================================================================
    # Validation
    # ========================================================================
    validation_error: Optional[dict]

    # ========================================================================
    # Agent Outputs
    # ========================================================================
    memory_output: Optional[dict]
    research_output: Optional[dict]
    sentiment_output: Optional[dict]
    rebalancing_output: Optional[dict]
    risk_policy_output: Optional[RiskPolicyResponse]
    trade_proposal_output: Optional[Any]
    guardrail_result: Optional[dict]
    approval_artifact: Optional[ApprovalArtifact]

    # ========================================================================
    # Agent Stage Results (for UI display)
    # ========================================================================
    agent_stages: Annotated[list[AgentStageResult], operator.add]

    # ========================================================================
    # Quality Indicators
    # ========================================================================
    confidence_map: dict[str, float]
    degraded_reasons: Annotated[list[str], operator.add]
    blockers: Annotated[list[str], operator.add]

    # ========================================================================
    # Final Output
    # ========================================================================
    recommendation_package: Optional[RecommendationPackage]
    workflow_state: WorkflowState
    audit_event_ids: Annotated[list[str], operator.add]

    # ========================================================================
    # Error Handling
    # ========================================================================
    error: Optional[dict]
    error_stage: Optional[str]


# Materialize __annotations__ as a plain dict to ensure cross-environment serialization
# in Python 3.14 (PEP 649 deferred annotations) across macOS and Linux reasoning engines.
WorkflowGraphState.__annotations__ = dict(WorkflowGraphState.__annotations__)



# ============================================================================
# State Initialization
# ============================================================================


def create_initial_state(request: PortfolioRebalanceRequest) -> WorkflowGraphState:
    """
    Create initial workflow state from request.

    Args:
        request: Portfolio rebalance request

    Returns:
        Initial workflow graph state
    """
    return WorkflowGraphState(
        # Correlation
        request_id=request.correlation.request_id,
        session_id=request.correlation.session_id,
        trace_id=request.correlation.trace_id or request.correlation.request_id,
        schema_version=request.version.schema_version,
        agent_version_set="1.0.0",  # TODO: Make configurable
        policy_version="1.0.0",  # TODO: Make configurable
        environment=request.version.environment,
        # Tracing
        trace_provider="bedrock_agentcore",  # TODO: Make configurable
        provider_trace_url=None,
        # Input
        request=request,
        user_role_context={"actor_id": request.actor.actor_id, "role": request.actor.role},
        # Validation
        validation_error=None,
        # Agent outputs
        memory_output=None,
        research_output=None,
        sentiment_output=None,
        rebalancing_output=None,
        risk_policy_output=None,
        trade_proposal_output=None,
        guardrail_result=None,
        approval_artifact=None,
        # Agent stages
        agent_stages=[],
        # Quality indicators
        confidence_map={},
        degraded_reasons=[],
        blockers=[],
        # Final output
        recommendation_package=None,
        workflow_state=WorkflowState.NORMAL,
        audit_event_ids=[],
        # Error handling
        error=None,
        error_stage=None,
    )


# ============================================================================
# State Update Helpers
# ============================================================================


def add_agent_stage(state: WorkflowGraphState, stage: AgentStageResult) -> None:
    """Add agent stage result to state."""
    if "agent_stages" not in state:
        state["agent_stages"] = []
    state["agent_stages"].append(stage)


def add_degraded_reason(state: WorkflowGraphState, reason: str) -> None:
    """Add degraded reason to state."""
    if "degraded_reasons" not in state:
        state["degraded_reasons"] = []
    if reason not in state["degraded_reasons"]:
        state["degraded_reasons"].append(reason)


def add_blocker(state: WorkflowGraphState, blocker: str) -> None:
    """Add blocker to state."""
    if "blockers" not in state:
        state["blockers"] = []
    if blocker not in state["blockers"]:
        state["blockers"].append(blocker)


def set_confidence(state: WorkflowGraphState, agent_name: str, confidence: float) -> None:
    """Set confidence score for agent."""
    if "confidence_map" not in state:
        state["confidence_map"] = {}
    state["confidence_map"][agent_name] = confidence


def add_audit_event_id(state: WorkflowGraphState, event_id: str) -> None:
    """Add audit event ID to state."""
    if "audit_event_ids" not in state:
        state["audit_event_ids"] = []
    state["audit_event_ids"].append(event_id)
