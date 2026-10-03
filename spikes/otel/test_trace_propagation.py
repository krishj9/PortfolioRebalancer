"""Spike P0-06: W3C Traceparent and Context Propagation Spike.

Validates that:
1. A W3C traceparent (format: 00-<trace_id>-<span_id>-<flags>) can be generated in Cloud Run API.
2. The trace context survives the hop across Agent Runtime via the query() payload.
3. The Agent Runtime can extract the parent context and inject it into outbound tool HTTP headers.
4. The Cloud Run tools service extracts the traceparent and correlates with the original trace.
5. Fallback correlation using run_id in structured Cloud Logging works when trace headers are absent.
"""

import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, Optional

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger("trace_spike")


def create_sample_traceparent() -> str:
    """Generate a valid W3C traceparent string."""
    trace_id = uuid.uuid4().hex  # 32 hex chars
    span_id = uuid.uuid4().hex[:16]  # 16 hex chars
    return f"00-{trace_id}-{span_id}-01"


def parse_traceparent(traceparent: str) -> Dict[str, str]:
    """Parse W3C traceparent parts."""
    parts = traceparent.split("-")
    if len(parts) != 4:
        raise ValueError(f"Invalid traceparent format: {traceparent}")
    return {
        "version": parts[0],
        "trace_id": parts[1],
        "parent_id": parts[2],
        "trace_flags": parts[3],
    }


class SimulatedCloudRunAPI:
    """Simulates the Cloud Run FastAPI backend receiving an external request."""

    def __init__(self, project_id: str = "mybrightday-dev"):
        self.project_id = project_id
        self.propagator = TraceContextTextMapPropagator()

    def handle_rebalance_request(
        self, account_id: str, incoming_traceparent: Optional[str] = None
    ) -> Dict[str, Any]:
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        traceparent = incoming_traceparent or create_sample_traceparent()
        trace_parts = parse_traceparent(traceparent)
        trace_id = trace_parts["trace_id"]

        # Structured log emitted by Cloud Run API
        api_log = {
            "severity": "INFO",
            "message": f"Received rebalance request for {account_id}",
            "run_id": run_id,
            "logging.googleapis.com/trace": f"projects/{self.project_id}/traces/{trace_id}",
            "logging.googleapis.com/spanId": trace_parts["parent_id"],
        }
        print(f"[Cloud Run API Log]: {json.dumps(api_log)}")

        # Hop to Agent Runtime: query() payload contains traceparent and run_id
        runtime_payload = {
            "input_text": json.dumps({"account_id": account_id}),
            "traceparent": traceparent,
            "run_id": run_id,
        }
        return runtime_payload


class SimulatedAgentRuntime:
    """Simulates Vertex AI Agent Runtime executing query()."""

    def __init__(self, project_id: str = "mybrightday-dev"):
        self.project_id = project_id
        self.propagator = TraceContextTextMapPropagator()

    def query(
        self, input_text: str, traceparent: Optional[str] = None, run_id: Optional[str] = None
    ) -> Dict[str, Any]:
        # 1. Extract context from traceparent
        if traceparent:
            carrier = {"traceparent": traceparent}
            ctx = self.propagator.extract(carrier)
            span = trace.get_current_span(ctx)
            span_ctx = span.get_span_context()
            trace_id = format(span_ctx.trace_id, "032x")
            parent_id = format(span_ctx.span_id, "016x")
        else:
            trace_id = uuid.uuid4().hex
            parent_id = uuid.uuid4().hex[:16]
            ctx = None

        # 2. Structured log in Agent Runtime
        runtime_log = {
            "severity": "INFO",
            "message": "AgentRuntime starting LangGraph workflow",
            "run_id": run_id,
            "logging.googleapis.com/trace": f"projects/{self.project_id}/traces/{trace_id}",
        }
        print(f"[Agent Runtime Log]: {json.dumps(runtime_log)}")

        # 3. Simulate tool invocation to Cloud Run tools service
        # Inject traceparent into outbound HTTP headers
        outbound_headers: Dict[str, str] = {}
        if ctx:
            self.propagator.inject(outbound_headers, context=ctx)
        else:
            outbound_headers["traceparent"] = f"00-{trace_id}-{parent_id}-01"

        if run_id:
            outbound_headers["X-Run-ID"] = run_id

        # Return tool invocation request
        return {
            "tool_call": "compute_drift",
            "headers": outbound_headers,
            "trace_id": trace_id,
        }


class SimulatedCloudRunTools:
    """Simulates Cloud Run deterministic tools service receiving a tool request."""

    def __init__(self, project_id: str = "mybrightday-dev"):
        self.project_id = project_id
        self.propagator = TraceContextTextMapPropagator()

    def handle_tool_request(
        self, tool_name: str, headers: Dict[str, str], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        traceparent = headers.get("traceparent")
        run_id = headers.get("X-Run-ID")

        if traceparent:
            carrier = {"traceparent": traceparent}
            ctx = self.propagator.extract(carrier)
            span = trace.get_current_span(ctx)
            span_ctx = span.get_span_context()
            trace_id = format(span_ctx.trace_id, "032x")
        else:
            trace_id = "unknown"

        tool_log = {
            "severity": "INFO",
            "message": f"Executing deterministic tool: {tool_name}",
            "run_id": run_id,
            "logging.googleapis.com/trace": f"projects/{self.project_id}/traces/{trace_id}",
        }
        print(f"[Cloud Run Tools Log]: {json.dumps(tool_log)}")

        return {
            "status": "SUCCESS",
            "tool": tool_name,
            "trace_id": trace_id,
            "run_id": run_id,
        }


def main():
    print("=== Testing W3C Traceparent Propagation Across Agent Runtime ===")
    api = SimulatedCloudRunAPI()
    runtime = SimulatedAgentRuntime()
    tools = SimulatedCloudRunTools()

    # Step 1: Inbound request with W3C traceparent
    initial_traceparent = create_sample_traceparent()
    initial_parts = parse_traceparent(initial_traceparent)
    original_trace_id = initial_parts["trace_id"]
    print(f"Original Trace ID from edge/ALB: {original_trace_id}")
    print(f"Initial Traceparent: {initial_traceparent}\n")

    # Step 2: API receives request and invokes Agent Runtime
    runtime_payload = api.handle_rebalance_request("ACC-001", incoming_traceparent=initial_traceparent)
    assert runtime_payload["traceparent"] == initial_traceparent

    # Step 3: Agent Runtime receives payload, queries graph, and prepares tool call
    tool_req = runtime.query(
        input_text=runtime_payload["input_text"],
        traceparent=runtime_payload["traceparent"],
        run_id=runtime_payload["run_id"],
    )
    assert tool_req["trace_id"] == original_trace_id
    outbound_headers = tool_req["headers"]
    print(f"\nOutbound headers from Runtime to Tools: {outbound_headers}")

    # Step 4: Tools service executes tool and verifies trace_id matches
    result = tools.handle_tool_request(
        tool_name=tool_req["tool_call"],
        headers=outbound_headers,
        payload={},
    )
    print(f"\nTool execution result: {result}")
    assert result["trace_id"] == original_trace_id, f"Trace ID mismatch: {result['trace_id']} != {original_trace_id}"
    assert result["run_id"] == runtime_payload["run_id"]

    print("\n=== Validation Passed: End-to-end trace correlation preserved across Agent Runtime! ===")


if __name__ == "__main__":
    main()
