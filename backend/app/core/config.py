from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ============================================================================
# Feature Flags
# ============================================================================


class FeatureFlags(BaseSettings):
    """Feature flags for LLM integration."""

    # Agent LLM integration flags
    memory_agent_llm_enabled: bool = Field(default=False, description="Enable LLM for Memory Agent")
    research_agent_llm_enabled: bool = Field(default=False, description="Enable LLM for Research Agent")
    sentiment_agent_llm_enabled: bool = Field(default=False, description="Enable LLM for Sentiment Agent")
    rebalancing_agent_llm_enabled: bool = Field(default=False, description="Enable LLM for Rebalancing Agent")
    risk_agent_llm_enabled: bool = Field(default=False, description="Enable LLM for Risk Agent")
    trade_proposal_agent_llm_enabled: bool = Field(default=False, description="Enable LLM for Trade Proposal Agent")

    # Fallback behavior
    fallback_on_llm_failure: bool = Field(default=True, description="Fall back to deterministic logic on LLM failure")
    fallback_on_validation_failure: bool = Field(default=True, description="Fall back on validation failure")

    # Observability
    log_llm_prompts: bool = Field(default=False, description="Log LLM prompts (privacy-sensitive)")
    log_llm_responses: bool = Field(default=False, description="Log LLM responses (privacy-sensitive)")
    sample_rate: float = Field(default=0.1, ge=0.0, le=1.0, description="Sample rate for detailed logging")

    model_config = SettingsConfigDict(env_prefix="FEATURE_", extra="ignore")


# ============================================================================
# LLM Configuration
# ============================================================================


class LLMConfig(BaseSettings):
    """LLM configuration for Bedrock integration."""

    # Bedrock configuration
    bedrock_region: str = Field(default="us-east-1", description="AWS region for Bedrock")
    bedrock_endpoint_url: str | None = Field(default=None, description="Custom Bedrock endpoint (for testing)")

    # Model configuration per agent
    memory_agent_model: str = Field(
        default="us.anthropic.claude-3-5-haiku-20241022-v1:0",
        description="Model for Memory Agent (fast, cost-effective)",
    )
    research_agent_model: str = Field(
        default="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        description="Model for Research Agent (high-quality synthesis)",
    )
    sentiment_agent_model: str = Field(
        default="us.anthropic.claude-3-5-haiku-20241022-v1:0",
        description="Model for Sentiment Agent (fast sentiment classification)",
    )
    rebalancing_agent_model: str = Field(
        default="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        description="Model for Rebalancing Agent (balanced quality/cost)",
    )
    risk_agent_model: str = Field(
        default="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        description="Model for Risk Agent (critical policy decisions)",
    )
    trade_proposal_agent_model: str = Field(
        default="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        description="Model for Trade Proposal Agent (high-stakes recommendations)",
    )

    # Retry configuration
    max_retries: int = Field(default=4, ge=0, le=10, description="Maximum retry attempts")
    base_delay: float = Field(default=1.0, ge=0.1, le=10.0, description="Base delay for exponential backoff (seconds)")
    max_delay: float = Field(default=60.0, ge=1.0, le=300.0, description="Maximum delay for exponential backoff (seconds)")
    exponential_base: float = Field(default=2.0, ge=1.5, le=3.0, description="Exponential base for backoff")
    jitter: bool = Field(default=True, description="Enable jitter for retry delays")

    # Timeout configuration
    standard_timeout: int = Field(default=60, ge=10, le=600, description="Standard timeout (seconds)")
    extended_timeout: int = Field(default=300, ge=60, le=1800, description="Extended timeout (seconds)")
    streaming_timeout: int = Field(default=120, ge=30, le=600, description="Streaming timeout (seconds)")

    # Validation configuration
    default_grounding_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Default grounding threshold")
    default_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0, description="Default confidence threshold")
    max_regeneration_attempts: int = Field(default=2, ge=0, le=5, description="Max regeneration attempts on validation failure")

    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")


