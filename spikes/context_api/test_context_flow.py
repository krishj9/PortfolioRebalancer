"""Complete validation script for Task P0-04: Sessions + Memory Bank API flow.

Validates the full lifecycle on Vertex AI Agent Runtime in project mybrightday-dev (us-central1):
1. Session creation (client.sessions.create)
2. Appending user and model events (client.sessions.events.append)
3. Memory generation from session events (client.memory_banks.memories.generate)
4. Memory retrieval by scope and ID (client.memory_banks.memories.retrieve, .get)
5. Memory deletion (client.memory_banks.memories.delete)
6. Session deletion (client.sessions.delete)
"""

import sys
import datetime
from pathlib import Path
import agentplatform

PROJECT_ID = "mybrightday-dev"
LOCATION = "us-central1"


def run_spike():
    client = agentplatform.Client(project=PROJECT_ID, location=LOCATION)

    # Locate the active deployed runtime resource
    runtime_file = Path(__file__).parent.parent / "agent_runtime_hello" / "deployed_runtime.txt"
    if not runtime_file.exists():
        raise FileNotFoundError(f"Missing {runtime_file}. Run P0-03 deploy.py first.")
    runtime_name = runtime_file.read_text().strip()
    print(f"[P0-04] Target Agent Runtime: {runtime_name}")

    # 1. Create a session
    print("\n--- Step 1: Create Session ---")
    user_id = "demo-user"
    op_sess = client.sessions.create(name=runtime_name, user_id=user_id)
    session = op_sess.response
    session_name = session.name
    print(f"Session created: {session_name} for user: {user_id}")
    assert session_name.startswith(runtime_name + "/sessions/"), f"Unexpected session name format: {session_name}"

    try:
        # 2. Append 2 events
        print("\n--- Step 2: Append 2 Conversation Events ---")
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # User event
        user_text = "I prefer a conservative portfolio risk profile and want to maintain at least 15% in cash."
        client.sessions.events.append(
            name=session_name,
            author="user",
            invocation_id="inv-p0-04",
            timestamp=now,
            config={
                "content": {
                    "role": "user",
                    "parts": [{"text": user_text}]
                }
            }
        )
        print(f"Appended User event: {user_text}")

        # Model event
        model_text = "Understood. I have recorded your preference for a conservative risk profile with 15% cash minimum."
        client.sessions.events.append(
            name=session_name,
            author="model",
            invocation_id="inv-p0-04",
            timestamp=now + datetime.timedelta(seconds=1),
            config={
                "content": {
                    "role": "model",
                    "parts": [{"text": model_text}]
                }
            }
        )
        print(f"Appended Model event: {model_text}")

        # Verify events in session
        events = list(client.sessions.events.list(name=session_name))
        print(f"Verified {len(events)} events in session.")
        assert len(events) >= 2, f"Expected at least 2 events, got {len(events)}"

        # 3. Generate memories from session
        print("\n--- Step 3: Generate Memories via Memory Bank ---")
        scope = {"user_id": user_id}
        gen_op = client.memory_banks.memories.generate(
            name=runtime_name,
            vertex_session_source={"session": session_name},
            scope=scope,
            config={"wait_for_completion": True}
        )
        print(f"Generate memories operation completed: {gen_op.done}")
        generated = getattr(gen_op.response, "generated_memories", None) or getattr(gen_op.response, "generatedMemories", None)
        assert generated, f"Expected generated memories in response, got: {gen_op.response}"
        print(f"Generated {len(generated)} memory items.")
        
        memory_resource = generated[0].memory
        memory_name = memory_resource.name
        print(f"Generated memory resource: {memory_name}")

        # 4. Retrieve memory by ID and by scope
        print("\n--- Step 4: Retrieve Memory ---")
        mem = client.memory_banks.memories.get(name=memory_name)
        print(f"Retrieved memory fact: '{mem.fact}'")
        print(f"Retrieved memory scope: {mem.scope}")
        assert mem.scope == scope, f"Expected scope {scope}, got {mem.scope}"
        assert "15%" in mem.fact or "conservative" in mem.fact.lower(), f"Unexpected fact: {mem.fact}"

        # Retrieve via semantic / scoped query
        retrieved_list = list(client.memory_banks.memories.retrieve(name=runtime_name, scope=scope))
        print(f"Scoped retrieval returned {len(retrieved_list)} item(s).")
        assert len(retrieved_list) >= 1, "Scoped retrieval returned no memories"

        # 5. Delete memory
        print("\n--- Step 5: Delete Memory ---")
        del_mem_op = client.memory_banks.memories.delete(name=memory_name)
        print(f"Memory delete operation done: {del_mem_op.done}")

        # Verify deletion
        remaining = list(client.memory_banks.memories.retrieve(name=runtime_name, scope=scope))
        print(f"Remaining memories for scope {scope}: {len(remaining)}")
        assert len(remaining) == 0, f"Expected 0 memories after deletion, got {len(remaining)}"

    finally:
        # 6. Delete session cleanup
        print("\n--- Step 6: Delete Session (Cleanup) ---")
        del_sess_op = client.sessions.delete(name=session_name)
        print(f"Session deleted successfully: {del_sess_op.done}")

    print("\n[P0-04] ALL SESSIONS + MEMORY BANK VALIDATIONS PASSED!")


if __name__ == "__main__":
    run_spike()
