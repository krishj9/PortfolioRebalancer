---
description: AWS deployment and infrastructure best practices
globs: **/*.{yaml,yml,json,tf,ts,py,sh}
alwaysApply: false
---

# AWS Deployment Best Practices

- Treat infrastructure as code. Prefer repeatable templates/scripts over manual console setup.
- Keep environments explicit: `dev`, `test`, `staging`, and `main`. Do not share stateful data stores across environments.
- Use least-privilege IAM for Lambda, DynamoDB, Bedrock, AgentCore, CloudWatch, and deployment automation.
- Store secrets in AWS-managed secret/config services or local ignored env files. Never commit credentials, tokens, account IDs that should be private, or `.env` files.
- Configure Lambda timeouts, memory, retries, idempotency keys, and structured logging deliberately.
- Use DynamoDB on-demand billing for the personal project unless a measured workload justifies provisioned capacity.
- Enable TTL for short-lived session, audit sample, approval artifact, and memory queue records where appropriate.
- Emit logs, metrics, and traces with `request_id`, `session_id`, `trace_id`, `schema_version`, `policy_version`, and `environment`.
- Keep Bedrock model IDs, guardrail IDs, trace provider, and feature flags in environment-specific config.
- Add cost controls: conservative log retention, alarms for errors/throttling, and a documented teardown path for non-main environments.

```yaml
Environment:
  Variables:
    ENVIRONMENT: dev
    TRACE_PROVIDER: langsmith
    SCHEMA_VERSION: 1.0.0
    POLICY_VERSION: 1.0.0
```
