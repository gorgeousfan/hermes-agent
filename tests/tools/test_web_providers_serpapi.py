"""Tests for the SerpApi web search provider plugin."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx


def test_serpapi_requires_api_key(monkeypatch):
    from plugins.web.serpapi.provider import SerpApiWebSearchProvider

    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    provider = SerpApiWebSearchProvider()

    assert provider.is_available() is False
    result = provider.search("coffee")

    assert result["success"] is False
    assert "SERPAPI_API_KEY" in result["error"]


def test_serpapi_search_normalizes_organic_results(monkeypatch):
    from plugins.web.serpapi.provider import SerpApiWebSearchProvider

    captured = {}

    def fake_get(url, *, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        response = MagicMock()
        response.json.return_value = {
            "organic_results": [
                {
                    "title": "Coffee - Wikipedia",
                    "link": "https://en.wikipedia.org/wiki/Coffee",
                    "snippet": "Coffee is a brewed drink.",
                    "position": 3,
                },
                {
                    "title": "National Coffee Association",
                    "link": "https://www.ncausa.org/",
                    "snippet": "Coffee facts and trends.",
                },
            ]
        }
        response.raise_for_status.return_value = None
        return response

    monkeypatch.setenv("SERPAPI_API_KEY", "serp-test")
    monkeypatch.setattr(httpx, "get", fake_get)

    result = SerpApiWebSearchProvider().search("coffee", limit=1)

    assert result == {
        "success": True,
        "data": {
            "web": [
                {
                    "title": "Coffee - Wikipedia",
                    "url": "https://en.wikipedia.org/wiki/Coffee",
                    "description": "Coffee is a brewed drink.",
                    "position": 3,
                }
            ]
        },
    }
    assert captured["url"] == "https://serpapi.com/search"
    assert captured["params"]["api_key"] == "serp-test"
    assert captured["params"]["engine"] == "google_light"
    assert captured["params"]["q"] == "coffee"
    assert captured["timeout"] == 30


def test_serpapi_normalizer_only_uses_documented_organic_keys():
    from plugins.web.serpapi.provider import _normalize_serpapi_results

    result = _normalize_serpapi_results(
        {
            "organic_results": [
                {
                    "title": "Documented result",
                    "link": "https://example.com/documented",
                    "snippet": "Documented snippet.",
                    "position": 2,
                },
                {
                    "title": "Undocumented fallbacks",
                    "url": "https://example.com/undocumented",
                    "description": "Undocumented description.",
                    "rich_snippet": {
                        "top": {
                            "detected_extensions": {
                                "rating": 4.8,
                            }
                        }
                    },
                },
            ]
        },
        limit=2,
    )

    assert result == {
        "success": True,
        "data": {
            "web": [
                {
                    "title": "Documented result",
                    "url": "https://example.com/documented",
                    "description": "Documented snippet.",
                    "position": 2,
                },
                {
                    "title": "Undocumented fallbacks",
                    "url": "",
                    "description": "",
                    "position": 2,
                },
            ]
        },
    }


def test_serpapi_search_accepts_engine_and_localization_env(monkeypatch):
    from plugins.web.serpapi.provider import SerpApiWebSearchProvider

    captured = {}

    def fake_get(url, *, params, timeout):
        captured.update(params)
        response = MagicMock()
        response.json.return_value = {"organic_results": []}
        response.raise_for_status.return_value = None
        return response

    monkeypatch.setenv("SERPAPI_API_KEY", "serp-test")
    monkeypatch.setenv("SERPAPI_ENGINE", "google")
    monkeypatch.setenv("SERPAPI_LOCATION", "Austin, Texas, United States")
    monkeypatch.setenv("SERPAPI_GL", "us")
    monkeypatch.setenv("SERPAPI_HL", "en")
    monkeypatch.setattr(httpx, "get", fake_get)

    result = SerpApiWebSearchProvider().search("coffee", limit=5)

    assert result == {"success": True, "data": {"web": []}}
    assert captured["engine"] == "google"
    assert captured["location"] == "Austin, Texas, United States"
    assert captured["gl"] == "us"
    assert captured["hl"] == "en"


def test_serpapi_surfaces_api_errors(monkeypatch):
    from plugins.web.serpapi.provider import SerpApiWebSearchProvider

    def fake_get(url, *, params, timeout):
        response = MagicMock()
        response.json.return_value = {"error": "Invalid API key"}
        response.raise_for_status.return_value = None
        return response

    monkeypatch.setenv("SERPAPI_API_KEY", "bad-key")
    monkeypatch.setattr(httpx, "get", fake_get)

    result = SerpApiWebSearchProvider().search("coffee")

    assert result["success"] is False
    assert result["error"] == "SerpApi search failed: Invalid API key"
