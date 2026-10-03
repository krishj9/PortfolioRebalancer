"""Unit tests validating that the LLM code path in all agents executes properly.

Validates Task P1-02 from docs/migration/backlog.md:
- Resolves Defect D3: agent calls to adapter now invoke `invoke_model`
- Verifies that when LLM flags are enabled, all 4 agents (Memory, Rebalancing, Risk, TradeProposal)
  invoke the adapter's `invoke_model` method.
- Verifies that when LLM flags are disabled, all agents execute deterministic logic without calling LLM.
"""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters.bedrock import BedrockModelAdapter, ModelResponse, TokenUsage
from app.adapters.prompts import PromptTemplateLoader
from app.adapters.validation import ResponseValidator
from app.agents.memory import MemoryPersonalizationAgent
from app.agents.rebalancing import PortfolioRebalancingAgent
from app.agents.risk_compliance import RiskComplianceAgent
from app.agents.trade_execution import TradeExecutionProposalAgent
from app.contracts.analysis import PolicyVerdictStatus, RiskPolicyResponse
from app.contracts.workflow import PortfolioRebalanceRequest
from app.core.config import get_feature_flags
from app.services.portfolio import calculate_drift
from tests.test_rebalance import request_payload


@pytest.fixture
def rebalance_request() -> PortfolioRebalanceRequest:
    return PortfolioRebalanceRequest.model_validate(request_payload())


def make_mock_response(content_dict: dict) -> ModelResponse:
    return ModelResponse(
        content=json.dumps(content_dict),
        model_id="test-model",
        usage=TokenUsage(input_tokens=50, output_tokens=100, total_tokens=150),
        latency_ms=25.0,
        finish_reason="stop",
    )


@pytest.mark.asyncio
async def test_memory_agent_llm_path(rebalance_request: PortfolioRebalanceRequest) -> None:
    """Verify MemoryPersonalizationAgent calls invoke_model when LLM flag is enabled."""
    mock_adapter = MagicMock(spec=BedrockModelAdapter)
    mock_adapter.invoke_model = AsyncMock(
        return_value=make_mock_response({
            "semantic_query": "ESG moderate risk allocation drift",
            "keywords": ["ESG", "drift"],
            "confidence": 0.95,
        })
    )

    flags = get_feature_flags()
    orig = flags.memory_agent_llm_enabled
    try:
        flags.memory_agent_llm_enabled = True
        agent = MemoryPersonalizationAgent(
            bedrock_adapter=mock_adapter,
            prompt_loader=PromptTemplateLoader(),
            validator=ResponseValidator(),
        )

        stage_result, payload = await agent.run(rebalance_request)

        assert stage_result.status == "COMPLETED"
        assert mock_adapter.invoke_model.called
        call_kwargs = mock_adapter.invoke_model.call_args.kwargs
        assert "prompt" in call_kwargs or "user_prompt" in call_kwargs
    finally:
        flags.memory_agent_llm_enabled = orig


@pytest.mark.asyncio
async def test_rebalancing_agent_llm_path(rebalance_request: PortfolioRebalanceRequest) -> None:
    """Verify PortfolioRebalancingAgent calls invoke_model when LLM flag is enabled."""
    mock_adapter = MagicMock(spec=BedrockModelAdapter)
    mock_adapter.invoke_model = AsyncMock(
        return_value=make_mock_response({
            "drift_summary": "Portfolio has drifted outside configured bands.",
            "drifted_assets": [],
            "why_drift_occurred": "Equity market rally.",
            "implications": "Increased risk profile.",
            "urgency": "MEDIUM",
            "confidence": 0.90,
        })
    )

    flags = get_feature_flags()
    orig = flags.rebalancing_agent_llm_enabled
    try:
        flags.rebalancing_agent_llm_enabled = True
        agent = PortfolioRebalancingAgent(
            bedrock_adapter=mock_adapter,
            prompt_loader=PromptTemplateLoader(),
            validator=ResponseValidator(),
        )

        stage_result, payload = await agent.run(rebalance_request)

        assert stage_result.status == "COMPLETED"
        assert mock_adapter.invoke_model.called
        assert "current_allocation" in payload
        assert "drift" in payload
    finally:
        flags.rebalancing_agent_llm_enabled = orig


