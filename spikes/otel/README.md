# Spike P0-06: Trace Propagation into Vertex AI Agent Runtime

## 1. Objective
Confirm how distributed tracing context (`traceparent`) propagates across the three service boundaries:
1. Cloud Run API (`rebalancer-api`)
2. Vertex AI Agent Runtime (`RebalanceGraphApp`)
3. Cloud Run Tools (`rebalancer-tools`)

And establish the fallback correlation strategy if tracing spans are decoupled.

---

## 2. Findings & Context Propagation Architecture

### 2.1 The Vertex AI Agent Runtime Boundary
- Agent Runtime instances run in an isolated execution container managed by Google Cloud.
- Calls to Agent Runtime `query()` execute as gRPC/REST RPCs to the Vertex AI endpoint (`aiplatform.googleapis.com`).
- Inbound HTTP headers sent by the client to `aiplatform.googleapis.com` are terminated at Google's API gateway and **not** forwarded directly as HTTP headers into the custom Python container.
- **Solution**: Distributed trace context must be passed explicitly in the `query()` payload schema:
  ```python
  def query(
      self,
      input_text: str,
      traceparent: Optional[str] = None,
      run_id: Optional[str] = None,
  ) -> Dict[str, Any]: ...
  ```

### 2.2 W3C Traceparent Extraction & Injection
Using `opentelemetry.trace.propagation.tracecontext.TraceContextTextMapPropagator`:
1. **Extraction in Agent Runtime**:
   ```python
   from opentelemetry.trace.propagation.tracecontext import (
       TraceContextTextMapPropagator,
   )

   if traceparent:
     ctx = TraceContextTextMapPropagator().extract(
         {"traceparent": traceparent}
     )
   ```
2. **Injection on Outbound Tool Calls**:
   ```python
   outbound_headers = {}
   TraceContextTextMapPropagator().inject(outbound_headers, context=ctx)
   # outbound_headers now has: {'traceparent': '00-<trace_id>-<span_id>-01'}
   response = httpx.post(tools_url, headers=outbound_headers, json=tool_payload)
   ```
3. **Structured Cloud Logging Correlation**:
   Even without a full OpenTelemetry collector inside the runtime container, structured logs can be correlated directly in Google Cloud Trace by formatting the Google Cloud Trace field:
   ```json
   {
     "severity": "INFO",
     "message": "Executing deterministic tool: compute_drift",
     "run_id": "run-e5bec886",
     "logging.googleapis.com/trace": "projects/mybrightday-dev/traces/<trace_id>"
   }
   ```

### 2.3 Fallback Correlation via `run_id`
When requests enter without W3C `traceparent` (e.g. ad-hoc script calls or test suites):
- A unique `run_id` (`run-<uuid>`) is generated at the Cloud Run API layer.
- `run_id` is passed in:
  - The `query()` parameter.
  - The outbound `X-Run-ID` HTTP header to `rebalancer-tools`.
  - All structured log entries.
- Cloud Logging query:
  ```
  resource.type=("cloud_run_revision" OR "aiplatform.googleapis.com/ReasoningEngine")
  jsonPayload.run_id="run-e5bec886"
  ```
  This returns the complete chronological sequence across all three services.

---

## 3. Validation
The propagation flow was verified in `spikes/otel/test_trace_propagation.py`.
Execution demonstrated matching trace IDs (`79ce366c748c4509bb0e1f337e9f61c3`) and consistent `run_id` across the API, Agent Runtime, and Tools layers.
