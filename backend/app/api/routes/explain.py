"""Recommendation explanation and scenario simulation endpoints."""

import json
import logging
from decimal import Decimal
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.adapters.model_factory import get_model_adapter
from app.contracts.analysis import DriftItem
from app.core.config import get_feature_flags, get_llm_config
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import WorkflowStore
from app.services.policy import evaluate_policy
from app.services.portfolio import calculate_drift

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["explain"])


# ── Request models ────────────────────────────────────────────────────────────

class ExplainRequest(BaseModel):
    question: str
    actor_id: str = "local_owner"


class SimulateRequest(BaseModel):
    scenario: dict  # equity_target, fixed_income_target, cash_target, max_single_position_pct


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_recommendation_context(approval) -> str:
    """Build a concise context string from the approval artifact for the LLM."""
    rec = approval.recommendation
    lines = [
        f"Portfolio: {approval.account_profile.account_id}",
        f"Workflow state: {rec.workflow_state}",
        f"Policy verdict: {rec.risk_policy.verdict}",
        f"Summary: {rec.summary}",
    ]

    # Allocation
    if rec.current_allocation:
        alloc_parts = [f"{k}: {v}%" for k, v in rec.current_allocation.items()]
        lines.append(f"Current allocation: {', '.join(alloc_parts)}")
    if rec.target_allocation:
        target_parts = [f"{k}: {v}%" for k, v in rec.target_allocation.items()]
        lines.append(f"Target allocation: {', '.join(target_parts)}")

    # Trades
    trades = rec.proposal.trades if rec.proposal else []
    if trades:
        trade_lines = [f"  - {t.action} {t.symbol}: ${t.estimated_value} — {t.rationale}" for t in trades]
        lines.append("Proposed trades:\n" + "\n".join(trade_lines))
    else:
        lines.append("No trades proposed.")

    # Policy rules
    if rec.risk_policy.rule_results:
        rule_lines = [
            f"  - {'PASS' if r.get('passed') else 'FAIL'}: {r.get('message', '')}"
            for r in rec.risk_policy.rule_results
        ]
        lines.append("Policy checks:\n" + "\n".join(rule_lines))

    # Risk profile
    if approval.risk_profile:
        lines.append(
            f"Risk profile: {approval.risk_profile.risk_level}, "
            f"max position {approval.risk_profile.max_single_position_pct}%, "
            f"max sector {approval.risk_profile.max_sector_pct}%"
        )

    # Sentiment output — include if available
    sentiment = getattr(rec, "sentiment_output", None)
    if sentiment:
        overall = sentiment.get("overall_sentiment", "NEUTRAL")
        lines.append(f"Market sentiment (from MCP Sentiment Agent): overall = {overall}")
        for sym in sentiment.get("symbol_sentiments", []):
            symbol = sym.get("symbol", "?")
            sent = sym.get("overall_sentiment", "NEUTRAL")
            score = sym.get("sentiment_score", 0.0)
            summary = sym.get("summary", "")
            lines.append(f"  - {symbol}: {sent} (score {score:.2f}){' — ' + summary if summary else ''}")

    # Research output — include if available
    research = getattr(rec, "research_output", None)
    if research:
        regime = research.get("regime", "NEUTRAL")
        context = research.get("market_context", "")
        lines.append(f"Market research (from A2A Research Agent): regime = {regime}")
        if context:
            lines.append(f"  Context: {context}")
        for insight in research.get("key_insights", []):
            lines.append(f"  - {insight}")

    return "\n".join(lines)


async def _stream_llm_response(
    question: str,
    context: str,
) -> AsyncGenerator[str, None]:
    """Stream LLM response as SSE events."""
    feature_flags = get_feature_flags()
    llm_config = get_llm_config()

    system_prompt = (
        "You are a professional portfolio advisor explaining a specific rebalancing recommendation "
        "to an investor. Answer questions clearly and concisely based ONLY on the recommendation "
        "context provided — which includes allocation data, proposed trades, policy checks, "
        "market sentiment analysis from the MCP Sentiment Agent, and market research insights "
        "from the A2A Research Agent. When asked about sentiment or market conditions, refer to "
        "the sentiment and research data in the context. "
        "Do not invent data beyond what is provided. "
        "Keep answers under 150 words. Use plain language, not financial jargon."
    )

    user_prompt = f"""Recommendation context:
{context}

Investor question: {question}"""

    if not feature_flags.risk_agent_llm_enabled:
        # Fallback: deterministic answer based on context
        fallback = (
            f"Based on the recommendation: {context[:300]}... "
            f"To answer your question about '{question}': "
            "Please enable LLM features (FEATURE_RISK_AGENT_LLM_ENABLED=true) for detailed AI explanations."
        )
        yield f"data: {json.dumps({'type': 'token', 'content': fallback})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    try:
        adapter = get_model_adapter()
        model_id = (
            llm_config.gemini_model
            if getattr(llm_config, "provider", "gemini") == "gemini"
            else llm_config.risk_agent_model
        )

        # Use streaming invoke
        async for chunk in adapter.invoke_model_streaming(
            model_id=model_id,
            system_prompt=system_prompt,
            prompt=user_prompt,
            temperature=0.5,
            max_tokens=300,
        ):
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        logger.error(f"LLM streaming failed: {e}", exc_info=True)
        error_msg = "I encountered an error generating the explanation. Please try again."
        yield f"data: {json.dumps({'type': 'token', 'content': error_msg})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/{approval_id}/explain")
