import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

# Load research agent app explicitly to avoid colliding with backend/app/main.py
_app_file = Path(__file__).resolve().parent.parent / "app" / "main.py"
_spec = importlib.util.spec_from_file_location("research_agent_app", _app_file)
assert _spec and _spec.loader
_research_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_research_mod)
app = _research_mod.app


def test_research_agent_a2a_response() -> None:
    client = TestClient(app)
    response = client.post(
        "/a2a/research",
        json={"task": "portfolio_research", "request_id": "req_test", "symbols": ["EQUITY"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["protocol"] == "A2A"
    assert body["payload"]["source"] in {"remote_a2a_research_agent", "local_fallback", "bedrock_llm"}
