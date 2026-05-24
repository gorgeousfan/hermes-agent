"""Direct OpenAI (api.openai.com) provider profile.

Routes ``provider: openai`` to ``https://api.openai.com/v1`` using
``OPENAI_API_KEY`` for auth. Distinct from ``openai-codex`` (OAuth →
Responses API for ChatGPT-account Codex) and from ``openrouter`` (the
multi-vendor aggregator that historically also accepted ``OPENAI_API_KEY``
for OpenAI-compat clients).

Why this profile exists (issue #31179):

Users who set ``auxiliary.vision.provider: openai`` — a natural choice for
routing screenshots to GPT-4o-mini when the main model (e.g. DeepSeek V4)
is text-only — used to hit a silent fallback to the main model because
``openai`` was not a recognised provider name. Vision calls then 400'd
with ``unknown variant image_url, expected text``.  Registering ``openai``
as a first-class profile lets that config Just Work.
"""

from providers import register_provider
from providers.base import ProviderProfile

openai = ProviderProfile(
    name="openai",
    aliases=("openai-direct", "openai_api"),
    api_mode="chat_completions",
    env_vars=("OPENAI_API_KEY",),
    base_url="https://api.openai.com/v1",
    auth_type="api_key",
    display_name="OpenAI",
    description="OpenAI — direct api.openai.com",
    signup_url="https://platform.openai.com/api-keys",
    fallback_models=(
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-5",
        "gpt-5-mini",
    ),
    default_aux_model="gpt-4o-mini",
)

register_provider(openai)