# ============================================================================
# Cost Management Configuration
# ============================================================================


class CostManagementConfig(BaseSettings):
    """Cost management configuration."""

    # Token budgets
    token_limit_per_invocation: int = Field(default=4096, ge=100, le=100000, description="Token limit per invocation")
    daily_token_budget: int = Field(default=1_000_000, ge=1000, description="Daily token budget")

    # Cost alerts
    cost_alert_threshold_usd: float = Field(default=100.0, ge=0.0, description="Cost alert threshold (USD)")

    # Caching
    enable_response_caching: bool = Field(default=True, description="Enable response caching")
    cache_ttl_seconds: int = Field(default=3600, ge=60, le=86400, description="Cache TTL (seconds)")

    model_config = SettingsConfigDict(env_prefix="COST_", extra="ignore")


# ============================================================================
# Tracing Configuration
# ============================================================================


class TracingConfig(BaseSettings):
    """Tracing configuration."""

    # Trace provider
    trace_provider: Literal["bedrock_agentcore", "langsmith"] = Field(
        default="bedrock_agentcore", description="Trace provider"
    )

    # LangSmith configuration
    langsmith_api_key: str | None = Field(default=None, description="LangSmith API key")
    langsmith_project: str | None = Field(default=None, description="LangSmith project name")

    model_config = SettingsConfigDict(env_prefix="TRACE_", extra="ignore")


# ============================================================================
# Main Settings
# ============================================================================


class Settings(BaseSettings):
    """Main application settings."""

    # Application metadata
    app_name: str = "Asset Management API"
    environment: str = "dev"
    schema_version: str = "1.0.0"
    policy_version: str = "1.0.0"
    app_version: str = "0.1.0"

    # Persistence configuration
    persistence_mode: str = Field(
        default="local",
        validation_alias=AliasChoices("persistence_mode", "dynamodb_mode"),
        description="Workflow store persistence mode: memory, local (DynamoDB local), aws (DynamoDB AWS), firestore",
    )
    dynamodb_endpoint_url: str | None = "http://localhost:55000"
    aws_region: str = "us-east-1"

    @property
    def dynamodb_mode(self) -> str:
        """Backwards-compatibility property for dynamodb_mode."""
        return self.persistence_mode

    # Table names
    approvals_table_name: str = "asset-management-dev-approvals"
    audit_events_table_name: str = "asset-management-dev-audit-events"
    portfolios_table_name: str = "asset-management-dev-portfolios"
    sessions_table_name: str = "asset-management-dev-sessions"
    memory_queue_table_name: str = "asset-management-dev-memory-queue"

    # Remote agents
    research_agent_url: str = "http://localhost:8101/a2a/research"
    research_agent_remote_enabled: bool = True
    sentiment_mcp_url: str = "http://localhost:8201/mcp"
    sentiment_mcp_enabled: bool = True
    remote_agent_timeout_seconds: float = 2.0

    # Market stream
    market_stream_max_events: int = 0
    seed_default_portfolios: bool = True

    # LLM Integration (nested configs)
    feature_flags: FeatureFlags = Field(default_factory=FeatureFlags)
    llm_config: LLMConfig = Field(default_factory=LLMConfig)
    cost_config: CostManagementConfig = Field(default_factory=CostManagementConfig)
    tracing_config: TracingConfig = Field(default_factory=TracingConfig)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


@lru_cache
def get_feature_flags() -> FeatureFlags:
    """Get cached feature flags instance."""
    return get_settings().feature_flags


@lru_cache
def get_llm_config() -> LLMConfig:
    """Get cached LLM config instance."""
    return get_settings().llm_config


@lru_cache
def get_cost_config() -> CostManagementConfig:
    """Get cached cost management config instance."""
    return get_settings().cost_config


@lru_cache
def get_tracing_config() -> TracingConfig:
    """Get cached tracing config instance."""
    return get_settings().tracing_config
