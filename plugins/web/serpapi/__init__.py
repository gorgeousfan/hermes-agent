"""SerpApi web search plugin — bundled, auto-loaded."""

from __future__ import annotations

from plugins.web.serpapi.provider import SerpApiWebSearchProvider


def register(ctx) -> None:
    """Register the SerpApi provider with the plugin context."""
    ctx.register_web_search_provider(SerpApiWebSearchProvider())
