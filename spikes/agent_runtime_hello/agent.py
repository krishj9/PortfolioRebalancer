"""Minimal two-node LangGraph agent using Agent Runtime custom template.

Conforms to Gemini Enterprise Agent Platform Custom Agent specification:
- __init__: Takes only serializable configuration parameters (project, location).
- set_up: Initializes and compiles the LangGraph StateGraph.
- query: Synchronous entrypoint invoked by Agent Runtime / Reason Engines.
"""

from typing import Any, Dict, TypedDict
from langgraph.graph import StateGraph, START, END


class HelloState(TypedDict):
    input_text: str
    stage: str
    processed_text: str
    status: str


class HelloLangGraphAgent:
    """Two-node StateGraph agent for Agent Runtime spike validation."""

    def __init__(
        self,
        project: str = "mybrightday-dev",
        location: str = "us-central1",
    ) -> None:
        self.project = project
        self.location = location
        self._graph = None

    def set_up(self) -> None:
        """Compile the LangGraph workflow during runtime initialization."""
        class HelloState(TypedDict):
            input_text: str
            stage: str
            processed_text: str
            status: str

        self._state_cls = HelloState
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
        """Execute the graph for a query request.

        Args:
            input_text: The input text to process through the LangGraph workflow.

        Returns:
            Dict[str, Any]: The final state of the LangGraph workflow.
        """
        try:
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
        except Exception as e:
            import traceback
            return {
                "status": "ERROR",
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
