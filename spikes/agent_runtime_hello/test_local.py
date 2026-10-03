"""Local validation test for HelloLangGraphAgent."""

import sys
from pathlib import Path

# Add spikes directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_runtime_hello.agent import HelloLangGraphAgent


def test_agent_local_execution():
    agent = HelloLangGraphAgent(project="mybrightday-dev", location="us-central1")
    agent.set_up()

    response = agent.query(input_text="Test Account ACC-12345")
    print("Local query response:", response)

    assert response["status"] == "SUCCESS"
    res = response.get("result", response)
    assert res["stage"] == "completed"
    assert "ACC-12345" in res["processed_text"]
    assert "validated by two-node LangGraph" in res["processed_text"]
    print("Local test PASSED!")


if __name__ == "__main__":
    test_agent_local_execution()