@pytest.mark.asyncio
async def test_risk_compliance_agent_llm_path(rebalance_request: PortfolioRebalanceRequest) -> None:
    """Verify RiskComplianceAgent calls invoke_model when LLM flag is enabled."""
    mock_adapter = MagicMock(spec=BedrockModelAdapter)
    mock_adapter.invoke_model = AsyncMock(
        return_value=make_mock_response({
            "verdict_summary": "Portfolio is compliant with risk constraints.",
            "rules_evaluated": [],
            "verdict_status": "COMPLIANT",
            "rationale": "All asset classes within concentration limits.",
            "actions_required": [],
            "confidence": 0.95,
        })
    )

    flags = get_feature_flags()
    orig = flags.risk_agent_llm_enabled
    try:
        flags.risk_agent_llm_enabled = True
        agent = RiskComplianceAgent(
            bedrock_adapter=mock_adapter,
            prompt_loader=PromptTemplateLoader(),
            validator=ResponseValidator(),
        )

        drift = calculate_drift(
            rebalance_request.portfolio_snapshot, rebalance_request.allocation_target
        )
        stage_result, risk_res = await agent.run(
            rebalance_request.portfolio_snapshot, drift, rebalance_request.risk_profile
        )

        assert stage_result.status == "COMPLETED"
        assert mock_adapter.invoke_model.called
        assert risk_res.verdict == PolicyVerdictStatus.COMPLIANT
    finally:
        flags.risk_agent_llm_enabled = orig


@pytest.mark.asyncio
async def test_trade_proposal_agent_llm_path(rebalance_request: PortfolioRebalanceRequest) -> None:
    """Verify TradeExecutionProposalAgent calls invoke_model when LLM flag is enabled."""
    mock_adapter = MagicMock(spec=BedrockModelAdapter)
    mock_adapter.invoke_model = AsyncMock(
        return_value=make_mock_response({
            "proposal_summary": "Rebalancing trades to align asset allocation.",
            "trade_rationales": [],
            "risk_mitigation": "Reduces equity overweight.",
            "expected_benefits": "Brings portfolio back into compliance.",
            "confidence": 0.92,
        })
    )

    flags = get_feature_flags()
    orig = flags.trade_proposal_agent_llm_enabled
    try:
        flags.trade_proposal_agent_llm_enabled = True
        agent = TradeExecutionProposalAgent(
            bedrock_adapter=mock_adapter,
            prompt_loader=PromptTemplateLoader(),
            validator=ResponseValidator(),
        )

        drift = calculate_drift(
            rebalance_request.portfolio_snapshot, rebalance_request.allocation_target
        )
        risk_policy = RiskPolicyResponse(
            verdict=PolicyVerdictStatus.COMPLIANT,
            evidence=[],
            drift=drift,
            confidence={"overall": "HIGH"},
        )

        stage_result, proposal = await agent.run(rebalance_request.portfolio_snapshot, risk_policy)

        assert stage_result.status == "COMPLETED"
        assert mock_adapter.invoke_model.called
        assert proposal.proposal_status in ("READY_FOR_REVIEW", "NO_ACTION_NEEDED")
    finally:
        flags.trade_proposal_agent_llm_enabled = orig


@pytest.mark.asyncio
async def test_agents_deterministic_fallback_when_llm_disabled(
    rebalance_request: PortfolioRebalanceRequest,
) -> None:
    """Verify that when LLM flags are disabled, agents run deterministically without calling invoke_model."""
    mock_adapter = MagicMock(spec=BedrockModelAdapter)
    mock_adapter.invoke_model = AsyncMock()

    flags = get_feature_flags()
    flags.memory_agent_llm_enabled = False
    flags.rebalancing_agent_llm_enabled = False
    flags.risk_agent_llm_enabled = False
    flags.trade_proposal_agent_llm_enabled = False

    mem_agent = MemoryPersonalizationAgent(bedrock_adapter=mock_adapter)
    await mem_agent.run(rebalance_request)

    reb_agent = PortfolioRebalancingAgent(bedrock_adapter=mock_adapter)
    await reb_agent.run(rebalance_request)

    drift = calculate_drift(rebalance_request.portfolio_snapshot, rebalance_request.allocation_target)
    risk_agent = RiskComplianceAgent(bedrock_adapter=mock_adapter)
    _, risk_policy = await risk_agent.run(
        rebalance_request.portfolio_snapshot, drift, rebalance_request.risk_profile
    )

    trade_agent = TradeExecutionProposalAgent(bedrock_adapter=mock_adapter)
    await trade_agent.run(rebalance_request.portfolio_snapshot, risk_policy)

    assert not mock_adapter.invoke_model.called, "invoke_model should not be called when LLM is disabled"
