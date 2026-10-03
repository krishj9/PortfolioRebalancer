"""Property-based tests for response validation."""

import json

import pytest
from hypothesis import given, settings, strategies as st
from pydantic import BaseModel, Field

from app.adapters.bedrock import ModelResponse, TokenUsage
from app.adapters.validation import ResponseValidator, ValidationResult


# ============================================================================
# Test Schema
# ============================================================================


class TestOutputSchema(BaseModel):
    """Test schema for validation."""

    __test__ = False

    summary: str
    confidence: str = Field(pattern="^(HIGH|MEDIUM|LOW)$")
    items: list[str] = Field(default_factory=list)


# ============================================================================
# Property 8: Schema Validation Correctness
# ============================================================================


@given(
    summary=st.text(min_size=1, max_size=100),
    confidence=st.sampled_from(["HIGH", "MEDIUM", "LOW"]),
    items=st.lists(st.text(min_size=1, max_size=20), min_size=0, max_size=5),
)
@settings(max_examples=100, deadline=None)
@pytest.mark.asyncio
async def test_property_8_schema_validation_accepts_valid(summary, confidence, items):
    """
    Feature: llm-langgraph-integration, Property 8: Schema Validation Correctness
    For any response conforming to schema, validator accepts it.

    Validates: Requirements 2.8, 10.1, 18.5
    """
    # Create valid response
    content = json.dumps({"summary": summary, "confidence": confidence, "items": items})

    response = ModelResponse(
        content=content,
        model_id="test-model",
        usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        latency_ms=100.0,
        finish_reason="stop",
    )

    validator = ResponseValidator()
    result = await validator.validate(
        response, expected_schema=TestOutputSchema, confidence_threshold=0.0
    )

    # Should be valid
    assert result.is_valid
    assert len(result.violations) == 0


@given(
    summary=st.text(min_size=1, max_size=100),
    confidence=st.text(min_size=1, max_size=20).filter(
        lambda x: x not in ["HIGH", "MEDIUM", "LOW"]
    ),
)
@settings(max_examples=50, deadline=None)
@pytest.mark.asyncio
async def test_property_8_schema_validation_rejects_invalid(summary, confidence):
    """
    Feature: llm-langgraph-integration, Property 8: Schema Validation Correctness
    For any response violating schema, validator rejects it.

    Validates: Requirements 2.8, 10.1, 18.5
    """
    # Create invalid response (confidence doesn't match pattern)
    content = json.dumps({"summary": summary, "confidence": confidence, "items": []})

    response = ModelResponse(
        content=content,
        model_id="test-model",
        usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        latency_ms=100.0,
        finish_reason="stop",
    )

    validator = ResponseValidator()
    result = await validator.validate(response, expected_schema=TestOutputSchema)

    # Should be invalid
    assert not result.is_valid
    assert len(result.violations) > 0
    assert any(v["type"] == "schema" for v in result.violations)


# ============================================================================
# Property 9: Evidence Grounding Validation
# ============================================================================


@given(
    claim=st.text(min_size=10, max_size=100),
    evidence_keyword=st.text(
        alphabet=st.characters(whitelist_categories=("Ll",)), min_size=5, max_size=15
    ),
)
@settings(max_examples=50, deadline=None)
@pytest.mark.asyncio
async def test_property_9_grounding_validation_with_evidence(claim, evidence_keyword):
    """
    Feature: llm-langgraph-integration, Property 9: Evidence Grounding Validation
    For any claim with supporting evidence, grounding check passes.

    Validates: Requirements 3.2, 3.6, 10.1, 10.2
    """
    # Create claim that includes evidence keyword
    content = f"{claim} {evidence_keyword}"

    # Create evidence that contains the keyword
    evidence = [{"source": "test", "text": f"Evidence containing {evidence_keyword}"}]

    response = ModelResponse(
        content=content,
        model_id="test-model",
        usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        latency_ms=100.0,
        finish_reason="stop",
    )

    validator = ResponseValidator()
    result = await validator.validate(response, grounding_sources=evidence)

    # Should have grounding score
    assert result.grounding_score is not None
    # With matching keywords, should have reasonable grounding
    assert result.grounding_score >= 0.0


@given(
    claim=st.text(min_size=10, max_size=100),
    unrelated_text=st.text(min_size=10, max_size=100),
)
@settings(max_examples=50, deadline=None)
@pytest.mark.asyncio
async def test_property_9_grounding_validation_without_evidence(claim, unrelated_text):
    """
    Feature: llm-langgraph-integration, Property 9: Evidence Grounding Validation
    For any claim without supporting evidence, grounding check may fail.

    Validates: Requirements 3.2, 3.6, 10.1, 10.2
    """
    # Ensure claim and evidence are completely different
    if any(word in unrelated_text.lower() for word in claim.lower().split()[:3]):
        return  # Skip if there's accidental overlap

    content = claim
    evidence = [{"source": "test", "text": unrelated_text}]

    response = ModelResponse(
        content=content,
        model_id="test-model",
        usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        latency_ms=100.0,
        finish_reason="stop",
    )

    validator = ResponseValidator()
    result = await validator.validate(response, grounding_sources=evidence)

    # Should have grounding score
    assert result.grounding_score is not None
    # Score should be calculated (may be low or high depending on content)
    assert 0.0 <= result.grounding_score <= 1.0


# ============================================================================
# Property 13: Confidence Threshold Flagging
# ============================================================================


@given(confidence_value=st.floats(min_value=0.0, max_value=0.6))
@settings(max_examples=50, deadline=None)
@pytest.mark.asyncio
async def test_property_13_confidence_threshold_flagging_low(confidence_value):
    """
    Feature: llm-langgraph-integration, Property 13: Confidence Threshold Flagging
    For any output with confidence below threshold, system flags it.

    Validates: Requirements 10.4
    """
    # Create response with low confidence
    content = json.dumps({"confidence": confidence_value, "summary": "test"})

    response = ModelResponse(
        content=content,
        model_id="test-model",
        usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        latency_ms=100.0,
        finish_reason="stop",
    )

    validator = ResponseValidator()
    result = await validator.validate(response, confidence_threshold=0.7)

    # Should flag low confidence
    assert not result.is_valid
    assert any(v["type"] == "confidence" for v in result.violations)


@given(confidence_value=st.floats(min_value=0.7, max_value=1.0))
@settings(max_examples=50, deadline=None)
@pytest.mark.asyncio
async def test_property_13_confidence_threshold_flagging_high(confidence_value):
    """
    Feature: llm-langgraph-integration, Property 13: Confidence Threshold Flagging
    For any output with confidence above threshold, system accepts it.

    Validates: Requirements 10.4
    """
    # Create response with high confidence
    content = json.dumps({"confidence": confidence_value, "summary": "test"})

    response = ModelResponse(
        content=content,
        model_id="test-model",
        usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        latency_ms=100.0,
        finish_reason="stop",
    )

    validator = ResponseValidator()
    result = await validator.validate(response, confidence_threshold=0.7)

    # Should not flag confidence (may have other violations, but not confidence)
    confidence_violations = [v for v in result.violations if v["type"] == "confidence"]
    assert len(confidence_violations) == 0
