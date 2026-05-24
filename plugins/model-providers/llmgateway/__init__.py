"""LLM Gateway (llmgateway.io) provider profile.

LLM Gateway is a unified API gateway that routes to 100+ models across
multiple providers. Hermes sends an ``X-Source`` attribution header so
that requests originating from Hermes show up correctly in the LLM
Gateway dashboard, mirroring the ``HTTP-Referer`` / ``X-Title`` pattern
used for OpenRouter.
"""

from providers import register_provider
from providers.base import ProviderProfile

from hermes_cli import __version__ as _HERMES_VERSION


llmgateway = ProviderProfile(
    name="llmgateway",
    aliases=("llm-gateway", "llm_gateway"),
    env_vars=("LLM_GATEWAY_API_KEY", "LLMGATEWAY_API_KEY"),
    display_name="LLM Gateway",
    description="LLM Gateway — unified API gateway for 100+ models",
    signup_url="https://llmgateway.io/dashboard",
    base_url="https://api.llmgateway.io/v1",
    default_headers={
        "X-Source": "https://hermes-agent.nousresearch.com",
        "User-Agent": f"HermesAgent/{_HERMES_VERSION}",
    },
    default_aux_model="google/gemini-3-flash",
)

register_provider(llmgateway)
