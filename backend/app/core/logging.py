"""Structured JSON logging with GCP Cloud Logging trace correlation and redaction (Task P1-12)."""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

# Sensitive field keys to redact from logs
SENSITIVE_KEYS = {
    "holdings",
    "prompt",
    "prompts",
    "system_prompt",
    "user_prompt",
    "raw_prompt",
    "api_token",
    "x-api-token",
}


def redact_sensitive_data(data: Any) -> Any:
    """Recursively redact sensitive data (prompts, holdings, tokens) from log payloads."""
    if isinstance(data, dict):
        redacted = {}
        for key, value in data.items():
            key_lower = str(key).lower()
            if key_lower in SENSITIVE_KEYS:
                if key_lower == "holdings" and isinstance(value, list):
                    redacted[key] = f"<{len(value)} holdings redacted>"
                else:
                    redacted[key] = f"<{key} redacted>"
            else:
                redacted[key] = redact_sensitive_data(value)
        return redacted
    elif isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]
    return data


class CloudLoggingJsonFormatter(logging.Formatter):
    """Formats log records as JSON for Google Cloud Logging with trace correlation."""

    def __init__(self, project_id: str | None = None) -> None:
        super().__init__()
        self.project_id = project_id or os.environ.get("GCP_PROJECT", "mybrightday-dev")

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }

        # Correlate Cloud Trace if trace_id is present
        trace_id = getattr(record, "trace_id", None)
        if trace_id and self.project_id:
            # W3C traceparent (4096-bit hex) or UUID format
            clean_trace = str(trace_id).replace("-", "")[:32]
            log_entry["logging.googleapis.com/trace"] = f"projects/{self.project_id}/traces/{clean_trace}"

        span_id = getattr(record, "span_id", None)
        if span_id:
            log_entry["logging.googleapis.com/spanId"] = str(span_id)

        # Context correlation fields
        for field in ("run_id", "session_id", "proposal_id", "account_id", "approval_id"):
            val = getattr(record, field, None)
            if val is not None:
                log_entry[field] = str(val)

        # If record has extra details or payload
        payload = getattr(record, "payload", None)
        if payload is not None:
            log_entry["jsonPayload"] = redact_sensitive_data(payload)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


def setup_structured_logging(project_id: str | None = None) -> None:
    """Configure root logger to use CloudLoggingJsonFormatter for structured stdout logging."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudLoggingJsonFormatter(project_id=project_id))

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)
