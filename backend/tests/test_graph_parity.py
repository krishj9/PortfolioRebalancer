"""Characterization tests comparing LangGraph StateGraph execution with Orchestrator.

Validates Task P0-02 from docs/migration/backlog.md:
- Prove the existing sequential Orchestrator works as the baseline
- Document and prove defects preventing LangGraph from running to completion:
    * D0a: TypeError in validate_request (Decimal vs float comparison)
    * D0b: InvalidUpdateError in parallel fan-out (hydrate_memory & run_research returning full state)
    * D1: ImportError in create_approval_artifact (ApprovalArtifact missing from app.contracts.domain)
    * D2: persist_workflow_artifacts is a no-op that does not call WorkflowStore
"""

import pytest

from app.contracts.common import WorkflowState
from app.contracts.workflow import PortfolioRebalanceRequest
from app.persistence.memory_store import InMemoryWorkflowStore
from app.services.langgraph_graph import build_workflow_graph
from app.services.langgraph_state import create_initial_state
from app.services.orchestrator import Orchestrator
from tests.test_rebalance import request_payload


@pytest.fixture
def rebalance_request() -> PortfolioRebalanceRequest:
    """Construct standard valid rebalance request."""
    return PortfolioRebalanceRequest.model_validate(request_payload())


@pytest.mark.asyncio
async def test_orchestrator_baseline_succeeds(rebalance_request: PortfolioRebalanceRequest) -> None:
    """Verify that sequential Orchestrator runs to completion and produces valid response."""
    store = InMemoryWorkflowStore()
    orchestrator = Orchestrator(store)

    response = await orchestrator.run(rebalance_request)

    assert response.workflow_state == WorkflowState.NORMAL
    assert response.recommendation_package is not None
    assert response.recommendation_package.approval_eligibility is True
    assert response.approval_artifact is not None
    assert response.approval_artifact.approval_status == "PENDING"
    assert response.approval_artifact.recommendation_hash is not None

    # Check store persistence (Defect D2 contrast)
    stored_portfolio = store.get_portfolio(rebalance_request.account_profile.account_id)
    assert stored_portfolio is not None
    stored_approval = store.get_approval(response.approval_artifact.approval_id)
    assert stored_approval is not None


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known LangGraph defects: D0a (Decimal/float operand in validate_request), "
        "D0b (parallel node full-state update collision), and D1 (ApprovalArtifact ImportError)"
    ),
)
async def test_langgraph_unmodified_graph_fails(rebalance_request: PortfolioRebalanceRequest) -> None:
    """
    Attempt to run the unmodified LangGraph StateGraph.

    This test is expected to fail (xfail) due to defects in validate_request, parallel state updates,
    and create_approval_artifact. Once tasks P1-01 and P1-02 are completed, this test can be updated
    to assert parity with Orchestrator.
    """
    initial_state = create_initial_state(rebalance_request)
    graph = build_workflow_graph()

    # This call raises an exception in unmodified backend
    final_state = await graph.ainvoke(initial_state)

    assert final_state is not None
    assert final_state.get("approval_artifact") is not None
