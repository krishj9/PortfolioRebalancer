"""Property-based tests for LangGraph state graph."""

import pytest
from hypothesis import given, settings, strategies as st

from app.contracts.analysis import PolicyVerdictStatus, RiskPolicyResponse
from app.contracts.common import WorkflowState
from app.services.langgraph_routing import (
    route_after_guardrails,
    route_after_risk_policy,
    route_after_validation,
)
from app.services.langgraph_state import WorkflowGraphState, add_blocker


# ============================================================================
# Property 1: State Graph Conditional Routing
# ============================================================================


@given(
    verdict=st.sampled_from([PolicyVerdictStatus.NON_COMPLIANT, PolicyVerdictStatus.UNRESOLVED])
)
@settings(max_examples=100, deadline=None)
def test_property_1_conditional_routing_blocks_on_non_compliant(verdict):
    """
    Feature: llm-langgraph-integration, Property 1: State Graph Conditional Routing
    For any workflow state with NON_COMPLIANT or UNRESOLVED verdict,
    routing skips trade proposal and marks workflow BLOCKED.

    Validates: Requirements 1.2, 1.5
    """
    # Create state with policy verdict
    state = WorkflowGraphState(
        request_id="test-req",
        session_id="test-session",
        trace_id="test-trace",
        schema_version="1.0.0",
        agent_version_set="1.0.0",
        policy_version="1.0.0",
        environment="test",
        trace_provider="bedrock_agentcore",
        risk_policy_output=RiskPolicyResponse(
            verdict=verdict, drift=[]
        ),
        workflow_state=WorkflowState.NORMAL,
        blockers=[],
    )

    # Route after risk policy
    next_node = route_after_risk_policy(state)

    # Should skip trade proposal
    assert next_node == "assemble_recommendation"

    # Should mark workflow as BLOCKED
    assert state["workflow_state"] == WorkflowState.BLOCKED

    # Should add blocker
    assert len(state["blockers"]) > 0
    assert any(verdict.value in blocker for blocker in state["blockers"])


@given(verdict=st.sampled_from([PolicyVerdictStatus.COMPLIANT]))
@settings(max_examples=50, deadline=None)
def test_property_1_conditional_routing_allows_compliant(verdict):
    """
    Feature: llm-langgraph-integration, Property 1: State Graph Conditional Routing
    For any workflow state with COMPLIANT verdict, routing proceeds to trade proposal.

    Validates: Requirements 1.2, 1.5
    """
    # Create state with compliant policy verdict
    state = WorkflowGraphState(
        request_id="test-req",
        session_id="test-session",
        trace_id="test-trace",
        schema_version="1.0.0",
        agent_version_set="1.0.0",
        policy_version="1.0.0",
        environment="test",
        trace_provider="bedrock_agentcore",
        risk_policy_output=RiskPolicyResponse(
            verdict=verdict, drift=[]
        ),
        workflow_state=WorkflowState.NORMAL,
        blockers=[],
    )

    # Route after risk policy
    next_node = route_after_risk_policy(state)

    # Should proceed to trade proposal
    assert next_node == "generate_execution_proposal"

    # Should not mark as blocked
    assert state["workflow_state"] == WorkflowState.NORMAL


# ============================================================================
# Property 2: State Accumulation Invariant
# ============================================================================


@given(
    num_outputs=st.integers(min_value=1, max_value=6),
    output_keys=st.lists(
        st.sampled_from(
            [
                "memory_output",
                "research_output",
                "sentiment_output",
                "rebalancing_output",
                "risk_policy_output",
                "trade_proposal_output",
            ]
        ),
        min_size=1,
        max_size=6,
        unique=True,
    ),
)
@settings(max_examples=100, deadline=None)
def test_property_2_state_accumulation_invariant(num_outputs, output_keys):
    """
    Feature: llm-langgraph-integration, Property 2: State Accumulation Invariant
    For any sequence of agent outputs, state preserves all outputs without loss.

    Validates: Requirements 1.3, 1.8
    """
    # Create initial state
    state = WorkflowGraphState(
        request_id="test-req",
        session_id="test-session",
        trace_id="test-trace",
        schema_version="1.0.0",
        agent_version_set="1.0.0",
        policy_version="1.0.0",
        environment="test",
        trace_provider="bedrock_agentcore",
    )

    # Take only the number of outputs we need
    output_keys = output_keys[:num_outputs]

    # Add agent outputs
    for key in output_keys:
        state[key] = {"test_data": f"output_for_{key}"}

    # Verify all outputs are preserved
    for key in output_keys:
        assert key in state
        assert state[key] is not None
        assert state[key]["test_data"] == f"output_for_{key}"


