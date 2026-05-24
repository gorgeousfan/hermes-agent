"""Jina AI Reader extraction plugin.

Uses Jina Reader (https://r.jina.ai/) to fetch markdown-ish page content by
prefixing the target URL. No API key is required, so this is a good local-first
fallback for web_extract when paid extract backends are absent or invalid.
"""

from __future__ import annotations

from plugins.web.jina.provider import JinaWebExtractProvider


def register(ctx) -> None:
    """Register the Jina extract provider with the plugin context."""
    ctx.register_web_search_provider(JinaWebExtractProvider())
