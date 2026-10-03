"""Tests for Sessions adapter (Task P2-01).

Includes:
- Local in-memory tests for lifecycle, event appending, and graph integration.
- Opt-in live smoke test against Vertex AI Agent Platform Sessions API (RUN_GCP_TESTS=1).
"""

import os
from pathlib import Path
import pytest

from app.adapters.sessions import (
    AgentPlatformSessionsAdapter,
    InMemorySessionsAdapter,
    get_sessions_adapter,
)
from app.agent_runtime.app import RebalanceGraphApp
from tests.test_rebalance import request_payload


def test_in_memory_sessions_lifecycle():
    """Verify local in-memory session creation, event appending, listing, and deletion."""
    adapter = InMemorySessionsAdapter()
    user_id = "test-user-123"

    # 1. Create session
    session_id = adapter.create_or_get_session(user_id=user_id)
    assert session_id is not None
    assert len(session_id) > 0

    # 2. Append user event
    user_text = "I prefer a conservative allocation."
    user_event = adapter.append_user_event(session_name=session_id, text=user_text, invocation_id="inv-1")
    assert user_event.author == "user"
    assert user_event.text == user_text

    # 3. Append model summary event
    summary_text = "Rebalanced portfolio with 2 trades."
    summary_event = adapter.append_summary_event(session_name=session_id, text=summary_text, invocation_id="inv-1")
    assert summary_event.author == "model"
    assert summary_event.text == summary_text

    # 4. List events
    events = adapter.list_events(session_name=session_id)
    assert len(events) == 2
    assert events[0].author == "user"
    assert events[1].author == "model"

    # 5. Reuse session
    reused_id = adapter.create_or_get_session(user_id=user_id, session_id=session_id)
    assert reused_id == session_id

    # 6. Delete session
    deleted = adapter.delete_session(session_name=session_id)
    assert deleted is True
    assert len(adapter.list_events(session_name=session_id)) == 0


def test_rebalance_graph_app_session_integration():
    """Verify RebalanceGraphApp automatically tracks sessions and appends events."""
    app_instance = RebalanceGraphApp(
        project="mybrightday-dev",
        location="us-central1",
        tool_mode="inprocess",
        sessions_mode="local",
    )
    app_instance.set_up()

    payload = request_payload()
    result1 = app_instance.query(payload)

    assert result1["workflow_state"] == "NORMAL"
    session_id_1 = result1.get("correlation", {}).get("session_id")
    assert session_id_1 is not None

    # Verify events recorded in the app's adapter
    events1 = app_instance._sessions_adapter.list_events(session_id_1)
    assert len(events1) >= 2
    assert events1[0].author == "user"
    assert events1[1].author == "model"

    # Second query reusing the same session_id
    result2 = app_instance.query(payload, session_id=session_id_1)
    session_id_2 = result2.get("correlation", {}).get("session_id")
    assert session_id_2 == session_id_1

    # Verify new events appended to the same session
    events2 = app_instance._sessions_adapter.list_events(session_id_1)
    assert len(events2) >= 4


@pytest.mark.gcp
@pytest.mark.skipif(
    not os.environ.get("RUN_GCP_TESTS"),
    reason="Set RUN_GCP_TESTS=1 to run smoke test against remote Agent Platform Sessions API",
)
def test_remote_agent_platform_sessions_smoke():
    """Verify live Sessions API on Vertex AI Agent Platform in mybrightday-dev (us-central1)."""
    project = os.environ.get("GCP_PROJECT", "mybrightday-dev")
    location = os.environ.get("GCP_LOCATION", "us-central1")

    resource_file = (
        Path(__file__).resolve().parent.parent.parent
        / "app"
        / "agent_runtime"
        / "deployed_runtime.txt"
    )
    if not resource_file.exists():
        pytest.skip(f"Deployed runtime resource file not found: {resource_file}")

    runtime_name = resource_file.read_text().strip()
    adapter = AgentPlatformSessionsAdapter(
        project=project,
        location=location,
        runtime_name=runtime_name,
    )

    user_id = "test-live-user"
    session_name = adapter.create_or_get_session(user_id=user_id)
    assert session_name.startswith(runtime_name + "/sessions/")

    try:
        # Append events
        adapter.append_user_event(
            session_name=session_name,
            text="Live test rebalance request.",
            invocation_id="inv-live-1",
        )
        adapter.append_summary_event(
            session_name=session_name,
            text="Live test summary response.",
            invocation_id="inv-live-1",
        )

        # List events
        events = adapter.list_events(session_name=session_name)
        assert len(events) >= 2

        # Verify reuse
        reused = adapter.create_or_get_session(user_id=user_id, session_id=session_name)
        assert reused == session_name
    finally:
        adapter.delete_session(session_name=session_name)
