import logging
from typing import Optional, Union

from app.adapters.bedrock import BedrockModelAdapter
from app.adapters.gemini import GeminiModelAdapter
from app.core.config import get_llm_config

logger = logging.getLogger(__name__)

ModelAdapter = Union[GeminiModelAdapter, BedrockModelAdapter]


def get_model_adapter(
    provider: Optional[str] = None,
    **kwargs,
) -> ModelAdapter:
    """
    Get configured model adapter based on LLM_PROVIDER setting or parameter.

    Args:
        provider: "gemini" or "bedrock". Defaults to llm_config.provider.
        **kwargs: Additional parameters passed to the adapter constructor.

    Returns:
        GeminiModelAdapter or BedrockModelAdapter instance
    """
    selected_provider = provider or get_llm_config().provider
    normalized_provider = selected_provider.lower().strip()

    if normalized_provider == "gemini":
        return GeminiModelAdapter(**kwargs)
    elif normalized_provider == "bedrock":
        return BedrockModelAdapter(**kwargs)
    else:
        logger.warning(
            f"Unknown LLM provider '{selected_provider}', defaulting to GeminiModelAdapter"
        )
        return GeminiModelAdapter(**kwargs)
