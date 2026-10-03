"""Deploy HelloLangGraphAgent to Vertex AI Agent Runtime using agentplatform SDK.

Task P0-03: Spike custom-template LangGraph on Agent Runtime.
"""

import sys
from pathlib import Path
import agentplatform

# Add spikes directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_runtime_hello.agent import HelloLangGraphAgent

PROJECT_ID = "mybrightday-dev"
LOCATION = "us-central1"
STAGING_BUCKET = "gs://staging.mybrightday-dev.appspot.com"
RUNTIME_DISPLAY_NAME = "spike-langgraph-hello"


def deploy():
    print(f"Initializing agentplatform.Client(project={PROJECT_ID}, location={LOCATION})...")
    client = agentplatform.Client(project=PROJECT_ID, location=LOCATION)

    print("Instantiating local HelloLangGraphAgent...")
    import agent_runtime_hello.agent
    import cloudpickle
    cloudpickle.register_pickle_by_value(agent_runtime_hello.agent)

    local_agent = HelloLangGraphAgent(project=PROJECT_ID, location=LOCATION)

    config = {
        "display_name": RUNTIME_DISPLAY_NAME,
        "description": "POC Spike P0-03: Two-node StateGraph custom agent on Agent Runtime",
        "staging_bucket": STAGING_BUCKET,
        "python_version": "3.14",
        "requirements": [
            "langgraph>=1.2.0",
            "pydantic>=2.0.0",
            "cloudpickle>=3.0.0",
        ],
        "extra_packages": [
            str(Path(__file__).parent),
        ],
    }

    resource_file = Path(__file__).parent / "deployed_runtime.txt"
    if resource_file.exists() and "--create" not in sys.argv:
        existing_name = resource_file.read_text().strip()
        print(f"Updating existing runtime via client.runtimes.update(): {existing_name}...")
        remote_agent = client.runtimes.update(name=existing_name, agent=local_agent, config=config)
        print("\nUpdate completed successfully!")
    else:
        print("Deploying agent to Agent Runtime via client.runtimes.create()...")
        remote_agent = client.runtimes.create(agent=local_agent, config=config)
        print("\nDeployment completed successfully!")
        resource_file.write_text(remote_agent.api_resource.name)
        print(f"Saved runtime resource name to {resource_file}")

    print(f"Runtime Resource Name: {remote_agent.api_resource.name}")
    print(f"Display Name: {remote_agent.api_resource.display_name}")

    return remote_agent


if __name__ == "__main__":
    deploy()
