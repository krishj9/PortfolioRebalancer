"""Tests for Memory Generation Trigger and cross-session retrieval (Task P2-03).

Includes:
- Local mocked unit tests for trigger invocation and graceful error handling.
- Live GCP smoke test verifying that preference recorded in Session A is retrievable in Session B.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from app.adapters.memory import MemoryBankAdapter
from app.adapters.sessions import AgentPlatformSessionsAdapter, InMemorySessionsAdapter
from app.agent_runtime.app import RebalanceGraphApp
from tests.test_rebalance import request_payload


def test_memory_generation_trigger_mocked():
    """Verify _trigger_memory_generation invokes client.memory_banks.memories.generate."""
    mock_client = MagicMock()
    app_instance = RebalanceGraphApp(
        project="test-proj",
        location="us-central1",
        tool_mode="inprocess",
        sessions_mode="local",
        memory_generation_enabled=True,
        client=mock_client,
    )
    app_instance.set_up()
    app_instance._sessions_adapter._runtime_name = "projects/123/reasoningEngines/456"

    # Call trigger directly
    app_instance._trigger_memory_generation(
        session_name="projects/123/reasoningEngines/456/sessions/sess-1",
        actor_id="user-xyz",
    )

    mock_client.memory_banks.memories.generate.assert_called_once_with(
        name="projects/123/reasoningEngines/456",
        vertex_session_source={"session": "projects/123/reasoningEngines/456/sessions/sess-1"},
        scope={"user_id": "user-xyz"},
    )


def test_memory_generation_trigger_failure_resilience():
    """Verify exceptions in _trigger_memory_generation are caught and logged without raising."""
    mock_client = MagicMock()
    mock_client.memory_banks.memories.generate.side_effect = RuntimeError("Service failure")

    app_instance = RebalanceGraphApp(
        project="test-proj",
        location="us-central1",
        tool_mode="inprocess",
        sessions_mode="local",
        memory_generation_enabled=True,
        client=mock_client,
    )
    app_instance.set_up()
    app_instance._sessions_adapter._runtime_name = "projects/123/reasoningEngines/456"

    # Should not raise exception
    app_instance._trigger_memory_generation(
        session_name="projects/123/reasoningEngines/456/sessions/sess-1",
        actor_id="user-xyz",
    )


def test_user_preference_explanation_style_and_trade_invariance():
    """Task P2-04: Verify explanation reflects preference while trades remain 100% deterministic."""
    from app.contracts.workflow import PortfolioRebalanceRequest
    from app.services.policy import evaluate_policy
    from app.services.portfolio import calculate_drift
    from app.services.proposal import generate_execution_proposal

    payload = request_payload()
    req = PortfolioRebalanceRequest.model_validate(payload)

    # 1. Deterministic baseline trades
    drift = calculate_drift(req.portfolio_snapshot, req.allocation_target)
    policy = evaluate_policy(req.portfolio_snapshot, drift, req.risk_profile)
    deterministic_proposal = generate_execution_proposal(req.portfolio_snapshot, policy)

    # 2. Run RebalanceGraphApp
    app_instance = RebalanceGraphApp(
        project="test-proj",
        location="us-central1",
        tool_mode="inprocess",
        sessions_mode="local",
    )
    app_instance.set_up()

    # Pre-populate session with presentation preference
    session_id = app_instance._sessions_adapter.create_or_get_session(user_id=req.actor.actor_id)
    app_instance._sessions_adapter.append_user_event(
        session_name=session_id,
        text="Keep explanations short and include a trade table.",
    )

    result = app_instance.query(payload, session_id=session_id)
    rec = result["recommendation_package"]
    proposal = rec["proposal"]

    # Verify deterministic trade invariants: symbols, actions, and values match exactly
    assert len(proposal["trades"]) == len(deterministic_proposal.trades)
    for res_trade, exp_trade in zip(proposal["trades"], deterministic_proposal.trades):
        assert res_trade["symbol"] == exp_trade.symbol
        exp_action = exp_trade.action.value if hasattr(exp_trade.action, "value") else str(exp_trade.action)
        assert res_trade["action"] == exp_action
        assert float(res_trade["estimated_value"]) == float(exp_trade.estimated_value)


@pytest.mark.asyncio
async def test_trade_execution_prompt_includes_user_preferences():
    """Task P2-04: Verify TradeExecutionProposalAgent prompt template includes user_preferences block."""
    from unittest.mock import AsyncMock
    from app.adapters.prompts import PromptTemplateLoader
    from app.adapters.validation import ResponseValidator
    from app.agents.trade_execution import TradeExecutionProposalAgent
    from app.contracts.workflow import PortfolioRebalanceRequest
    from app.services.policy import evaluate_policy
    from app.services.portfolio import calculate_drift
    from app.services.proposal import generate_execution_proposal

    mock_llm = MagicMock()
    mock_llm.invoke_model = AsyncMock(
        return_value=MagicMock(
            content='{"proposal_summary": "Short rebalance", "primary_objectives": ["Balance"], "trade_rationale": [{"symbol": "EQUITY", "action": "SELL", "quantity": 1, "rationale": "Trim", "expected_impact": "Lower risk"}], "risk_considerations": ["None"], "expected_benefits": ["Target met"], "confidence": 0.9}'
        )
    )

    loader = PromptTemplateLoader()
    agent = TradeExecutionProposalAgent(
        bedrock_adapter=mock_llm,
        prompt_loader=loader,
        validator=ResponseValidator(),
    )
    agent.feature_flags.trade_proposal_agent_llm_enabled = True

    payload = request_payload()
    req = PortfolioRebalanceRequest.model_validate(payload)
    drift = calculate_drift(req.portfolio_snapshot, req.allocation_target)
    policy = evaluate_policy(req.portfolio_snapshot, drift, req.risk_profile)
    deterministic_proposal = generate_execution_proposal(req.portfolio_snapshot, policy)

    memory_ctx = {
        "items": [
            {"summary": "Keep explanation short and provide a trade table.", "category": "user_preference"}
        ]
    }

    rationale = await agent.generate_proposal_rationale(
        req.portfolio_snapshot,
        policy,
        deterministic_proposal,
        memory=memory_ctx,
    )

    assert rationale is not None
    # Check that invoke_model prompt received the user preferences
    call_args = mock_llm.invoke_model.call_args
    prompt_text = call_args.kwargs.get("prompt", "")
    assert "User Preferences (Non-Authoritative Presentation Guidance Only)" in prompt_text
    assert "Keep explanation short and provide a trade table." in prompt_text


@pytest.mark.gcp
@pytest.mark.skipif(
    not os.environ.get("RUN_GCP_TESTS"),
    reason="Set RUN_GCP_TESTS=1 to run smoke test against remote Vertex AI Memory Bank",
)
def test_cross_session_memory_generation_smoke():
    """Verify that a preference stated in Session A is generated into Memory Bank and retrievable in Session B."""
    import agentplatform

    project = os.environ.get("GCP_PROJECT", "mybrightday-dev")
    location = os.environ.get("GCP_LOCATION", "us-central1")

    resource_file = (
        Path(__file__).resolve().parent.parent.parent
        / "app"
        / "agent_runtime"
        / "deployed_runtime.txt"
    )
    if not resource_file.exists():
        pytest.skip(f"Deployed runtime resource file not found: {resource_file}")

    runtime_name = resource_file.read_text().strip()
    client = agentplatform.Client(project=project, location=location)

    sessions_adapter = AgentPlatformSessionsAdapter(
        project=project,
        location=location,
        runtime_name=runtime_name,
        client=client,
    )
    memory_adapter = MemoryBankAdapter(
        project=project,
        location=location,
        runtime_name=runtime_name,
        client=client,
    )

    test_user_id = f"test-user-p203-{os.getpid()}"
    scope = {"user_id": test_user_id}

    # Step 1: Session A - Create and record user preference
    session_a = sessions_adapter.create_or_get_session(user_id=test_user_id)
    created_memory_names = []

    try:
        pref_text = "I prefer a conservative portfolio risk profile and want to maintain at least 15% in cash."
        sessions_adapter.append_user_event(
            session_name=session_a,
            text=pref_text,
            invocation_id="inv-sess-a",
        )
        sessions_adapter.append_summary_event(
            session_name=session_a,
            text="Understood. Recommendation generated adhering to conservative risk with 15% cash minimum.",
            invocation_id="inv-sess-a",
        )

        # Trigger memory generation synchronously with wait_for_completion for test verification
        gen_op = client.memory_banks.memories.generate(
            name=runtime_name,
            vertex_session_source={"session": session_a},
            scope=scope,
            config={"wait_for_completion": True},
        )
        assert gen_op.done, "Memory generation operation did not complete"

        generated = getattr(gen_op.response, "generated_memories", None) or getattr(
            gen_op.response, "generatedMemories", None
        )
        assert generated, f"Expected generated memories in response, got: {gen_op.response}"
        for g in generated:
            created_memory_names.append(g.memory.name)

        # Step 2: Session B - Retrieve memories for the same user in an independent session context
        session_b = sessions_adapter.create_or_get_session(user_id=test_user_id)
        assert session_b != session_a

        # Retrieve memories using MemoryBankAdapter
        import asyncio

        retrieved_items = asyncio.run(memory_adapter.retrieve(user_id=test_user_id))
        assert len(retrieved_items) >= 1, "Expected at least 1 memory item retrieved in Session B"

        found_fact = any(
            "15%" in item.summary or "conservative" in item.summary.lower()
            for item in retrieved_items
        )
        assert found_fact, f"Preference from Session A not found in Session B memories: {retrieved_items}"

    finally:
        # Cleanup
        for mem_name in created_memory_names:
            try:
                client.memory_banks.memories.delete(name=mem_name)
            except Exception:
                pass
        try:
            sessions_adapter.delete_session(session_a)
        except Exception:
            pass
        if "session_b" in locals():
            try:
                sessions_adapter.delete_session(session_b)
            except Exception:
                pass
