import os

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.telemetry import create_traceparent, telemetry_span
from app.api.routes.approvals import router as approvals_router
from app.api.routes.explain import router as explain_router
from app.api.routes.health import router as health_router
from app.api.routes.intelligence import router as intelligence_router
from app.api.routes.market import router as market_router
from app.api.routes.memory import router as memory_router
from app.api.routes.portfolios import router as portfolios_router
from app.api.routes.preferences import router as preferences_router
from app.api.routes.rebalance import router as rebalance_router
from app.core.config import get_settings
from app.tools.router import router as tools_router

# Routes that don't require a token (health check + OPTIONS preflight)
_OPEN_PATHS = {"/health", "/"}


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Personal portfolio decision-support API.",
    )
    cors_origins = (
        settings.cors_allowed_origins
        if settings.auth_mode.lower() == "iap"
        else ["*"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True if cors_origins != ["*"] else False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # ── Distributed Tracing Middleware (Task P4-01) ───────────────────────────
    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        incoming_tp = (
            request.headers.get("traceparent")
            or request.headers.get("x-cloud-trace-context")
        )
        incoming_run_id = request.headers.get("x-run-id") or request.headers.get("x-session-id")
        span_attrs = {
            "http.method": request.method,
            "http.url": str(request.url),
            "http.path": request.url.path,
        }
        with telemetry_span(
            f"http.{request.method.lower()}",
            attributes=span_attrs,
            traceparent=incoming_tp,
            run_id=incoming_run_id,
        ) as span:
            request.state.trace_id = span.trace_id
            request.state.span_id = span.span_id
            response = await call_next(request)
            response.headers["traceparent"] = create_traceparent(span.trace_id, span.span_id)
            response.headers["X-Trace-ID"] = span.trace_id
            if span.attributes.get("run_id"):
                response.headers["X-Run-ID"] = str(span.attributes["run_id"])
            return response

    # ── Token gate middleware ──────────────────────────────────────────────────
    # Reads API_TOKEN from environment. If not set, gate is disabled (local dev).
    # Frontend sends the token in the x-api-token header.
    @app.middleware("http")
    async def token_gate(request: Request, call_next):
        required_token = os.environ.get("API_TOKEN", "")

        # Gate disabled in local dev (no token configured)
        if not required_token:
            return await call_next(request)

        # Always allow health check, OPTIONS preflight, and tool service endpoints
        if (
            request.method == "OPTIONS"
            or request.url.path in _OPEN_PATHS
            or request.url.path.startswith("/tools")
        ):
            return await call_next(request)

        token = request.headers.get("x-api-token", "")
        if token != required_token:
            return Response(
                content='{"detail":"Invalid or missing API token"}',
                status_code=401,
                media_type="application/json",
            )

        return await call_next(request)

    app_role = os.environ.get("APP_ROLE", "api").lower().strip()
    if app_role == "tools":
        app.include_router(health_router)
        app.include_router(tools_router)
        return app

    app.include_router(approvals_router, prefix="/api")
    app.include_router(explain_router, prefix="/api")
    app.include_router(intelligence_router, prefix="/api")
    app.include_router(health_router)
    app.include_router(market_router, prefix="/api")
    app.include_router(memory_router, prefix="/api")
    app.include_router(portfolios_router, prefix="/api")
    app.include_router(preferences_router, prefix="/api")
    app.include_router(rebalance_router, prefix="/api")
    app.include_router(tools_router)

    # Backward compatibility for direct endpoints without /api prefix
    app.include_router(approvals_router)
    app.include_router(explain_router)
    app.include_router(intelligence_router)
    app.include_router(market_router)
    app.include_router(memory_router)
    app.include_router(portfolios_router)
    app.include_router(preferences_router)
    app.include_router(rebalance_router)
    return app


app = create_app()
