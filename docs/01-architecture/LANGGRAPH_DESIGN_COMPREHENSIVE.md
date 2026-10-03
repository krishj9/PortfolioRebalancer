# LangGraph Architecture and LLM Integration Design

## Executive Summary

This document provides a comprehensive explanation of the LangGraph-based orchestration architecture for the Asset Management platform's portfolio rebalancing workflow. The system transforms a hardcoded multi-agent system into a flexible, LLM-powered decision support platform while maintaining deterministic fallback capabilities and strict quality controls.

The architecture implements a state machine-based workflow using LangGraph, where each node represents a distinct processing stage. LLMs are integrated at strategic points to enhance decision quality, provide explanations, and synthesize complex information—all while maintaining system reliability through feature flags, validation layers, and graceful degradation.

**Key Architectural Principles:**
- **Deterministic Foundation**: All critical calculations remain deterministic and verifiable
- **LLM Enhancement**: LLMs augment (not replace) deterministic logic with explanations and insights
- **Feature Flags**: All LLM features are disabled by default and can be toggled independently
- **Graceful Degradation**: System continues functioning if LLM services fail
- **Quality Assurance**: Multi-layer validation ensures output quality and consistency
- **Observability**: Comprehensive tracing and audit logging for compliance and debugging

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Graph Construction](#graph-construction)
3. [Workflow Execution Flow](#workflow-execution-flow)
4. [LLM Integration Strategy](#llm-integration-strategy)
5. [Agent Implementations](#agent-implementations)
6. [State Management](#state-management)
7. [Error Handling and Resilience](#error-handling-and-resilience)
8. [Quality Assurance](#quality-assurance)
9. [Deployment and Operations](#deployment-and-operations)

---

## Architecture Overview

### System Components

The Asset Management platform consists of four primary components working in concert:

```
┌─────────────────────────────────────────────────────────────────┐
│                     Frontend (Angular)                           │
│                    Port 4200 (Dev Server)                        │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP/REST
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Backend API (FastAPI)                           │
│                    Port 8000 (Docker)                            │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │         LangGraph Orchestrator                           │   │
│  │  ┌────────────────────────────────────────────────────┐  │   │
│  │  │  Workflow Graph with 12 Nodes and Conditional     │  │   │
│  │  │  Routing Logic                                     │  │   │
│  │  └────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
    ┌─────────────┐   ┌──────────────┐   ┌──────────────┐
    │  Research   │   │  Sentiment   │   │  DynamoDB    │
    │  Agent A2A  │   │  Agent MCP   │   │  Local       │
    │  Port 8101  │   │  Port 8201   │   │  Port 55000  │
    └─────────────┘   └──────────────┘   └──────────────┘
```

### LangGraph Workflow Graph

The core of the system is a directed acyclic graph (DAG) with 12 nodes representing distinct processing stages:

```
validate_request
    ├─ [CONDITIONAL] ─────────────────────────────────────┐
    │                                                      │
    ├─ initialize_context_and_trace                       │
    │  └─ log_request_audit_event                         │
    │     ├─ hydrate_memory (parallel)                    │
    │     └─ run_research (parallel)                      │
    │        └─ run_sentiment_analysis                    │
    │           └─ run_portfolio_rebalancing              │
    │              └─ run_risk_policy                     │
    │                 ├─ [CONDITIONAL] ──────────────────┤
    │                 │                                   │
    │                 └─ generate_execution_proposal      │
    │                    └─ assemble_recommendation       │
    │                       └─ apply_output_guardrails    │
    │                          ├─ [CONDITIONAL] ─────────┤
    │                          │                          │
    │                          └─ create_approval_artifact│
    │                             └─ persist_workflow_artifacts
    │                                └─ emit_workflow_audit_event
    │                                   └─ return_response
    │                                      └─ END
    │
    └─ emit_workflow_audit_event ──────────────────────────┘
       └─ return_response
          └─ END
```

---

## Graph Construction

### Building the Workflow Graph

The `build_workflow_graph()` function in `langgraph_graph.py` constructs the complete workflow using LangGraph's StateGraph API:

```python
def build_workflow_graph() -> StateGraph:
    graph = StateGraph(WorkflowGraphState)
    
    # 1. Add all nodes
    graph.add_node("validate_request", validate_request)
    graph.add_node("initialize_context_and_trace", initialize_context_and_trace)
    # ... 10 more nodes
    
    # 2. Set entry point
    graph.set_entry_point("validate_request")
    
    # 3. Add edges (connections between nodes)
    graph.add_edge("initialize_context_and_trace", "log_request_audit_event")
    graph.add_conditional_edges("validate_request", route_after_validation, {...})
    
    # 4. Compile graph
    return graph.compile()
```

### Node Types

**1. Validation and Initialization Nodes**
- `validate_request`: Validates request schema and business rules
- `initialize_context_and_trace`: Sets up tracing context and correlation IDs
- `log_request_audit_event`: Records request received event

**2. Agent Execution Nodes**
- `hydrate_memory`: Retrieves and synthesizes client context (Memory Agent)
- `run_research`: Gathers market research (Research Agent A2A)
- `run_sentiment_analysis`: Analyzes market sentiment (Sentiment Agent MCP)
- `run_portfolio_rebalancing`: Calculates allocation drift (Rebalancing Agent)
- `run_risk_policy`: Evaluates policy compliance (Risk & Compliance Agent)
- `generate_execution_proposal`: Creates trade proposal (Trade Execution Agent)

**3. Output Processing Nodes**
- `assemble_recommendation`: Combines agent outputs into recommendation package
- `apply_output_guardrails`: Applies Bedrock guardrails for safety
- `create_approval_artifact`: Creates human-reviewable approval artifact
- `persist_workflow_artifacts`: Stores results in DynamoDB
- `emit_workflow_audit_event`: Records workflow completion event
- `return_response`: Prepares final response

### Edge Types

**Sequential Edges**: Direct connections between nodes
```python
graph.add_edge("initialize_context_and_trace", "log_request_audit_event")
```

**Parallel Edges**: Multiple nodes can execute concurrently
```python
graph.add_edge("log_request_audit_event", "hydrate_memory")
graph.add_edge("log_request_audit_event", "run_research")
# Both hydrate_memory and run_research execute in parallel
```

**Conditional Edges**: Routing based on state conditions
```python
graph.add_conditional_edges(
    "validate_request",
    route_after_validation,
    {
        "emit_workflow_audit_event": "emit_workflow_audit_event",
        "initialize_context_and_trace": "initialize_context_and_trace",
    },
)
```

---

## Workflow Execution Flow

### Request Lifecycle

#### Phase 1: Request Validation and Initialization

**Node: `validate_request`**
- Validates request schema (performed at API layer)
- Validates business rules:
  - Allocation targets sum to 100%
  - Holdings have positive quantities
  - Risk profile constraints are satisfied
- Sets `validation_error` if validation fails
- Routes to error path if validation fails

**Node: `initialize_context_and_trace`**
- Generates trace ID for distributed tracing
- Initializes trace provider context (Bedrock AgentCore or LangSmith)
- Generates provider-specific trace URLs for debugging
- Stores correlation metadata for audit trail

**Node: `log_request_audit_event`**
- Records "REQUEST_RECEIVED" audit event
- Captures actor ID, session ID, and timestamp
- Stores event ID for correlation

#### Phase 2: Parallel Information Gathering

**Node: `hydrate_memory` (Parallel)**
- Executes Memory Agent to retrieve client context
- If LLM enabled:
  - Generates semantic query for better retrieval
  - Retrieves memories using semantic search
  - Synthesizes memories into coherent context
  - Detects conflicts in memory items
- If LLM disabled or fails:
  - Falls back to keyword-based retrieval
  - Returns raw memories without synthesis
- Stores output in `memory_output` state

**Node: `run_research` (Parallel)**
- Executes Research Agent A2A to gather market research
- Retrieves market context and key insights
- Stores output in `research_output` state

**Node: `run_sentiment_analysis`**
- Waits for both memory and research to complete
- Executes Sentiment Agent MCP
- Analyzes market sentiment for relevant symbols
- Stores output in `sentiment_output` state

#### Phase 3: Portfolio Analysis

**Node: `run_portfolio_rebalancing`**
- Always performs deterministic drift calculation:
  - Calculates current asset allocation
  - Compares against target allocation
  - Identifies holdings outside tolerance bands
- If LLM enabled:
  - Generates LLM-enhanced drift explanation
  - Recommends rebalancing strategy
  - Validates LLM output against deterministic calculations
- Stores output in `rebalancing_output` state

**Node: `run_risk_policy`**
- Evaluates portfolio against risk and compliance policies
- Checks:
  - Single position concentration limits
  - Sector concentration limits
  - Allowed asset classes
  - Tax implications
- Returns policy verdict: COMPLIANT, NON_COMPLIANT, or UNRESOLVED
- Stores output in `risk_policy_output` state

#### Phase 4: Conditional Routing Based on Policy

**Conditional Node: `route_after_risk_policy`**
- If policy verdict is COMPLIANT:
  - Routes to `generate_execution_proposal`
- If policy verdict is NON_COMPLIANT or UNRESOLVED:
  - Marks workflow as BLOCKED
  - Skips trade proposal generation
  - Routes directly to `assemble_recommendation`

**Node: `generate_execution_proposal`**
- Creates specific trade recommendations
- Calculates estimated impact on portfolio
- Determines proposal status: READY_FOR_REVIEW, NO_ACTION_NEEDED, or BLOCKED
- Stores output in `trade_proposal_output` state

#### Phase 5: Output Assembly and Guardrails

**Node: `assemble_recommendation`**
- Combines all agent outputs into unified recommendation package
- Determines final workflow state:
  - NORMAL: All checks passed, ready for review
  - DEGRADED: Some quality issues but still usable
  - BLOCKED: Critical issues prevent recommendation
- Determines approval eligibility based on workflow state
- Stores output in `recommendation_package` state

**Node: `apply_output_guardrails`**
- Applies Bedrock guardrails to recommendation output
- Checks for:
  - Sensitive information (PII, credentials)
  - Harmful content
  - Policy violations
- Returns guardrail action: NONE or BLOCKED
- Stores result in `guardrail_result` state

#### Phase 6: Conditional Routing Based on Guardrails

**Conditional Node: `route_after_guardrails`**
- If guardrails action is NONE:
  - Routes to `create_approval_artifact`
- If guardrails action is BLOCKED:
  - Marks workflow as BLOCKED
  - Skips approval artifact creation
  - Routes directly to `emit_workflow_audit_event`

**Node: `create_approval_artifact`**
- Creates human-reviewable approval artifact
- Includes:
  - Recommendation summary
  - Agent stage results
  - Current vs. target allocation
  - Risk policy verdict
  - Trade proposal details
- Stores artifact in `approval_artifact` state

#### Phase 7: Persistence and Audit

**Node: `persist_workflow_artifacts`**
- Writes recommendation package to DynamoDB
- Writes approval artifact to DynamoDB
- Stores for later retrieval and audit

**Node: `emit_workflow_audit_event`**
- Records "WORKFLOW_COMPLETED" audit event
- Captures final workflow state and outcome
- Stores event ID for correlation

**Node: `return_response`**
- Prepares final response for API client
- Serializes recommendation package and approval artifact
- Returns to frontend

---

## LLM Integration Strategy

### Design Philosophy

The LLM integration follows these core principles:

1. **Deterministic Foundation**: All critical calculations remain deterministic
2. **LLM Enhancement**: LLMs augment with explanations, synthesis, and insights
3. **Feature Flags**: All LLM features disabled by default
4. **Graceful Degradation**: System works without LLMs
5. **Quality Validation**: Multi-layer validation ensures output quality
6. **Cost Optimization**: Different models for different agents based on requirements

### Feature Flags

Each agent has an independent feature flag controlling LLM usage:

```python
FEATURE_MEMORY_AGENT_LLM_ENABLED = false
FEATURE_RESEARCH_AGENT_LLM_ENABLED = false
FEATURE_SENTIMENT_AGENT_LLM_ENABLED = false
FEATURE_REBALANCING_AGENT_LLM_ENABLED = false
FEATURE_RISK_AGENT_LLM_ENABLED = false
FEATURE_TRADE_PROPOSAL_AGENT_LLM_ENABLED = false
```

Flags can be toggled via environment variables or configuration files without code changes.

### Model Selection

Different models are selected based on agent requirements:

| Agent | Model | Rationale | Cost | Quality |
|-------|-------|-----------|------|---------|
| Memory | Claude 3 Haiku | Fast semantic queries, low cost | Low | High |
| Research | Claude 3 Sonnet | Complex analysis, moderate cost | Medium | High |
| Sentiment | Claude 3 Haiku | Classification task, low cost | Low | High |
| Rebalancing | Claude 3 Sonnet | Detailed explanations, moderate cost | Medium | High |
| Risk | Claude 3 Haiku | Policy evaluation, low cost | Low | High |
| Trade Proposal | Claude 3 Sonnet | Complex reasoning, moderate cost | Medium | High |

### Bedrock Model Adapter

The `BedrockModelAdapter` provides production-ready LLM invocation with:

**Retry Logic**
- Exponential backoff with jitter
- Configurable retry limits (default: 4 retries)
- Distinguishes retryable vs. non-retryable errors
- Retryable: ThrottlingException, ServiceUnavailableException, Timeout
- Non-retryable: ValidationException, AccessDeniedException

**Timeout Management**
- Standard timeout: 60 seconds
- Extended timeout: 300 seconds (for complex tasks)
- Streaming timeout: 120 seconds
- Chunk timeout: 30 seconds per chunk

**Token Usage Tracking**
- Tracks input, output, and total tokens
- Enables cost monitoring and optimization
- Provides latency metrics for performance analysis

**Error Handling**
- Distinguishes model errors from infrastructure errors
- Provides detailed error messages for debugging
- Supports graceful fallback to deterministic logic

### Prompt Template System

Prompts are managed as YAML templates with versioning:

```yaml
# prompts/memory-agent/v1.0.0.yaml
semantic_query:
  system_prompt: |
    You are a semantic query generator for portfolio memory retrieval.
    Generate a semantic query that captures the essence of the client's
    investment preferences and constraints.
  
  user_prompt_template: |
    Client ID: {client_id}
    Risk Tolerance: {risk_tolerance}
    Investment Horizon: {investment_horizon}
    Constraints: {constraints}
    
    Generate a semantic query for retrieving relevant memories.
  
  validation:
    output_schema:
      type: object
      properties:
        semantic_query:
          type: string
        keywords:
          type: array
          items:
            type: string
        confidence:
          type: number
          minimum: 0
          maximum: 1
    confidence_threshold: 0.7

memory_synthesis:
  system_prompt: |
    You are a memory synthesis expert. Combine multiple memory items
    into a coherent narrative about the client's investment preferences.
  
  user_prompt_template: |
    Memory Items:
    {memory_items}
    
    Synthesize these memories into a coherent context.
  
  validation:
    output_schema: {...}
    confidence_threshold: 0.8
```

---

## Agent Implementations

### Memory Agent with LLM Enhancement

**Purpose**: Retrieve and synthesize client context for personalized recommendations

**Execution Flow**:

1. **Semantic Query Generation** (LLM)
   - Input: Client profile, investment horizon, constraints
   - Output: Semantic query, keywords, confidence score
   - Model: Claude 3 Haiku
   - Temperature: 0.3 (low for consistency)

2. **Memory Retrieval** (Deterministic)
   - Uses semantic query or keywords to retrieve memories
   - Falls back to keyword-based retrieval if semantic query fails
   - Returns list of relevant memory items

3. **Memory Synthesis** (LLM)
   - Input: Retrieved memory items, client profile
   - Output: Summary, key preferences, historical patterns, relevant decisions
   - Model: Claude 3 Haiku
   - Temperature: 0.5 (moderate for synthesis)

4. **Conflict Detection** (LLM)
   - Input: Memory items
   - Output: Conflicts detected, conflict details, confidence
   - Model: Claude 3 Haiku
   - Temperature: 0.3 (low for consistency)

**Fallback Strategy**:
- If semantic query generation fails: Use keyword-based retrieval
- If memory synthesis fails: Return raw memories without synthesis
- If conflict detection fails: Return empty conflicts list
- If all LLM features fail: Return deterministic memory retrieval

**Validation**:
- Semantic query confidence must exceed 0.7
- Synthesis confidence must exceed 0.8
- Conflict detection confidence must exceed 0.7

### Rebalancing Agent with LLM Enhancement

**Purpose**: Analyze portfolio drift and recommend rebalancing strategy

**Execution Flow**:

1. **Deterministic Drift Calculation** (Always)
   - Calculate current asset allocation
   - Compare against target allocation
   - Identify holdings outside tolerance bands
   - Returns: current_allocation, target_allocation, drift items

2. **Drift Explanation** (LLM, if enabled)
   - Input: Current allocation, target allocation, drift items
   - Output: Drift summary, drifted assets, explanation
   - Model: Claude 3 Sonnet
   - Temperature: 0.5 (moderate for explanation)
   - Validates LLM output against deterministic calculations

3. **Strategy Recommendation** (LLM, if enabled)
   - Input: Portfolio value, risk tolerance, drift explanation, constraints
   - Output: Recommended actions, rationale, priority
   - Model: Claude 3 Sonnet
   - Temperature: 0.5 (moderate for strategy)

**Validation**:
- Drift amounts in LLM output must match deterministic calculations (±1%)
- Drifted assets in LLM output must be subset of deterministic drift items
- Confidence scores must exceed thresholds

**Fallback Strategy**:
- If LLM analysis fails: Return deterministic drift without explanation
- If drift explanation fails: Skip to strategy recommendation
- If strategy recommendation fails: Return deterministic drift only

### Risk & Compliance Agent

**Purpose**: Evaluate portfolio against risk and compliance policies

**Execution Flow**:

1. **Policy Evaluation** (Deterministic)
   - Check single position concentration limits
   - Check sector concentration limits
   - Verify allowed asset classes
   - Evaluate tax implications
   - Returns: verdict (COMPLIANT, NON_COMPLIANT, UNRESOLVED), evidence

2. **LLM-Enhanced Explanation** (Optional)
   - Input: Policy verdict, evidence, portfolio details
   - Output: Explanation, recommendations, confidence
   - Model: Claude 3 Haiku
   - Temperature: 0.3 (low for consistency)

**Routing Decision**:
- COMPLIANT: Proceed to trade proposal generation
- NON_COMPLIANT: Block trade proposal, mark workflow as BLOCKED
- UNRESOLVED: Block trade proposal, mark workflow as BLOCKED

---

## State Management

### WorkflowGraphState Schema

The state object maintains all workflow information:

```python
class WorkflowGraphState(TypedDict):
    # Correlation and Versioning
    request_id: str
    session_id: str
    trace_id: str
    schema_version: str
    agent_version_set: str
    policy_version: str
    environment: str
    
    # Tracing
    trace_provider: Literal["bedrock_agentcore", "langsmith"]
    provider_trace_url: Optional[str]
    
    # Input
    request: PortfolioRebalanceRequest
    user_role_context: dict
    
    # Validation
    validation_error: Optional[dict]
    
    # Agent Outputs
    memory_output: Optional[dict]
    research_output: Optional[dict]
    sentiment_output: Optional[dict]
    rebalancing_output: Optional[dict]
    risk_policy_output: Optional[RiskPolicyResponse]
    trade_proposal_output: Optional[dict]
    guardrail_result: Optional[dict]
    approval_artifact: Optional[ApprovalArtifact]
    
    # Agent Stage Results
    agent_stages: list[AgentStageResult]
    
    # Quality Indicators
    confidence_map: dict[str, float]
    degraded_reasons: list[str]
    blockers: list[str]
    
    # Final Output
    recommendation_package: Optional[RecommendationPackage]
    workflow_state: WorkflowState
    audit_event_ids: list[str]
    
    # Error Handling
    error: Optional[dict]
    error_stage: Optional[str]
```

### State Transitions

State flows through the graph with updates at each node:

```
Initial State
    ↓
validate_request: Sets validation_error if validation fails
    ↓
initialize_context_and_trace: Sets trace_id, provider_trace_url
    ↓
log_request_audit_event: Adds audit_event_id
    ↓
hydrate_memory: Sets memory_output, adds agent_stage
    ↓
run_research: Sets research_output, adds agent_stage
    ↓
run_sentiment_analysis: Sets sentiment_output, adds agent_stage
    ↓
run_portfolio_rebalancing: Sets rebalancing_output, adds agent_stage
    ↓
run_risk_policy: Sets risk_policy_output, adds agent_stage
    ↓
[CONDITIONAL] route_after_risk_policy
    ├─ If COMPLIANT: generate_execution_proposal
    └─ If NON_COMPLIANT/UNRESOLVED: Skip to assemble_recommendation
    ↓
generate_execution_proposal: Sets trade_proposal_output, adds agent_stage
    ↓
assemble_recommendation: Sets recommendation_package, workflow_state
    ↓
apply_output_guardrails: Sets guardrail_result
    ↓
[CONDITIONAL] route_after_guardrails
    ├─ If NONE: create_approval_artifact
    └─ If BLOCKED: Skip to emit_workflow_audit_event
    ↓
create_approval_artifact: Sets approval_artifact
    ↓
persist_workflow_artifacts: Stores to DynamoDB
    ↓
emit_workflow_audit_event: Adds audit_event_id
    ↓
return_response: Final state ready for serialization
```

---

## Error Handling and Resilience

### Error Categories

**1. Validation Errors**
- Invalid request schema
- Business rule violations
- Missing required fields

**2. LLM Errors**
- Model invocation timeout
- Model not available
- Invalid response format
- Validation failure

**3. Infrastructure Errors**
- Database connection failure
- Service unavailable
- Network timeout

**4. Policy Errors**
- Policy evaluation failure
- Conflicting policies
- Unresolved policy verdict

### Fallback Strategies

**LLM Failure Fallback**:
```python
if feature_flags.llm_enabled and bedrock_adapter:
    try:
        result = await llm_operation()
    except Exception as e:
        if feature_flags.fallback_on_llm_failure:
            logger.info("Falling back to deterministic logic")
            result = await deterministic_operation()
        else:
            raise
```

**Graceful Degradation**:
- If LLM fails: Use deterministic logic
- If memory synthesis fails: Return raw memories
- If drift explanation fails: Return deterministic drift
- If strategy recommendation fails: Return deterministic drift only

**Workflow State Management**:
- NORMAL: All checks passed
- DEGRADED: Some quality issues but usable
- BLOCKED: Critical issues prevent recommendation

### Retry Logic

The Bedrock adapter implements exponential backoff:

```python
for attempt in range(max_retries + 1):
    try:
        response = await invoke_model(...)
        return response
    except RetryableError as e:
        if attempt >= max_retries:
            raise
        delay = base_delay * (exponential_base ** attempt)
        if jitter:
            delay *= 0.5 + random.random() * 0.5
        await asyncio.sleep(delay)
```

---

## Quality Assurance

### Multi-Layer Validation

**1. Request Validation**
- Schema validation at API layer
- Business rule validation in validate_request node
- Allocation targets sum to 100%
- Holdings have positive quantities

**2. LLM Output Validation**
- Response schema validation
- Confidence score thresholds
- Consistency checks against deterministic calculations
- Semantic validation of content

**3. Guardrail Validation**
- Bedrock guardrails for sensitive information
- Policy compliance checks
- Content safety assessment

**4. Consistency Validation**
- LLM drift amounts match deterministic calculations
- LLM drifted assets match deterministic drift items
- Confidence scores are within valid ranges

### Confidence Scoring

Each LLM operation produces a confidence score:

```python
confidence_map = {
    "semantic_query": 0.95,
    "memory_synthesis": 0.88,
    "conflict_detection": 0.92,
    "drift_explanation": 0.91,
    "strategy_recommendation": 0.87,
}
```

Confidence scores below thresholds trigger fallback to deterministic logic.

### Property-Based Testing

The system uses property-based testing with 100+ iterations per property:

```python
@given(
    portfolio=portfolio_strategy(),
    target_allocation=allocation_strategy(),
)
def test_drift_calculation_properties(portfolio, target_allocation):
    # Property 1: Drift sum equals total drift
    drift = calculate_drift(portfolio, target_allocation)
    assert sum(item.drift_amount for item in drift) == total_drift
    
    # Property 2: Drift is within bounds
    for item in drift:
        assert -1.0 <= item.drift_amount <= 1.0
    
    # Property 3: Deterministic calculation is consistent
    drift1 = calculate_drift(portfolio, target_allocation)
    drift2 = calculate_drift(portfolio, target_allocation)
    assert drift1 == drift2
```

---

## Deployment and Operations

### Docker Compose Orchestration

All services run in Docker containers:

```yaml
services:
  backend:
    image: assetmanagement-backend:latest
    ports:
      - "8000:8000"
    environment:
      - DYNAMODB_ENDPOINT_URL=http://dynamodb:8000
      - FEATURE_MEMORY_AGENT_LLM_ENABLED=false
      - FEATURE_RESEARCH_AGENT_LLM_ENABLED=false
      # ... other feature flags
  
  research-agent:
    image: assetmanagement-research-agent:latest
    ports:
      - "8101:8101"
  
  sentiment-mcp:
    image: assetmanagement-sentiment-mcp:latest
    ports:
      - "8201:8201"
  
  dynamodb:
    image: amazon/dynamodb-local:latest
    ports:
      - "55000:8000"
```

### Feature Flag Management

Feature flags are managed via environment variables:

```bash
# Enable specific LLM features
export FEATURE_MEMORY_AGENT_LLM_ENABLED=true
export FEATURE_REBALANCING_AGENT_LLM_ENABLED=true

# Disable fallback (strict mode)
export FALLBACK_ON_LLM_FAILURE=false

# Set model IDs
export LLM_MEMORY_AGENT_MODEL=anthropic.claude-3-haiku-20240307-v1:0
export LLM_REBALANCING_AGENT_MODEL=anthropic.claude-3-sonnet-20240229-v1:0
```

### Monitoring and Observability

**Tracing**:
- Bedrock AgentCore tracing for AWS-native deployments
- LangSmith tracing for detailed LLM call analysis
- Trace URLs stored in workflow state for debugging

**Audit Logging**:
- REQUEST_RECEIVED event at workflow start
- WORKFLOW_COMPLETED event at workflow end
- Agent stage results for each agent execution
- Approval artifact creation and persistence

**Metrics**:
- Token usage per agent
- Latency per node
- Confidence scores per LLM operation
- Error rates and retry counts

### Rollout Strategy

**Phase 1: Disabled by Default**
- All LLM features disabled
- System operates with deterministic logic only
- Validates infrastructure and baseline performance

**Phase 2: Gradual Enablement**
- Enable Memory Agent LLM (low risk, high value)
- Monitor confidence scores and fallback rates
- Gradually enable other agents

**Phase 3: Full Enablement**
- All LLM features enabled
- Continuous monitoring of quality metrics
- Ready for production deployment

---

## Conclusion

The LangGraph-based architecture provides a flexible, reliable foundation for LLM-powered portfolio management. By combining deterministic calculations with LLM enhancements, feature flags for safe rollout, and multi-layer validation, the system delivers high-quality recommendations while maintaining system reliability and compliance requirements.

The architecture is designed for:
- **Extensibility**: New agents can be added as new nodes
- **Reliability**: Graceful degradation when LLMs fail
- **Observability**: Comprehensive tracing and audit logging
- **Safety**: Feature flags and validation layers
- **Performance**: Parallel execution and efficient state management

This design enables the platform to evolve from a deterministic system to an intelligent, LLM-powered decision support platform while maintaining the trust and reliability required for financial advisory applications.
