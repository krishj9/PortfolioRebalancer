"""Tests for OpenTelemetry trace linkage across API, Runtime, Nodes, and Tools (Task P4-01)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.adapters.telemetry import (
    create_traceparent,
    parse_traceparent,
    format_cloud_trace_context,
    inject_trace_headers,
    telemetry_span,
    get_current_trace_id,
    get_current_span_id,
    get_span_recorder,
    InMemorySpanRecorder,
)
from app.main import app
from app.tools.client import ToolClient
from app.services.runtime_client import RuntimeClient
from app.agent_runtime.app import RebalanceGraphApp


@pytest.fixture(autouse=True)
def reset_recorder():
    """Reset span recorder before and after each test."""
    recorder = get_span_recorder()
    recorder.clear()
    yield
    recorder.clear()


def test_w3c_traceparent_generation_and_parsing():
    """Verify standard W3C traceparent formatting and parsing."""
    tp = create_traceparent()
    assert tp.startswith("00-")
    parts = tp.split("-")
    assert len(parts) == 4
    parsed = parse_traceparent(tp)
    assert parsed["trace_id"] == parts[1]
    assert parsed["span_id"] == parts[2]
    assert parsed["trace_flags"] == parts[3]
    assert len(parsed["trace_id"]) == 32
    assert len(parsed["span_id"]) == 16


def test_cloud_trace_context_formatting():
    """Verify Google Cloud Logging trace resource string format."""
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    cloud_trace = format_cloud_trace_context("mybrightday-dev", trace_id)
    assert cloud_trace["logging.googleapis.com/trace"] == "projects/mybrightday-dev/traces/4bf92f3577b34da6a3ce929d0e0e4736"


def test_api_middleware_preserves_incoming_traceparent():
    """Verify trace_middleware retains client-provided traceparent and run-id."""
    client = TestClient(app)
    incoming_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    incoming_run_id = "run-client-999"

    resp = client.get(
        "/health",
        headers={
            "traceparent": incoming_tp,
            "x-run-id": incoming_run_id,
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Trace-ID") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert resp.headers.get("X-Run-ID") == incoming_run_id
    assert resp.headers.get("traceparent") is not None
    assert "4bf92f3577b34da6a3ce929d0e0e4736" in resp.headers.get("traceparent")


def test_api_middleware_generates_traceparent_if_missing():
    """Verify trace_middleware mints new traceparent when client sends none."""
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    trace_id = resp.headers.get("X-Trace-ID")
    assert trace_id is not None
    assert len(trace_id) == 32
    tp = resp.headers.get("traceparent")
    assert tp is not None
    assert trace_id in tp


def test_tool_client_injects_trace_headers():
    """Verify ToolClient injects current traceparent and X-Run-ID in outbound requests."""
    tp = "00-11112222333344445555666677778888-9999000011112222-01"
    run_id = "run-trace-test-123"

    with telemetry_span("test.parent_operation", traceparent=tp, attributes={"run_id": run_id}):
        client = ToolClient(base_url="http://localhost:8001")
        headers = client._get_headers()
        assert "traceparent" in headers
        assert "11112222333344445555666677778888" in headers["traceparent"]
        assert headers.get("X-Run-ID") == run_id


@pytest.mark.asyncio
async def test_hierarchical_span_recording_across_workflow():
    """Verify nested spans record parent-child hierarchy and preserve trace_id."""
    recorder = get_span_recorder()
    root_traceparent = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"

    with telemetry_span("api.request", traceparent=root_traceparent, attributes={"run_id": "run-001"}):
        with telemetry_span("agent_runtime.query", attributes={"run_id": "run-001"}):
            with telemetry_span("node.validate_request", attributes={"request_id": "req-1"}):
                pass
            with telemetry_span("tool.validate", attributes={"rule_count": 5}):
                pass
            with telemetry_span("node.persist_workflow_artifacts", attributes={"proposal_id": "prop-1"}):
                with telemetry_span("tool.persist_proposal", attributes={"proposal_id": "prop-1"}):
                    pass

    spans = recorder.get_spans()
    assert len(spans) == 6

    span_names = [s.name for s in spans]
    assert "node.validate_request" in span_names
    assert "tool.validate" in span_names
    assert "tool.persist_proposal" in span_names
    assert "node.persist_workflow_artifacts" in span_names
    assert "agent_runtime.query" in span_names
    assert "api.request" in span_names

    # Verify all spans in the workflow share the exact same trace_id
    for span in spans:
        assert span.trace_id == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", f"Span {span.name} did not share root trace_id"

    # Verify attributes
    persist_span = next(s for s in spans if s.name == "node.persist_workflow_artifacts")
    assert persist_span.attributes.get("proposal_id") == "prop-1"


def test_agent_runtime_query_propagates_incoming_traceparent():
    """Verify RebalanceGraphApp.query() accepts and attaches traceparent to response."""
    app_instance = RebalanceGraphApp()
    incoming_tp = "00-55556666777788889999aaaabbbbcccc-1234567890abcdef-01"
    run_id = "test-run-trace-prop"

    result = app_instance.query(
        request={"request_id": "req-test-trace"},
        run_id=run_id,
        traceparent=incoming_tp,
    )

    assert result.get("run_id") == run_id
    assert "traceparent" in result
    assert "55556666777788889999aaaabbbbcccc" in result["traceparent"]

    # Verify spans recorded for this query
    recorder = get_span_recorder()
    spans = recorder.get_spans()
    runtime_spans = [s for s in spans if s.name == "agent_runtime.query"]
    assert len(runtime_spans) >= 1
    assert runtime_spans[0].trace_id == "55556666777788889999aaaabbbbcccc"
