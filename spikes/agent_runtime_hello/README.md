# Spike P0-03: Custom-Template LangGraph on Agent Runtime

## Objective
Confirm that an existing `StateGraph` workflow can be deployed to Vertex AI Agent Runtime (Gemini Enterprise Agent Platform) using the custom agent template, and that `query()` remotely executes the compiled graph.

---

## 1. SDK Specifications & Package Requirements

- **SDK Package:** `google-cloud-aiplatform>=2.3.0` (which provides the `agentplatform` namespace and `agentplatform.Client`)
- **Python Version:** 3.14 (`python_version="3.14"`)
- **Remote Runtime Environment:** Python 3.14.7 (`GCC 14.2.0`, Debian-based container)
- **Local Environment:**
  - Python: `3.14.7`
  - `langgraph`: `1.2.12`
  - `google-cloud-aiplatform`: `2.3.0`
  - `cloudpickle`: `3.1.2`
  - `pydantic`: `2.13.5`
- **Target GCP Project:** `mybrightday-dev`
- **Target Region:** `us-central1`
- **Staging Bucket:** `gs://staging.mybrightday-dev.appspot.com`
- **Deployed Runtime Resource Name:** `projects/754915077075/locations/us-central1/reasoningEngines/6104962996679737344`

---

## 2. Custom Agent Architecture & Key Findings

### Finding 1: Python 3.14 PEP 649 Deferred Annotations
In Python 3.14, module-level `TypedDict` annotations rely on compiler-generated `__annotate_func__` / `__annotations_cache__`. When a class is unpickled across environments via `cloudpickle`, `HelloState.__annotations__` evaluated to `{}` in the remote container.
As a result, LangGraph initialized with 0 state channels (`channels={}`), causing node outputs to be silently dropped and `invoke()` to return `None`.
**Resolution:** Defining the `TypedDict` state schema inside `set_up()` ensures that Python 3.14 inside the execution container evaluates the annotations natively at startup, restoring all state channels (`input_text`, `stage`, `processed_text`, `status`).

### Finding 2: Explicit Type Annotations on `query()`
Agent Runtime dynamically builds a FastAPI interface around registered methods. Using `**kwargs: Any` generates an empty OpenAPI schema (`parameters.properties = {}`), leading to `InvalidRequestError: 'NoneType' object is not iterable` during unmarshaling.
**Resolution:** Always provide explicit parameter types and docstrings:
```python
def query(self, input_text: str) -> Dict[str, Any]:
    """Execute the graph for a query request.

    Args:
        input_text: The input text to process through the LangGraph workflow.
    """
```

### Finding 3: Packaging & Serialization
To ensure self-contained deployment:
- Register the local module with `cloudpickle.register_pickle_by_value(agent_module)`.
- Include the module directory in `config["extra_packages"]`.
- Include `"pydantic>=2.0.0"` in `config["requirements"]`.

### Verified Agent Template:
```python
from typing import Any, Dict, TypedDict
from langgraph.graph import StateGraph, START, END

class HelloLangGraphAgent:
    def __init__(self, project: str = "mybrightday-dev", location: str = "us-central1") -> None:
        self.project = project
        self.location = location
        self._graph = None

    def set_up(self) -> None:
        """Compile the LangGraph workflow inside the remote container on startup."""
        class HelloState(TypedDict):
            input_text: str
            stage: str
            processed_text: str
            status: str

        builder = StateGraph(HelloState)

        def node_enrich(state: HelloState) -> Dict[str, Any]:
            raw = state.get("input_text", "")
            return {
                "stage": "enriched",
                "processed_text": f"AgentRuntime received: [{raw}]",
            }

        def node_format(state: HelloState) -> Dict[str, Any]:
            processed = state.get("processed_text", "")
            return {
                "stage": "completed",
                "status": "SUCCESS",
                "processed_text": f"{processed} -> validated by two-node LangGraph",
            }

        builder.add_node("enrich", node_enrich)
        builder.add_node("format", node_format)
        builder.add_edge(START, "enrich")
        builder.add_edge("enrich", "format")
        builder.add_edge("format", END)

        self._graph = builder.compile()

    def query(self, input_text: str) -> Dict[str, Any]:
        """Query entrypoint invoked synchronously by Agent Runtime."""
        if self._graph is None:
            self.set_up()

        initial_state = {
            "input_text": input_text,
            "stage": "started",
            "processed_text": "",
            "status": "PENDING",
        }
        result = self._graph.invoke(initial_state)
        return dict(result)
```

---

## 3. Deployment Call Pattern

```python
import agentplatform
import cloudpickle

client = agentplatform.Client(project="mybrightday-dev", location="us-central1")

# Register module by value
cloudpickle.register_pickle_by_value(agent_module)
local_agent = HelloLangGraphAgent(project="mybrightday-dev", location="us-central1")

config = {
    "display_name": "spike-langgraph-hello",
    "description": "POC Spike P0-03: Two-node StateGraph custom agent on Agent Runtime",
    "staging_bucket": "gs://staging.mybrightday-dev.appspot.com",
    "python_version": "3.14",
    "requirements": [
        "langgraph>=1.2.0",
        "pydantic>=2.0.0",
        "cloudpickle>=3.0.0",
    ],
    "extra_packages": [
        package_directory_path,
    ],
}

# Initial creation:
remote_agent = client.runtimes.create(agent=local_agent, config=config)

# In-place update:
remote_agent = client.runtimes.update(name=existing_name, agent=local_agent, config=config)
```

---

## 4. Remote Query Invocation & Verification

```python
client = agentplatform.Client(project="mybrightday-dev", location="us-central1")
runtime = client.runtimes.get(name="projects/754915077075/locations/us-central1/reasoningEngines/6104962996679737344")

response = runtime.query(input_text="Test Account ACC-99887")
```

### Remote Verification Output:
```json
{
  "input_text": "Test Account ACC-99887",
  "stage": "completed",
  "processed_text": "AgentRuntime received: [Test Account ACC-99887] -> validated by two-node LangGraph",
  "status": "SUCCESS"
}
```

**Acceptance Result:**
- Local execution verified (`PASSED`).
- Remote deployment on Vertex AI Agent Runtime in `mybrightday-dev` (`us-central1`) verified (`PASSED`).
- Graph state transitions (`START` -> `enrich` -> `format` -> `END`) completed successfully in the cloud container.
