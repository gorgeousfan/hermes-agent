"""Fireworks AI provider profile.

Fireworks AI provides fast, efficient inference for open and proprietary
models through an OpenAI-compatible chat completions endpoint.
"""

from providers import register_provider
from providers.base import ProviderProfile


fireworks = ProviderProfile(
    name="fireworks",
    aliases=("fireworks-ai", "fw"),
    display_name="Fireworks AI",
    description="Fireworks AI — fast inference for open and proprietary models",
    signup_url="https://app.fireworks.ai/settings/users/api-keys",
    env_vars=("FIREWORKS_API_KEY", "FIREWORKS_BASE_URL"),
    base_url="https://api.fireworks.ai/inference/v1",
    auth_type="api_key",
    default_aux_model="accounts/fireworks/models/minimax-m2p5",
    fallback_models=(
        "accounts/fireworks/routers/kimi-k2p6-turbo",
        "accounts/fireworks/models/glm-5p1",
        "accounts/fireworks/models/minimax-m2p5",
    ),
)

register_provider(fireworks)