# ============================================================================
# Property 3: Validation Failure Short-Circuit
# ============================================================================


@given(
    error_message=st.text(min_size=1, max_size=100),
    num_errors=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=50, deadline=None)
def test_property_3_validation_failure_short_circuit(error_message, num_errors):
    """
    Feature: llm-langgraph-integration, Property 3: Validation Failure Short-Circuit
    For any invalid request, routing skips agent execution and goes to audit.

    Validates: Requirements 1.4
    """
    # Create state with validation error
    state = WorkflowGraphState(
        request_id="test-req",
        session_id="test-session",
        trace_id="test-trace",
        schema_version="1.0.0",
        agent_version_set="1.0.0",
        policy_version="1.0.0",
        environment="test",
        trace_provider="bedrock_agentcore",
        validation_error={"errors": [error_message] * num_errors},
        workflow_state=WorkflowState.BLOCKED,
    )

    # Route after validation
    next_node = route_after_validation(state)

    # Should skip to audit emission
    assert next_node == "emit_workflow_audit_event"


@given(request_id=st.text(min_size=1, max_size=50))
@settings(max_examples=50, deadline=None)
def test_property_3_validation_success_proceeds(request_id):
    """
    Feature: llm-langgraph-integration, Property 3: Validation Failure Short-Circuit
    For any valid request, routing proceeds to initialization.

    Validates: Requirements 1.4
    """
    # Create state without validation error
    state = WorkflowGraphState(
        request_id=request_id,
        session_id="test-session",
        trace_id="test-trace",
        schema_version="1.0.0",
        agent_version_set="1.0.0",
        policy_version="1.0.0",
        environment="test",
        trace_provider="bedrock_agentcore",
        validation_error=None,
    )

    # Route after validation
    next_node = route_after_validation(state)

    # Should proceed to initialization
    assert next_node == "initialize_context_and_trace"


# ============================================================================
# Property 4: Guardrail Block Enforcement
# ============================================================================


@given(
    assessment_type=st.sampled_from(
        ["SENSITIVE_INFORMATION", "HARMFUL_CONTENT", "POLICY_VIOLATION"]
    )
)
@settings(max_examples=50, deadline=None)
def test_property_4_guardrail_block_enforcement(assessment_type):
    """
    Feature: llm-langgraph-integration, Property 4: Guardrail Block Enforcement
    For any guardrail BLOCKED action, workflow is marked BLOCKED and skips approval.

    Validates: Requirements 1.6
    """
    # Create state with blocked guardrail result
    state = WorkflowGraphState(
        request_id="test-req",
        session_id="test-session",
        trace_id="test-trace",
        schema_version="1.0.0",
        agent_version_set="1.0.0",
        policy_version="1.0.0",
        environment="test",
        trace_provider="bedrock_agentcore",
        guardrail_result={
            "action": "BLOCKED",
            "assessments": [{"type": assessment_type, "action": "BLOCKED"}],
        },
        workflow_state=WorkflowState.NORMAL,
        blockers=[],
    )

    # Route after guardrails
    next_node = route_after_guardrails(state)

    # Should skip approval artifact creation
    assert next_node == "emit_workflow_audit_event"

    # Should mark workflow as BLOCKED
    assert state["workflow_state"] == WorkflowState.BLOCKED

    # Should add GUARDRAIL_VIOLATION blocker
    assert "GUARDRAIL_VIOLATION" in state["blockers"]


@given(request_id=st.text(min_size=1, max_size=50))
@settings(max_examples=50, deadline=None)
def test_property_4_guardrail_pass_proceeds(request_id):
    """
    Feature: llm-langgraph-integration, Property 4: Guardrail Block Enforcement
    For any guardrail NONE action, workflow proceeds to approval creation.

    Validates: Requirements 1.6
    """
    # Create state with passing guardrail result
    state = WorkflowGraphState(
        request_id=request_id,
        session_id="test-session",
        trace_id="test-trace",
        schema_version="1.0.0",
        agent_version_set="1.0.0",
        policy_version="1.0.0",
        environment="test",
        trace_provider="bedrock_agentcore",
        guardrail_result={"action": "NONE", "assessments": []},
        workflow_state=WorkflowState.NORMAL,
    )

    # Route after guardrails
    next_node = route_after_guardrails(state)

    # Should proceed to approval artifact creation
    assert next_node == "create_approval_artifact"

    # Should not mark as blocked
    assert state["workflow_state"] == WorkflowState.NORMAL
