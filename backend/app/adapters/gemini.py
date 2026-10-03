import asyncio
import logging
import os
import random
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Literal, Optional

from google import genai
from google.genai import errors, types

from app.adapters.bedrock import (
    ModelInvocationError,
    ModelResponse,
    ModelTimeoutError,
    RetryConfig,
    TimeoutConfig,
    TokenUsage,
)
from app.core.config import get_llm_config

logger = logging.getLogger(__name__)


class GeminiModelAdapter:
    """Production-ready Gemini adapter for Vertex AI using google-genai SDK."""

    def __init__(
        self,
        client: Optional[Any] = None,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        default_model: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        timeout_config: Optional[TimeoutConfig] = None,
        thinking_budget: Optional[int] = 0,
    ):
        llm_config = get_llm_config()
        self.project_id = (
            project_id
            or getattr(llm_config, "gemini_project_id", None)
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or "mybrightday-dev"
        )
        self.location = (
            location
            or getattr(llm_config, "gemini_location", None)
            or "us-central1"
        )
        self.default_model = (
            default_model
            or getattr(llm_config, "gemini_model", None)
            or "gemini-2.5-flash"
        )
        self.client = client or genai.Client(
            vertexai=True,
            project=self.project_id,
            location=self.location,
        )
        self.retry_config = retry_config or RetryConfig()
        self.timeout_config = timeout_config or TimeoutConfig()
        self.thinking_budget = thinking_budget

    def _resolve_model(self, model_id: Optional[str]) -> str:
        """Resolve model ID, falling back to default Gemini model if non-Gemini ID is passed."""
        if model_id and "gemini" in model_id.lower():
            return model_id
        if model_id:
            logger.debug(
                f"Non-Gemini model ID '{model_id}' provided to Gemini adapter; "
                f"resolving to default '{self.default_model}'"
            )
        return self.default_model

    def _map_finish_reason(
        self, raw_reason: Any
    ) -> Literal["stop", "length", "content_filter"]:
        """Map Gemini finish reason to standard finish reason."""
        if raw_reason is None:
            return "stop"
        reason_str = str(raw_reason).upper()
        if "MAX_TOKENS" in reason_str:
            return "length"
        if any(filter_kw in reason_str for filter_kw in ["SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED", "SPII"]):
            return "content_filter"
        return "stop"

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
        Invoke Gemini model with retry logic and token usage extraction.

        Args:
            model_id: Model identifier (or fallback to default Gemini model)
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            stop_sequences: Optional stop sequences
            metadata: Optional metadata for logging
            timeout: Optional timeout override in seconds
            user_prompt: Alias for prompt

        Returns:
            ModelResponse with content, token usage, latency, and metadata
        """
        prompt_text = prompt if prompt is not None else (user_prompt or "")
        resolved_model = self._resolve_model(model_id)
        timeout_seconds = timeout or self.timeout_config.standard_timeout
        start_time = datetime.now()

        config = types.GenerateContentConfig(
            system_instruction=system_prompt if system_prompt else None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            stop_sequences=stop_sequences if stop_sequences else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget)
            if self.thinking_budget is not None
            else None,
        )

        try:
            response = await self._generate_with_retry(
                model=resolved_model,
                contents=prompt_text,
                config=config,
                timeout_seconds=timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise ModelTimeoutError(
                f"Gemini model invocation timed out after {timeout_seconds}s"
            )

        latency_ms = (datetime.now() - start_time).total_seconds() * 1000

        # Extract text content
        content = ""
        try:
            if response.text:
                content = response.text
        except Exception:
            pass

        if not content and response.candidates:
            cand = response.candidates[0]
            if cand.content and cand.content.parts:
                content = "".join(
                    getattr(part, "text", "") or "" for part in cand.content.parts
                )

        # Extract token usage
        usage_meta = response.usage_metadata
        input_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
        cand_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
        thought_tokens = getattr(usage_meta, "thoughts_token_count", 0) or 0
        output_tokens = cand_tokens if cand_tokens > 0 else thought_tokens
        total_tokens = getattr(usage_meta, "total_token_count", 0) or (input_tokens + output_tokens)

        token_usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

        # Finish reason
        raw_finish_reason = None
        if response.candidates:
            raw_finish_reason = getattr(response.candidates[0], "finish_reason", None)
        finish_reason = self._map_finish_reason(raw_finish_reason)

        return ModelResponse(
            content=content,
            model_id=resolved_model,
            usage=token_usage,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            metadata=metadata or {},
        )

    # Backward compatibility alias
    invoke = invoke_model

    async def invoke_model_streaming(
        self,
        model_id: str,
        prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        chunk_callback: Optional[Callable[[str], None]] = None,
        metadata: Optional[dict] = None,
        user_prompt: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """
        Invoke Gemini model with streaming response.

        Args:
            model_id: Model identifier (or fallback to default Gemini model)
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            chunk_callback: Optional callback invoked for each chunk
            metadata: Optional metadata for logging
            user_prompt: Alias for prompt

        Yields:
            Response text chunks as they arrive
        """
        prompt_text = prompt if prompt is not None else (user_prompt or "")
        resolved_model = self._resolve_model(model_id)

        config = types.GenerateContentConfig(
            system_instruction=system_prompt if system_prompt else None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget)
            if self.thinking_budget is not None
            else None,
        )

        try:
            response_stream = await self.client.aio.models.generate_content_stream(
                model=resolved_model,
                contents=prompt_text,
                config=config,
            )

            async for chunk in response_stream:
                text = getattr(chunk, "text", "") or ""
                if text:
                    if chunk_callback:
                        chunk_callback(text)
                    yield text

        except Exception as e:
            logger.error(f"Gemini streaming invocation failed: {e}")
            raise ModelInvocationError(f"Gemini streaming failed: {str(e)}")

    async def _generate_with_retry(
        self,
        model: str,
        contents: str,
        config: types.GenerateContentConfig,
        timeout_seconds: int,
    ) -> Any:
        """Execute model invocation with exponential backoff retry."""
        last_exception = None

        for attempt in range(self.retry_config.max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=timeout_seconds,
                )
                return response

            except asyncio.TimeoutError:
                raise
            except Exception as e:
                last_exception = e
                error_type = type(e).__name__
                is_retryable = False

                # Handle Google GenAI API errors
                if isinstance(e, errors.APIError):
                    code = getattr(e, "code", None)
                    # 429: Too Many Requests, 500: Internal, 503: Unavailable
                    if code in (429, 500, 503, 504):
                        is_retryable = True
                elif isinstance(e, (ConnectionError, TimeoutError)):
                    is_retryable = True

                if not is_retryable or attempt >= self.retry_config.max_retries:
                    logger.error(
                        f"Gemini call failed (retryable={is_retryable}, attempt={attempt + 1}): {error_type} - {str(e)}"
                    )
                    raise ModelInvocationError(f"{error_type}: {str(e)}")

                delay = min(
                    self.retry_config.base_delay
                    * (self.retry_config.exponential_base**attempt),
                    self.retry_config.max_delay,
                )
                if self.retry_config.jitter:
                    delay *= 0.5 + random.random() * 0.5

                logger.warning(
                    f"Retry attempt {attempt + 1}/{self.retry_config.max_retries} "
                    f"after {delay:.2f}s delay. Error: {error_type}"
                )
                await asyncio.sleep(delay)

        raise ModelInvocationError(f"Failed after retries: {str(last_exception)}")
