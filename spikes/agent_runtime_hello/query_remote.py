"""Query the remotely deployed HelloLangGraphAgent on Vertex AI Agent Runtime.

Task P0-03: Spike custom-template LangGraph on Agent Runtime.
"""

import sys
from pathlib import Path
import agentplatform

PROJECT_ID = "mybrightday-dev"
LOCATION = "us-central1"


def query_remote(resource_name: str | None = None, message: str = "Test Account ACC-99887"):
    client = agentplatform.Client(project=PROJECT_ID, location=LOCATION)

    if not resource_name:
        resource_file = Path(__file__).parent / "deployed_runtime.txt"
        if not resource_file.exists():
            print(f"Error: {resource_file} not found. Please provide resource_name or run deploy.py first.")
            sys.exit(1)
        resource_name = resource_file.read_text().strip()

    print(f"Connecting to deployed runtime: {resource_name}")
    runtime = client.runtimes.get(name=resource_name)
    print(f"Retrieved runtime: {runtime.api_resource.display_name}")

    print(f"\nSending query: input_text='{message}'...")
    response = runtime.query(input_text=message)
    print("\nRemote Query Response:")
    print(response)

    if response.get("status") == "ERROR":
        print("\n=== Remote Exception Traceback ===")
        print(response.get("traceback"))

    assert response.get("status") == "SUCCESS", f"Expected SUCCESS, got {response}"
    assert "ACC-99887" in response.get("processed_text", ""), f"Expected input in response, got {response}"
    assert response.get("stage") == "completed", f"Expected stage=completed, got {response}"
    print("\nRemote query assertion PASSED!")
    return response


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else None
    query_remote(name)
