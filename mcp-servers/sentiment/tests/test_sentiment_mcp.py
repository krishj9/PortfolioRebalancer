import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

# Load sentiment mcp app explicitly to avoid colliding with backend/app/main.py
_app_file = Path(__file__).resolve().parent.parent / "app" / "main.py"
_spec = importlib.util.spec_from_file_location("sentiment_mcp_app", _app_file)
assert _spec and _spec.loader
_sentiment_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sentiment_mod)
app = _sentiment_mod.app


def test_sentiment_mcp_tool_call() -> None:
    client = TestClient(app)
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "test",
            "method": "tools/call",
            "params": {
                "name": "analyze_symbol_news_sentiment",
                "arguments": {"symbols": ["EQUITY", "BONDS"]},
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result"]["sentiment"] == "CAUTIOUS"
