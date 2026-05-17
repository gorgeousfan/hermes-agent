import json
import pytest


ROUTING_PATCHES = {
    "_get_extract_backend": lambda: "fake",
    "is_safe_url": lambda url: True,
    "check_website_access": lambda url: None,
}


def patch_web_extract_dependencies(monkeypatch, web_tools, provider=None):
    for name, value in ROUTING_PATCHES.items():
        monkeypatch.setattr(web_tools, name, value)
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)
    if provider is not None:
        monkeypatch.setattr("agent.web_search_registry.get_provider", lambda name: provider)


def patch_direct_fetch(monkeypatch, web_tools, body, content_type="text/plain; charset=utf-8"):
    def fake_direct_fetch(url, timeout=20):
        return body, content_type, url

    monkeypatch.setattr(web_tools, "_direct_fetch_text", fake_direct_fetch)


class FakeExtractProvider:
    name = "fake"
    display_name = "Fake"

    def __init__(self, results=None):
        self.calls = []
        self.results = results or []

    def supports_extract(self):
        return True

    def extract(self, urls, **kwargs):
        self.calls.append((urls, kwargs))
        return self.results


@pytest.mark.asyncio
async def test_web_extract_routes_github_blob_to_raw(monkeypatch):
    from tools import web_tools

    provider = FakeExtractProvider()
    patch_web_extract_dependencies(monkeypatch, web_tools, provider)

    calls = []

    def fake_direct_fetch(url, timeout=20):
        calls.append(url)
        return "# Hello from raw github\n", "text/plain; charset=utf-8", url

    monkeypatch.setattr(web_tools, "_direct_fetch_text", fake_direct_fetch)


    result = json.loads(
        await web_tools.web_extract_tool(
            ["https://github.com/octo/repo/blob/main/README.md"],
            use_llm_processing=False,
        )
    )

    assert calls == ["https://raw.githubusercontent.com/octo/repo/main/README.md"]
    entry = result["results"][0]
    assert entry["content"] == "# Hello from raw github\n"
    assert entry["url"] == "https://raw.githubusercontent.com/octo/repo/main/README.md"
    assert entry["title"] == "README.md"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_web_extract_routed_url_does_not_require_configured_provider(monkeypatch):
    from tools import web_tools

    patch_web_extract_dependencies(monkeypatch, web_tools)
    monkeypatch.setattr(
        "agent.web_search_registry.get_provider",
        lambda name: pytest.fail("provider lookup should not run for routed-only URLs"),
    )

    patch_direct_fetch(monkeypatch, web_tools, "# Hello from raw github\n")

    result = json.loads(
        await web_tools.web_extract_tool(
            ["https://github.com/octo/repo/blob/main/README.md"],
            use_llm_processing=False,
        )
    )

    assert result["results"][0]["url"] == (
        "https://raw.githubusercontent.com/octo/repo/main/README.md"
    )


@pytest.mark.asyncio
async def test_web_extract_routes_x_status_to_raw_html_fallback(monkeypatch):
    from tools import web_tools

    provider = FakeExtractProvider()
    patch_web_extract_dependencies(monkeypatch, web_tools, provider)

    html_doc = r'''
    <html>
      <head>
        <meta property="og:title" content="helicerat (@helicerat0x)">
        <meta property="og:description" content="linked article preview text">
      </head>
      <body>
        <script>
          window.__DATA__ = {"full_text":"https://t.co/rwV6oYg5Rd","expanded_url":"https:\/\/x.com\/i\/article\/2053497254510460928"};
        </script>
      </body>
    </html>
    '''

    patch_direct_fetch(monkeypatch, web_tools, html_doc, "text/html; charset=utf-8")


    result = json.loads(
        await web_tools.web_extract_tool(
            ["https://x.com/helicerat0x/status/2054223640573493523"],
            use_llm_processing=False,
        )
    )

    entry = result["results"][0]
    assert entry["title"] == "helicerat (@helicerat0x)"
    assert "Author: @helicerat0x" in entry["content"]
    assert "Tweet ID: 2054223640573493523" in entry["content"]
    assert "Linked article: https://x.com/i/article/2053497254510460928" in entry["content"]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_web_extract_uses_firecrawl_for_general_urls(monkeypatch):
    from tools import web_tools

    provider = FakeExtractProvider([
        {
            "url": "https://example.com",
            "title": "Example Domain",
            "content": "Example Domain body",
            "raw_content": "Example Domain body",
        }
    ])
    patch_web_extract_dependencies(monkeypatch, web_tools, provider)
    monkeypatch.setattr(
        web_tools,
        "_direct_fetch_text",
        lambda *a, **k: pytest.fail("direct fetch should not run for general URLs"),
    )

    result = json.loads(
        await web_tools.web_extract_tool(
            ["https://example.com"],
            use_llm_processing=False,
        )
    )

    entry = result["results"][0]
    assert entry["url"] == "https://example.com"
    assert entry["title"] == "Example Domain"
    assert entry["content"] == "Example Domain body"
    assert provider.calls == [(["https://example.com"], {"format": None})]


@pytest.mark.asyncio
async def test_web_extract_preserves_input_order_with_routed_and_generic_urls(monkeypatch):
    from tools import web_tools

    provider = FakeExtractProvider([
        {
            "url": "https://example.com",
            "title": "Example Domain",
            "content": "Example Domain body",
            "raw_content": "Example Domain body",
        }
    ])
    patch_web_extract_dependencies(monkeypatch, web_tools, provider)

    patch_direct_fetch(monkeypatch, web_tools, "# Hello from raw github\n")

    result = json.loads(
        await web_tools.web_extract_tool(
            [
                "https://example.com",
                "https://github.com/octo/repo/blob/main/README.md",
            ],
            use_llm_processing=False,
        )
    )

    urls = [entry["url"] for entry in result["results"]]
    assert urls == [
        "https://example.com",
        "https://raw.githubusercontent.com/octo/repo/main/README.md",
    ]
    assert provider.calls == [(["https://example.com"], {"format": None})]
