"""Unit tests for structured logging and redaction (Task P1-12)."""

import json
import logging
from app.core.logging import CloudLoggingJsonFormatter, redact_sensitive_data


def test_redact_sensitive_data_holdings_and_prompts():
    """Verify holdings lists and prompt fields are redacted."""
    sample_payload = {
        "account_id": "acct_demo",
        "prompt": "Tell me what trades to make with my money",
        "system_prompt": "You are a portfolio manager",
        "holdings": [
            {"symbol": "EQUITY", "quantity": 100},
            {"symbol": "BONDS", "quantity": 50},
        ],
        "safe_field": "visible_value",
        "nested": {
            "user_prompt": "What is my drift?",
            "safe_nested": 123,
        },
    }

    redacted = redact_sensitive_data(sample_payload)

    assert redacted["account_id"] == "acct_demo"
    assert redacted["safe_field"] == "visible_value"
    assert redacted["prompt"] == "<prompt redacted>"
    assert redacted["system_prompt"] == "<system_prompt redacted>"
    assert redacted["holdings"] == "<2 holdings redacted>"
    assert redacted["nested"]["user_prompt"] == "<user_prompt redacted>"
    assert redacted["nested"]["safe_nested"] == 123


def test_cloud_logging_json_formatter():
    """Verify CloudLoggingJsonFormatter outputs valid JSON with GCP trace correlation."""
    formatter = CloudLoggingJsonFormatter(project_id="mybrightday-dev")
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Workflow execution completed",
        args=(),
        exc_info=None,
    )
    record.trace_id = "trace_abc123"
    record.run_id = "run_456"
    record.proposal_id = "prp_789"
    record.payload = {"safe": "value", "prompt": "secret prompt"}

    output = formatter.format(record)
    parsed = json.loads(output)

    assert parsed["severity"] == "INFO"
    assert parsed["message"] == "Workflow execution completed"
    assert parsed["logging.googleapis.com/trace"] == "projects/mybrightday-dev/traces/trace_abc123"
    assert parsed["run_id"] == "run_456"
    assert parsed["proposal_id"] == "prp_789"
    assert parsed["jsonPayload"]["safe"] == "value"
    assert parsed["jsonPayload"]["prompt"] == "<prompt redacted>"