async def explain_recommendation(
    approval_id: str,
    request: ExplainRequest,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
) -> StreamingResponse:
    """Stream an LLM explanation of the recommendation in response to a question."""
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval artifact not found")

    context = _build_recommendation_context(approval)

    return StreamingResponse(
        _stream_llm_response(request.question, context),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{approval_id}/simulate")
async def simulate_scenario(
    approval_id: str,
    request: SimulateRequest,
    store: Annotated[WorkflowStore, Depends(get_workflow_store)],
) -> dict:
    """
    Run a what-if scenario: recalculate drift and policy with new targets/risk params.
    Fast — no full LangGraph run.
    """
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval artifact not found")

    scenario = request.scenario
    snapshot = approval.original_portfolio_snapshot
    original_target = approval.allocation_target
    original_risk = approval.risk_profile

    # Build new allocation target from scenario
    from app.contracts.domain import AllocationTarget, RiskProfile

    equity_t = Decimal(str(scenario.get("equity_target", 60)))
    fi_t = Decimal(str(scenario.get("fixed_income_target", 30)))
    cash_t = Decimal(str(scenario.get("cash_target", 10)))

    new_target = AllocationTarget(
        target_id=original_target.target_id,
        account_id=original_target.account_id,
        asset_class_targets={
            "equity": equity_t,
            "fixed_income": fi_t,
            "cash": cash_t,
        },
        tolerance_bands=original_target.tolerance_bands,
    )

    # Build new risk profile from scenario
    max_pos = Decimal(str(scenario.get("max_single_position_pct",
        original_risk.max_single_position_pct if original_risk else 85)))

    new_risk = RiskProfile(
        risk_profile_id=original_risk.risk_profile_id if original_risk else "scenario",
        risk_level=original_risk.risk_level if original_risk else "balanced",
        max_single_position_pct=max_pos,
        max_sector_pct=original_risk.max_sector_pct if original_risk else Decimal("60"),
        allowed_asset_classes=original_risk.allowed_asset_classes if original_risk else ["equity", "fixed_income", "cash"],
    )

    # Recalculate drift deterministically
    drift_items: list[DriftItem] = calculate_drift(snapshot, new_target)
    policy_result = evaluate_policy(snapshot, drift_items, new_risk)

    would_be_blocked = policy_result.verdict.value != "COMPLIANT"

    # Build drift changes summary
    drift_changes = [
        {
            "key": item.key,
            "current_pct": str(item.current_pct),
            "target_pct": str(item.target_pct),
            "drift_pct": str(item.drift_pct),
            "within_tolerance": item.within_tolerance,
        }
        for item in drift_items
    ]

    # LLM scenario summary (fast, optional)
    scenario_summary = _deterministic_scenario_summary(drift_items, policy_result, would_be_blocked)

    feature_flags = get_feature_flags()
    if feature_flags.risk_agent_llm_enabled:
        try:
            scenario_summary = await _llm_scenario_summary(
                drift_items, policy_result, would_be_blocked, scenario
            )
        except Exception as e:
            logger.warning(f"LLM scenario summary failed, using deterministic: {e}")

    return {
        "drift_changes": drift_changes,
        "policy_verdict": policy_result.verdict.value,
        "would_be_blocked": would_be_blocked,
        "scenario_summary": scenario_summary,
    }


def _deterministic_scenario_summary(drift_items, policy_result, would_be_blocked: bool) -> str:
    out_of_tolerance = [d for d in drift_items if not d.within_tolerance]
    if would_be_blocked:
        return (
            f"This scenario would be blocked by policy. "
            f"{len(out_of_tolerance)} asset class(es) outside tolerance."
        )
    if not out_of_tolerance:
        return "With these targets, the portfolio would be within tolerance — no rebalancing needed."
    assets = ", ".join(d.key for d in out_of_tolerance)
    return f"{len(out_of_tolerance)} asset class(es) ({assets}) would require rebalancing with these targets."


async def _llm_scenario_summary(drift_items, policy_result, would_be_blocked: bool, scenario: dict) -> str:
    llm_config = get_llm_config()
    adapter = get_model_adapter()

    drift_text = "; ".join(
        f"{d.key}: current {d.current_pct}% vs target {d.target_pct}% (drift {d.drift_pct}%)"
        for d in drift_items
    )

    prompt = f"""Scenario targets: equity {scenario.get('equity_target')}%, fixed_income {scenario.get('fixed_income_target')}%, cash {scenario.get('cash_target')}%, max position {scenario.get('max_single_position_pct')}%.
Drift result: {drift_text}
Policy verdict: {policy_result.verdict.value}

In 1-2 sentences, explain what this scenario means for the investor. Be specific about what changes and any risks."""

    model_id = (
        llm_config.gemini_model
        if getattr(llm_config, "provider", "gemini") == "gemini"
        else llm_config.risk_agent_model
    )
    response = await adapter.invoke_model(
        model_id=model_id,
        system_prompt="You are a portfolio advisor. Give a brief, plain-language scenario impact summary.",
        prompt=prompt,
        temperature=0.3,
        max_tokens=120,
    )
    return response.content.strip()
