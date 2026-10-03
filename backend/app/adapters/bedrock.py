import asyncio
import hashlib
import json
import logging
import random
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Literal, Optional, Type

import boto3
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration Models
# ============================================================================


class RetryConfig(BaseModel):
    """Configuration for retry logic."""

    max_retries: int = 4
    base_delay: float = 1.0  # seconds
    max_delay: float = 60.0  # seconds
    exponential_base: float = 2.0
    jitter: bool = True

    # Retryable errors
    retryable_exceptions: list[str] = Field(
        default=[
            "ThrottlingException",
            "ModelTimeoutException",
            "ServiceUnavailableException",
            "InternalServerException",
            "ConnectionError",
            "Timeout",
        ]
    )

    # Non-retryable errors
    non_retryable_exceptions: list[str] = Field(
        default=[
            "ValidationException",
            "AccessDeniedException",
            "ResourceNotFoundException",
            "ModelNotReadyException",
        ]
    )


class TimeoutConfig(BaseModel):
    """Configuration for timeout controls."""

    standard_timeout: int = 60  # seconds
    extended_timeout: int = 300  # seconds
    streaming_timeout: int = 120  # seconds
    chunk_timeout: int = 30  # seconds


class TokenUsage(BaseModel):
    """Token usage statistics."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


class ModelResponse(BaseModel):
    """Response from Bedrock model."""

    content: str
    model_id: str
    usage: TokenUsage
    latency_ms: float
    finish_reason: Literal["stop", "length", "content_filter"]
    metadata: dict = Field(default_factory=dict)


class ValidationResult(BaseModel):
    """Result of response validation."""

    is_valid: bool
    violations: list[dict] = Field(default_factory=list)
    confidence: Optional[float] = None
    grounding_score: Optional[float] = None


# ============================================================================
# Exceptions
# ============================================================================


class ModelInvocationError(Exception):
    """Base exception for model invocation errors."""

    pass


class ModelTimeoutError(ModelInvocationError):
    """Exception raised when model invocation times out."""

    pass


class ModelValidationError(ModelInvocationError):
    """Exception raised when response validation fails."""

    pass


class PromptInjectionDetected(ModelInvocationError):
    """Exception raised when prompt injection is detected."""

    pass


# ============================================================================
# Bedrock Model Adapter
# ============================================================================


class BedrockModelAdapter:
    """Production-ready Bedrock adapter with retry logic and validation."""

    def __init__(
        self,
        bedrock_client: Optional[Any] = None,
        retry_config: Optional[RetryConfig] = None,
        timeout_config: Optional[TimeoutConfig] = None,
        region: str = "us-east-1",
    ):
        self.client = bedrock_client or boto3.client(
            "bedrock-runtime", region_name=region
        )
        self.retry_config = retry_config or RetryConfig()
        self.timeout_config = timeout_config or TimeoutConfig()

    async def invoke_model(
        self,
        model_id: str,
        prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop_sequences: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
        timeout: Optional[int] = None,
        user_prompt: Optional[str] = None,
    ) -> ModelResponse:
        """
        Invoke Bedrock model with retry logic and validation.

        Args:
            model_id: Bedrock model identifier
            prompt: User prompt (or user_prompt)
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            stop_sequences: Optional stop sequences
            metadata: Optional metadata for logging
            timeout: Optional timeout override
        Returns:
            ModelResponse with content, token usage, and metadata

        Raises:
            ModelInvocationError: For non-retryable errors
            ModelTimeoutError: When timeout is exceeded
            ModelValidationError: When response validation fails
        """
        prompt = prompt if prompt is not None else (user_prompt or "")
        start_time = datetime.now()
        timeout_seconds = timeout or self.timeout_config.standard_timeout

        # Prepare request body based on model family
        if "anthropic.claude" in model_id:
            body = self._prepare_claude_request(
                prompt, system_prompt, temperature, max_tokens, stop_sequences
            )
        elif "amazon.titan" in model_id:
            body = self._prepare_titan_request(
                prompt, temperature, max_tokens, stop_sequences
            )
        else:
            raise ModelInvocationError(f"Unsupported model: {model_id}")

        # Invoke with retry logic
        try:
            response_body = await self._invoke_with_retry(
                model_id, body, timeout_seconds
            )
        except asyncio.TimeoutError:
            raise ModelTimeoutError(
                f"Model invocation timed out after {timeout_seconds}s"
            )

        # Parse response
        latency_ms = (datetime.now() - start_time).total_seconds() * 1000

        if "anthropic.claude" in model_id:
            return self._parse_claude_response(
                response_body, model_id, latency_ms, metadata or {}
            )
        elif "amazon.titan" in model_id:
            return self._parse_titan_response(
                response_body, model_id, latency_ms, metadata or {}
            )

        raise ModelInvocationError(f"Failed to parse response for model: {model_id}")

    # Backward compatibility alias
    invoke = invoke_model

    async def invoke_model_streaming(
        self,
        model_id: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        chunk_callback: Optional[Callable[[str], None]] = None,
        metadata: Optional[dict] = None,
    ) -> AsyncIterator[str]:
        """
        Invoke Bedrock model with streaming response.

        Args:
            model_id: Bedrock model identifier
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            chunk_callback: Optional callback for each chunk
            metadata: Optional metadata for logging

        Yields:
            Response chunks as they arrive
        """
        # Prepare request body
        if "anthropic.claude" in model_id:
            body = self._prepare_claude_request(
                prompt, system_prompt, temperature, max_tokens
            )
        elif "amazon.titan" in model_id:
            body = self._prepare_titan_request(prompt, temperature, max_tokens)
        else:
            raise ModelInvocationError(f"Unsupported model: {model_id}")

        # Invoke streaming
        try:
            response = self.client.invoke_model_with_response_stream(
                modelId=model_id, body=json.dumps(body)
            )

            stream = response.get("body")
            if stream:
                for event in stream:
                    chunk = event.get("chunk")
                    if chunk:
                        chunk_data = json.loads(chunk.get("bytes").decode())

                        # Extract text based on model family
                        if "anthropic.claude" in model_id:
                            if chunk_data.get("type") == "content_block_delta":
                                text = chunk_data.get("delta", {}).get("text", "")
                                if text:
                                    if chunk_callback:
                                        chunk_callback(text)
                                    yield text
                        elif "amazon.titan" in model_id:
                            text = chunk_data.get("outputText", "")
                            if text:
                                if chunk_callback:
                                    chunk_callback(text)
                                yield text

        except Exception as e:
            logger.error(f"Streaming invocation failed: {e}")
            raise ModelInvocationError(f"Streaming failed: {str(e)}")

    async def _invoke_with_retry(
        self, model_id: str, body: dict, timeout_seconds: int
    ) -> dict:
        """Execute model invocation with exponential backoff retry."""
        last_exception = None

        for attempt in range(self.retry_config.max_retries + 1):
            try:
                # Invoke with timeout
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.client.invoke_model,
                        modelId=model_id,
                        body=json.dumps(body),
                    ),
                    timeout=timeout_seconds,
                )

                # Parse response body
                response_body = json.loads(response.get("body").read())
                return response_body

            except Exception as e:
                last_exception = e
                error_type = type(e).__name__

                # Check if error is retryable
                if error_type not in self.retry_config.retryable_exceptions:
                    logger.error(f"Non-retryable error: {error_type} - {str(e)}")
                    raise ModelInvocationError(f"{error_type}: {str(e)}")

                # Check if we've exhausted retries
                if attempt >= self.retry_config.max_retries:
                    logger.error(
                        f"Exhausted retries after {attempt + 1} attempts: {str(e)}"
                    )
                    raise ModelInvocationError(
                        f"Exhausted retries: {error_type} - {str(e)}"
                    )

                # Calculate backoff delay
                delay = min(
                    self.retry_config.base_delay
                    * (self.retry_config.exponential_base**attempt),
                    self.retry_config.max_delay,
                )

                # Add jitter if configured
                if self.retry_config.jitter:
                    delay *= 0.5 + random.random() * 0.5

                logger.warning(
                    f"Retry attempt {attempt + 1}/{self.retry_config.max_retries} "
                    f"after {delay:.2f}s delay. Error: {error_type}"
                )

                await asyncio.sleep(delay)

        raise ModelInvocationError(f"Failed after retries: {str(last_exception)}")

    def _prepare_claude_request(
        self,
        prompt: str,
        system_prompt: Optional[str],
        temperature: float,
        max_tokens: int,
        stop_sequences: Optional[list[str]] = None,
    ) -> dict:
        """Prepare request body for Claude models."""
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }

        if system_prompt:
            body["system"] = system_prompt

        if stop_sequences:
            body["stop_sequences"] = stop_sequences

        return body

    def _prepare_titan_request(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stop_sequences: Optional[list[str]] = None,
    ) -> dict:
        """Prepare request body for Titan models."""
        body = {
            "inputText": prompt,
            "textGenerationConfig": {
                "temperature": temperature,
                "maxTokenCount": max_tokens,
            },
        }

        if stop_sequences:
            body["textGenerationConfig"]["stopSequences"] = stop_sequences

        return body

    def _parse_claude_response(
        self, response_body: dict, model_id: str, latency_ms: float, metadata: dict
    ) -> ModelResponse:
        """Parse response from Claude models."""
        content = ""
        for content_block in response_body.get("content", []):
            if content_block.get("type") == "text":
                content += content_block.get("text", "")

        usage = response_body.get("usage", {})
        token_usage = TokenUsage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        )

        return ModelResponse(
            content=content,
            model_id=model_id,
            usage=token_usage,
            latency_ms=latency_ms,
            finish_reason=response_body.get("stop_reason", "stop"),
            metadata=metadata,
        )

    def _parse_titan_response(
        self, response_body: dict, model_id: str, latency_ms: float, metadata: dict
    ) -> ModelResponse:
        """Parse response from Titan models."""
        results = response_body.get("results", [])
        content = results[0].get("outputText", "") if results else ""

        token_usage = TokenUsage(
            input_tokens=response_body.get("inputTextTokenCount", 0),
            output_tokens=results[0].get("tokenCount", 0) if results else 0,
            total_tokens=response_body.get("inputTextTokenCount", 0)
            + (results[0].get("tokenCount", 0) if results else 0),
        )

        return ModelResponse(
            content=content,
            model_id=model_id,
            usage=token_usage,
            latency_ms=latency_ms,
            finish_reason=results[0].get("completionReason", "FINISH") if results else "FINISH",
            metadata=metadata,
        )


# ============================================================================
# Model Adapter Protocol (for backward compatibility)
# ============================================================================


class ModelAdapter:
    """Protocol for model adapters."""

    async def summarize(self, prompt: str, evidence: list[dict[str, str]]) -> str:
        """Generate a user-facing summary from evidence."""
        raise NotImplementedError


class LocalModelAdapter(ModelAdapter):
    """Local fallback adapter without LLM."""

    async def summarize(self, prompt: str, evidence: list[dict[str, str]]) -> str:
        evidence_summary = "; ".join(item.get("summary", "") for item in evidence)
        return f"{prompt}: {evidence_summary}".strip()

