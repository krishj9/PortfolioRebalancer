"""Response validation with schema and grounding checks."""

import json
import logging
from typing import Optional, Type

from pydantic import BaseModel, Field, ValidationError

from app.adapters.bedrock import ModelResponse

logger = logging.getLogger(__name__)


# ============================================================================
# Models
# ============================================================================


class GroundingResult(BaseModel):
    """Result of grounding check."""

    is_grounded: bool
    grounding_score: float
    ungrounded_claims: list[str] = Field(default_factory=list)
    evidence_citations: list[dict] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """Result of response validation."""

    is_valid: bool
    violations: list[dict] = Field(default_factory=list)
    confidence: Optional[float] = None
    grounding_score: Optional[float] = None


# ============================================================================
# Response Validator
# ============================================================================


class ResponseValidator:
    """Validates LLM responses for schema compliance and grounding."""

    def __init__(self, guardrails_client=None):
        """
        Initialize validator.

        Args:
            guardrails_client: Optional Bedrock guardrails client
        """
        self.guardrails_client = guardrails_client

    async def validate(
        self,
        response: ModelResponse,
        expected_schema: Optional[Type[BaseModel]] = None,
        grounding_sources: Optional[list[dict]] = None,
        confidence_threshold: float = 0.7,
        apply_guardrails: bool = False,
    ) -> ValidationResult:
        """
        Multi-stage validation:
        1. Schema validation (Pydantic)
        2. Grounding validation (evidence citations)
        3. Confidence validation (threshold checks)
        4. Bedrock guardrails (safety, policy)

        Args:
            response: Model response to validate
            expected_schema: Expected Pydantic schema
            grounding_sources: Evidence sources for grounding check
            confidence_threshold: Minimum confidence threshold
            apply_guardrails: Whether to apply Bedrock guardrails

        Returns:
            ValidationResult with pass/fail status and violations
        """
        violations = []

        # Schema validation
        if expected_schema:
            schema_violations = await self._validate_schema(response, expected_schema)
            violations.extend(schema_violations)

        # Grounding validation
        grounding_score = None
        if grounding_sources:
            grounding_result = await self._check_grounding(
                response.content, grounding_sources
            )
            if not grounding_result.is_grounded:
                violations.append(
                    {
                        "type": "grounding",
                        "ungrounded_claims": grounding_result.ungrounded_claims,
                    }
                )
            grounding_score = grounding_result.grounding_score

        # Confidence validation
        confidence = self._extract_confidence(response.content)
        if confidence is not None and confidence < confidence_threshold:
            violations.append(
                {
                    "type": "confidence",
                    "value": confidence,
                    "threshold": confidence_threshold,
                }
            )

        # Guardrails validation
        if apply_guardrails and self.guardrails_client:
            guardrail_violations = await self._apply_guardrails(response.content)
            violations.extend(guardrail_violations)

        return ValidationResult(
            is_valid=len(violations) == 0,
            violations=violations,
            confidence=confidence,
            grounding_score=grounding_score,
        )

    async def _validate_schema(
        self, response: ModelResponse, expected_schema: Type[BaseModel]
    ) -> list[dict]:
        """
        Validate response against Pydantic schema.

        Args:
            response: Model response
            expected_schema: Expected Pydantic schema

        Returns:
            List of schema violations
        """
        violations = []

        try:
            # Try to parse as JSON first
            try:
                content_json = json.loads(response.content)
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code blocks
                import re

                json_match = re.search(
                    r"```(?:json)?\s*(\{.*?\})\s*```", response.content, re.DOTALL
                )
                if json_match:
                    content_json = json.loads(json_match.group(1))
                else:
                    raise ValueError("No valid JSON found in response")

            # Validate against schema
            expected_schema.model_validate(content_json)

        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            violations.append(
                {
                    "type": "schema",
                    "errors": str(e) if not isinstance(e, ValidationError) else e.errors(),
                }
            )

        return violations

    async def _check_grounding(
        self, content: str, sources: list[dict]
    ) -> GroundingResult:
        """
        Check if claims in content are supported by sources.

        This is a simplified implementation. In production, you would use
        Bedrock guardrails grounding check API.

        Args:
            content: LLM response content
            sources: Evidence sources

        Returns:
            GroundingResult with grounding status
        """
        # Simplified grounding check
        # In production, use Bedrock guardrails API

        # Extract claims (simplified - look for sentences)
        import re

        sentences = re.split(r"[.!?]+", content)
        claims = [s.strip() for s in sentences if len(s.strip()) > 10]

        # Check if sources are referenced
        ungrounded_claims = []
        evidence_citations = []

        source_texts = [str(s) for s in sources]
        source_text_combined = " ".join(source_texts).lower()

        for claim in claims:
            # Simple keyword matching (in production, use semantic similarity)
            claim_lower = claim.lower()
            words = claim_lower.split()
            significant_words = [w for w in words if len(w) > 4]

            if significant_words:
                # Check if any significant words appear in sources
                found_in_source = any(
                    word in source_text_combined for word in significant_words[:3]
                )

                if not found_in_source:
                    ungrounded_claims.append(claim)
                else:
                    evidence_citations.append(
                        {"claim": claim, "source": "evidence_provided"}
                    )

        # Calculate grounding score
        total_claims = len(claims)
        grounded_claims = total_claims - len(ungrounded_claims)
        grounding_score = (
            grounded_claims / total_claims if total_claims > 0 else 1.0
        )

        return GroundingResult(
            is_grounded=grounding_score >= 0.7,
            grounding_score=grounding_score,
            ungrounded_claims=ungrounded_claims,
            evidence_citations=evidence_citations,
        )

    def _extract_confidence(self, content: str) -> Optional[float]:
        """
        Extract confidence score from LLM output.

        Looks for patterns like:
        - "confidence": "HIGH" -> 0.9
        - "confidence": "MEDIUM" -> 0.7
        - "confidence": "LOW" -> 0.5
        - "confidence": 0.85

        Args:
            content: LLM response content

        Returns:
            Confidence score or None
        """
        import re

        # Try to find confidence in JSON
        try:
            content_json = json.loads(content)
            if isinstance(content_json, dict) and "confidence" in content_json:
                conf = content_json["confidence"]
                if isinstance(conf, (int, float)):
                    return float(conf)
                elif isinstance(conf, str):
                    conf_upper = conf.upper()
                    if conf_upper == "HIGH":
                        return 0.9
                    elif conf_upper == "MEDIUM":
                        return 0.7
                    elif conf_upper == "LOW":
                        return 0.5
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            pass

        # Try to find confidence in text
        patterns = [
            (r'"confidence":\s*"HIGH"', 0.9),
            (r'"confidence":\s*"MEDIUM"', 0.7),
            (r'"confidence":\s*"LOW"', 0.5),
            (r'"confidence":\s*(0\.\d+)', None),  # Extract numeric value
        ]

        for pattern, score in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                if score is not None:
                    return score
                else:
                    # Extract numeric value
                    return float(match.group(1))

        return None

    async def _apply_guardrails(self, content: str) -> list[dict]:
        """
        Apply Bedrock guardrails to content.

        Args:
            content: Content to check

        Returns:
            List of guardrail violations
        """
        violations = []

        if not self.guardrails_client:
            return violations

        try:
            # Call Bedrock guardrails API
            # This is a placeholder - actual implementation would use boto3
            response = self.guardrails_client.apply_guardrail(
                guardrailIdentifier="default",
                guardrailVersion="1",
                source="OUTPUT",
                content=[{"text": {"text": content}}],
            )

            action = response.get("action")
            if action == "BLOCKED":
                violations.append(
                    {
                        "type": "guardrail",
                        "action": "BLOCKED",
                        "assessments": response.get("assessments", []),
                    }
                )

        except Exception as e:
            logger.error(f"Guardrails check failed: {e}")
            # Don't fail validation if guardrails unavailable
            pass

        return violations
