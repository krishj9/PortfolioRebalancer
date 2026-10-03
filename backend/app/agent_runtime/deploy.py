"""Deploy RebalanceGraphApp to Vertex AI Agent Runtime using agentplatform SDK.

Task P1-08: Host LangGraph portfolio rebalancer on Vertex AI Agent Runtime.
"""

import argparse
import os
import sys
from pathlib import Path

import agentplatform
import cloudpickle

# Ensure backend directory is in path
backend_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(backend_dir))

import app
from app.agent_runtime.app import RebalanceGraphApp

PROJECT_ID = os.environ.get("GCP_PROJECT", "mybrightday-dev")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get("GCP_STAGING_BUCKET", "gs://staging.mybrightday-dev.appspot.com")
RUNTIME_DISPLAY_NAME = os.environ.get("RUNTIME_DISPLAY_NAME", "portfolio-rebalancer-runtime")
TOOLS_URL = os.environ.get("TOOLS_URL", "http://localhost:8000")
AGENT_GATEWAY = os.environ.get("AGENT_GATEWAY", "")
PSC_NETWORK_ATTACHMENT = os.environ.get("PSC_NETWORK_ATTACHMENT", "")


def deploy(
    project_id: str = PROJECT_ID,
    location: str = LOCATION,
    staging_bucket: str = STAGING_BUCKET,
    display_name: str = RUNTIME_DISPLAY_NAME,
    tools_url: str = TOOLS_URL,
    agent_gateway: str | None = None,
    psc_network_attachment: str | None = None,
    force_create: bool = False,
    runtime_name: str | None = None,
):
    gateway_resource = agent_gateway if agent_gateway is not None else AGENT_GATEWAY
    psc_attachment = (
        psc_network_attachment
        if psc_network_attachment is not None
        else PSC_NETWORK_ATTACHMENT
    )
    print(f"Initializing agentplatform.Client(project={project_id}, location={location})...")
    client = agentplatform.Client(project=project_id, location=location)

    print("Registering app.agent_runtime.app for pickling by value...")
    import app.agent_runtime.app
    cloudpickle.register_pickle_by_value(app.agent_runtime.app)


    print(f"Instantiating local RebalanceGraphApp(tools_url={tools_url})...")
    local_agent = RebalanceGraphApp(
        project=project_id,
        location=location,
        tool_mode="remote",
        tools_url=tools_url,
        llm_provider="gemini",
        gemini_model_id="gemini-2.5-flash",
    )

    # Ensure working directory is backend_dir so relative path "app" packages cleanly into /code/app
    os.chdir(str(backend_dir))

    requirements = [
        "langgraph>=1.2.0",
        "pydantic>=2.0.0",
        "pydantic-settings>=2.0.0",
        "google-genai>=2.28.0",
        "google-cloud-firestore>=2.19.0",
        "httpx>=0.28.0",
        "cloudpickle>=3.0.0",
        "google-auth>=2.0.0",
        "boto3>=1.34.0",
    ]

    extra_packages = [
        "app",
    ]

    env_vars = {
        "PROJECT_ID": project_id,
        "TOOL_MODE": "remote",
        "TOOLS_URL": tools_url,
        "LLM_PROVIDER": "gemini",
        "GEMINI_MODEL_ID": "gemini-2.5-flash",
        "RESEARCH_AGENT_REMOTE_ENABLED": "false",
        "SENTIMENT_AGENT_REMOTE_ENABLED": "false",
    }

    config = {
        "display_name": display_name,
        "description": "Portfolio Rebalancer LangGraph workflow on Vertex AI Agent Runtime",
        "staging_bucket": staging_bucket,
        "python_version": "3.14",
        "requirements": requirements,
        "extra_packages": extra_packages,
        "env_vars": env_vars,
    }

    if gateway_resource:
        print(f"Binding Agent Gateway (Agent-to-Anywhere egress): {gateway_resource}")
        config["agent_gateway_config"] = {
            "agent_to_anywhere_config": {
                "agent_gateway": gateway_resource,
            }
        }
        config["identity_type"] = "AGENT_IDENTITY"

    if psc_attachment:
        print(f"Configuring PSC Interface attachment: {psc_attachment}")
        config["psc_interface_config"] = {
            "network_attachment": psc_attachment,
        }

    resource_file = Path(__file__).parent / "deployed_runtime.txt"
    target_name = runtime_name
    if not target_name and resource_file.exists() and not force_create:
        target_name = resource_file.read_text().strip()

    if target_name and not force_create:
        print(f"Updating existing Agent Runtime: {target_name}...")
        remote_agent = client.runtimes.update(name=target_name, agent=local_agent, config=config)
        print("\nUpdate completed successfully!")
    else:
        print("Deploying new Agent Runtime via client.runtimes.create()...")
        remote_agent = client.runtimes.create(agent=local_agent, config=config)
        print("\nDeployment completed successfully!")
        target_name = remote_agent.api_resource.name
        resource_file.write_text(target_name)
        print(f"Saved runtime resource name to {resource_file}")

    print(f"Runtime Resource Name: {remote_agent.api_resource.name}")
    print(f"Display Name: {remote_agent.api_resource.display_name}")
    return remote_agent


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy RebalanceGraphApp to Vertex AI Agent Runtime")
    parser.add_argument("--create", action="store_true", help="Force create a new Agent Runtime instead of updating")
    parser.add_argument("--name", type=str, default=None, help="Existing runtime resource name to update")
    parser.add_argument("--tools-url", type=str, default=TOOLS_URL, help="Tools service base URL")
    parser.add_argument("--agent-gateway", type=str, default=None, help="Agent Gateway resource path")
    parser.add_argument("--psc-network-attachment", type=str, default=None, help="PSC Network Attachment resource path")
    args = parser.parse_args()

    deploy(
        force_create=args.create,
        runtime_name=args.name,
        tools_url=args.tools_url,
        agent_gateway=args.agent_gateway,
        psc_network_attachment=args.psc_network_attachment,
    )
