"""SaladCloud AI Gateway provider profile."""

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class SaladCloudProfile(ProviderProfile):
    """Salad AI Gateway - OpenAI-compatible endpoint."""

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        model: str | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}

        model_name = (model or "").strip().lower()
        is_qwen_model = model_name.startswith("qwen") or "/qwen" in model_name
        if not is_qwen_model or reasoning_config is None:
            return extra_body, {}

        enabled = reasoning_config.get("enabled")
        effort = (reasoning_config.get("effort") or "").strip().lower()

        if enabled is False or effort == "none":
            extra_body["chat_template_kwargs"] = {"enable_thinking": False}
        elif enabled is True or effort:
            extra_body["chat_template_kwargs"] = {"enable_thinking": True}

        return extra_body, {}


saladcloud = SaladCloudProfile(
    name="saladcloud",
    aliases=(
        "salad",
        "salad-cloud",
        "salad-ai-gateway",
        "saladcloud-ai-gateway",
    ),
    display_name="Salad AI Gateway",
    description="Salad AI Gateway - OpenAI-compatible open model API",
    signup_url="https://salad.com/ai-gateway",
    env_vars=("SALAD_CLOUD_API_KEY",),
    base_url="https://ai.salad.cloud/v1",
    models_url="https://ai.salad.cloud/v1/models",
    auth_type="api_key",
    default_aux_model="qwen3.5-9b",
    fallback_models=(
        "qwen3.6-35b-a3b",
        "qwen3.6-27b",
        "qwen3.5-9b",
    ),
)

register_provider(saladcloud)
