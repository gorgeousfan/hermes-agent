"""SerpApi web search provider — plugin form.

Subclasses :class:`agent.web_search_provider.WebSearchProvider` and maps
SerpApi's JSON search responses to Hermes' standard ``web_search`` shape.

Config keys this provider responds to::

    web:
      search_backend: "serpapi"     # explicit per-capability
      backend: "serpapi"            # shared fallback

Env vars::

    SERPAPI_API_KEY=...             # https://serpapi.com
    SERPAPI_ENGINE=google_light     # optional, default google_light
    SERPAPI_LOCATION=...            # optional, e.g. Austin, Texas, United States
    SERPAPI_GL=us                   # optional country code
    SERPAPI_HL=en                   # optional language code
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

_SERPAPI_ENDPOINT = "https://serpapi.com/search"
_DEFAULT_ENGINE = "google_light"


def _serpapi_search_params(query: str) -> Dict[str, str]:
    """Build SerpApi query params from env-backed configuration."""
    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "SERPAPI_API_KEY environment variable not set. "
            "Get your API key at https://serpapi.com"
        )

    params: Dict[str, str] = {
        "api_key": api_key,
        "engine": os.getenv("SERPAPI_ENGINE", _DEFAULT_ENGINE).strip() or _DEFAULT_ENGINE,
        "q": query,
    }
    optional_env = {
        "location": "SERPAPI_LOCATION",
        "gl": "SERPAPI_GL",
        "hl": "SERPAPI_HL",
    }
    for param_name, env_name in optional_env.items():
        value = os.getenv(env_name, "").strip()
        if value:
            params[param_name] = value
    return params


def _normalize_serpapi_results(response: Dict[str, Any], limit: int) -> Dict[str, Any]:
    """Map SerpApi response JSON to Hermes' standard web search result shape."""
    if response.get("error"):
        return {"success": False, "error": f"SerpApi search failed: {response['error']}"}

    raw_results = response.get("organic_results") or []
    web_results: List[Dict[str, Any]] = []
    for index, result in enumerate(raw_results[: max(0, limit)]):
        url = result.get("link") or ""
        description = result.get("snippet") or ""
        if not isinstance(description, str):
            description = str(description)
        web_results.append(
            {
                "title": str(result.get("title", "")),
                "url": str(url),
                "description": description,
                "position": int(result.get("position") or index + 1),
            }
        )
    return {"success": True, "data": {"web": web_results}}


class SerpApiWebSearchProvider(WebSearchProvider):
    """Search-only provider backed by SerpApi's JSON Search API."""

    @property
    def name(self) -> str:
        return "serpapi"

    @property
    def display_name(self) -> str:
        return "SerpApi"

    def is_available(self) -> bool:
        """Return True when ``SERPAPI_API_KEY`` is set to a non-empty value."""
        return bool(os.getenv("SERPAPI_API_KEY", "").strip())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a SerpApi search and normalize organic results."""
        import httpx

        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return {"success": False, "error": "Interrupted"}

            clamped_limit = max(1, min(int(limit), 20))
            params = _serpapi_search_params(query)
            logger.info(
                "SerpApi search: '%s' (engine=%s, limit=%d)",
                query,
                params.get("engine"),
                clamped_limit,
            )
            response = httpx.get(_SERPAPI_ENDPOINT, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return {"success": False, "error": "SerpApi search failed: non-object JSON response"}
            return _normalize_serpapi_results(data, clamped_limit)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except httpx.HTTPStatusError as exc:
            logger.warning("SerpApi HTTP error: %s", exc)
            return {
                "success": False,
                "error": f"SerpApi returned HTTP {exc.response.status_code}",
            }
        except httpx.RequestError as exc:
            logger.warning("SerpApi request error: %s", exc)
            return {"success": False, "error": f"Could not reach SerpApi: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("SerpApi search error: %s", exc)
            return {"success": False, "error": f"SerpApi search failed: {exc}"}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "SerpApi",
            "badge": "paid",
            "tag": "Structured search results from Google and many other engines.",
            "env_vars": [
                {
                    "key": "SERPAPI_API_KEY",
                    "prompt": "SerpApi API key",
                    "url": "https://serpapi.com/",
                },
            ],
        }
