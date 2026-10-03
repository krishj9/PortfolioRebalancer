"""Tests for Gemini model adapter and model factory."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.adapters.bedrock import BedrockModelAdapter, ModelInvocationError, ModelResponse, TokenUsage
from app.adapters.gemini import GeminiModelAdapter
from app.adapters.model_factory import get_model_adapter
from app.contracts.analysis import ApprovalArtifact
from app.contracts.common import ActorContext, CorrelationMetadata
from app.contracts.domain import PortfolioHolding, PortfolioSnapshot
from app.main import app
from app.persistence.dependencies import get_workflow_store
from app.persistence.memory_store import InMemoryWorkflowStore


class MockUsageMetadata:
    def __init__(self, prompt=12, candidates=24, thoughts=0, total=36):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.thoughts_token_count = thoughts
        self.total_token_count = total


class MockCandidate:
    def __init__(self, text="Sample response", finish_reason="STOP"):
        self.text = text
        self.finish_reason = finish_reason
        self.content = MagicMock()
        part = MagicMock()
        part.text = text
        self.content.parts = [part]


class MockGenerateContentResponse:
    def __init__(self, text="Sample response", finish_reason="STOP", usage=None):
        self.text = text
        self.candidates = [MockCandidate(text=text, finish_reason=finish_reason)]
        self.usage_metadata = usage or MockUsageMetadata()


class MockStreamChunk:
    def __init__(self, text="chunk"):
        self.text = text


@pytest.mark.asyncio
async def test_gemini_adapter_invoke_model_success():
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=MockGenerateContentResponse(
            text="Rebalance executed safely.",
            finish_reason="STOP",
            usage=MockUsageMetadata(prompt=10, candidates=20, total=30),
        )
    )

    adapter = GeminiModelAdapter(client=mock_client)
    response = await adapter.invoke_model(
        model_id="gemini-2.5-flash",
        prompt="Explain rebalancing rationale",
        system_prompt="You are a financial advisor.",
        temperature=0.3,
        max_tokens=250,
    )

    assert isinstance(response, ModelResponse)
    assert response.content == "Rebalance executed safely."
    assert response.model_id == "gemini-2.5-flash"
    assert response.finish_reason == "stop"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 20
    assert response.usage.total_tokens == 30
    assert response.latency_ms >= 0


@pytest.mark.asyncio
async def test_gemini_adapter_finish_reasons():
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=MockGenerateContentResponse(
            text="Truncated text",
            finish_reason="MAX_TOKENS",
        )
    )

    adapter = GeminiModelAdapter(client=mock_client)
    response = await adapter.invoke_model(
        model_id="gemini-2.5-flash",
        prompt="Explain",
    )
    assert response.finish_reason == "length"

    # Test safety / content filter
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=MockGenerateContentResponse(
            text="",
            finish_reason="SAFETY",
        )
    )
    response = await adapter.invoke_model(
        model_id="gemini-2.5-flash",
        prompt="Explain",
    )
    assert response.finish_reason == "content_filter"


@pytest.mark.asyncio
async def test_gemini_adapter_model_id_mapping():
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=MockGenerateContentResponse(text="ok")
    )

    adapter = GeminiModelAdapter(client=mock_client, default_model="gemini-2.5-flash")

    # Non-Gemini Claude model ID should fall back to default Gemini model
    await adapter.invoke_model(
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        prompt="test",
    )
    call_args = mock_client.aio.models.generate_content.call_args
    assert call_args.kwargs["model"] == "gemini-2.5-flash"

    # Explicit Gemini model ID should be preserved
    await adapter.invoke_model(
        model_id="gemini-2.0-flash",
        prompt="test",
    )
    call_args = mock_client.aio.models.generate_content.call_args
    assert call_args.kwargs["model"] == "gemini-2.0-flash"


@pytest.mark.asyncio
async def test_gemini_adapter_streaming():
    mock_client = MagicMock()

    async def mock_stream_generator():
        for chunk in [MockStreamChunk("Hello "), MockStreamChunk("World"), MockStreamChunk("!")]:
            yield chunk

    mock_client.aio.models.generate_content_stream = AsyncMock(
        return_value=mock_stream_generator()
    )

    adapter = GeminiModelAdapter(client=mock_client)
    received_callback = []

    def on_chunk(c: str):
        received_callback.append(c)

    chunks = []
    async for chunk in adapter.invoke_model_streaming(
        model_id="gemini-2.5-flash",
        prompt="Stream this",
        chunk_callback=on_chunk,
    ):
        chunks.append(chunk)

    assert "".join(chunks) == "Hello World!"
    assert "".join(received_callback) == "Hello World!"


def test_model_factory_selection():
    # Explicit gemini
    gemini_adapter = get_model_adapter("gemini")
    assert isinstance(gemini_adapter, GeminiModelAdapter)

    # Explicit bedrock
    bedrock_adapter = get_model_adapter("bedrock")
    assert isinstance(bedrock_adapter, BedrockModelAdapter)

    # Configured provider
    with patch("app.adapters.model_factory.get_llm_config") as mock_cfg:
        mock_cfg.return_value.provider = "bedrock"
        adapter = get_model_adapter()
        assert isinstance(adapter, BedrockModelAdapter)

        mock_cfg.return_value.provider = "gemini"
        adapter = get_model_adapter()
        assert isinstance(adapter, GeminiModelAdapter)


@pytest.mark.asyncio
async def test_explain_route_streams_from_gemini():
    store = InMemoryWorkflowStore()
    app.dependency_overrides[get_workflow_store] = lambda: store

    from app.contracts.workflow import PortfolioRebalanceRequest
    from app.services.orchestrator import Orchestrator
    from tests.test_rebalance import request_payload

    try:
        req = PortfolioRebalanceRequest.model_validate(request_payload())
        orchestrator = Orchestrator(store)
        orch_res = await orchestrator.run(req)
        approval_id = orch_res.approval_artifact.approval_id

        async def mock_stream(*args, **kwargs):
            for token in ["This ", "portfolio ", "is ", "well ", "balanced."]:
                yield token

        mock_gemini = MagicMock(spec=GeminiModelAdapter)
        mock_gemini.invoke_model_streaming = mock_stream

        with (
            patch("app.api.routes.explain.get_model_adapter", return_value=mock_gemini),
            patch("app.api.routes.explain.get_feature_flags") as mock_ff,
        ):
            mock_ff.return_value.risk_agent_llm_enabled = True

            client = TestClient(app)
            response = client.post(
                f"/api/recommendations/{approval_id}/explain",
                json={"question": "Why rebalance now?"},
            )
            assert response.status_code == 200
            text = response.text
            assert "This" in text
            assert "balanced" in text
            assert '"type": "done"' in text
    finally:
        app.dependency_overrides.clear()



@pytest.mark.asyncio
async def test_gemini_adapter_retry_on_transient_error():
    from app.adapters.bedrock import RetryConfig
    from google.genai.errors import APIError

    mock_client = MagicMock()
    success_resp = MockGenerateContentResponse(text="Success after retry")

    err = APIError(429, {"message": "Rate limit exceeded"})

    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=[err, success_resp]
    )

    retry_cfg = RetryConfig(max_retries=2, base_delay=0.01, jitter=False)
    adapter = GeminiModelAdapter(client=mock_client, retry_config=retry_cfg)

    response = await adapter.invoke_model("gemini-2.5-flash", prompt="Hello")
    assert response.content == "Success after retry"
    assert mock_client.aio.models.generate_content.call_count == 2


@pytest.mark.asyncio
async def test_gemini_adapter_fatal_error():
    from app.adapters.bedrock import RetryConfig
    from google.genai.errors import APIError

    mock_client = MagicMock()
    err = APIError(400, {"message": "Invalid argument"})

    mock_client.aio.models.generate_content = AsyncMock(side_effect=err)

    retry_cfg = RetryConfig(max_retries=2, base_delay=0.01, jitter=False)
    adapter = GeminiModelAdapter(client=mock_client, retry_config=retry_cfg)

    with pytest.raises(ModelInvocationError):
        await adapter.invoke_model("gemini-2.5-flash", prompt="Hello")

    # Non-retryable error should only be called once
    assert mock_client.aio.models.generate_content.call_count == 1

