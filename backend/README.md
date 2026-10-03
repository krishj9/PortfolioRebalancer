# Asset Management Backend

FastAPI backend for the personal portfolio multi-agent platform.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python and dependency management.

## Local setup

```shell
cd backend
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Alternative: skip activation and use `uv run` (see below).

## Run

With an activated venv:

```shell
PERSISTENCE_MODE=local \
DYNAMODB_ENDPOINT_URL=http://localhost:55000 \
uvicorn app.main:app --reload
```

Or without activation:

```shell
PERSISTENCE_MODE=local \
DYNAMODB_ENDPOINT_URL=http://localhost:55000 \
uv run uvicorn app.main:app --reload
```

One command helper from repo root:

```shell
./backend/scripts/run_backend.sh
```

Optional AWS mode:

```shell
./backend/scripts/run_backend.sh aws
```

The same repository code can target AWS-hosted DynamoDB by setting `PERSISTENCE_MODE=aws` (or legacy `DYNAMODB_MODE=aws`)
and leaving `DYNAMODB_ENDPOINT_URL` unset. For memory-only mode (e.g. testing), use `PERSISTENCE_MODE=memory`.

The backend stores client portfolios in a dedicated portfolios table. Local mode seeds demo
portfolios automatically when the table is empty. Approval actions persist the approved
portfolio allocation back to the same account, and `/market/stream?account_id=<id>` uses the
stored portfolio as the baseline for drift simulation.

## Optional Remote Agent Services

Run the remote A2A Research Agent:

```shell
cd ../remote-agents/research-agent
uv run uvicorn app.main:app --reload --port 8101
```

Run the Sentiment MCP server:

```shell
cd ../mcp-servers/sentiment
uv run uvicorn app.main:app --reload --port 8201
```

The backend falls back to local behavior if either service is unavailable.

## AWS Lambda

The backend exposes a Lambda entry point through `app.lambda_handler.handler`.
Set `DYNAMODB_MODE=aws`, leave `DYNAMODB_ENDPOINT_URL` empty, and provide the hosted
DynamoDB table names when running in AWS. `MARKET_STREAM_MAX_EVENTS` can be set to a
positive integer to keep market streaming bounded for Lambda/API Gateway.

## Test

```shell
uv run pytest
```

## Generate schemas

```shell
uv run python scripts/generate_schemas.py
```
