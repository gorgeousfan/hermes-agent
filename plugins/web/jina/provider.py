"""Jina AI Reader web extraction provider.

Jina Reader turns a public web page into plain markdown-like text. It is
extract-only (no search/crawl) and requires no API key.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from urllib.parse import quote, urlparse

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)


class JinaWebExtractProvider(WebSearchProvider):
    """No-key extract provider backed by https://r.jina.ai/."""

    @property
    def name(self) -> str:
        return "jina"

    @property
    def display_name(self) -> str:
        return "Jina Reader"

    def is_available(self) -> bool:
        """Jina Reader is network-only and needs no local credentials."""
        return True

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract URL content through Jina Reader.

        Return the legacy list-of-documents shape consumed by
        tools.web_tools.web_extract_tool.
        """
        import httpx

        timeout = float(kwargs.get("timeout") or 60)
        documents: List[Dict[str, Any]] = []
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for url in urls:
                try:
                    reader_url = self._reader_url(url)
                    response = client.get(reader_url, headers={"Accept": "text/plain"})
                    response.raise_for_status()
                    content = response.text.strip()
                    documents.append(
                        {
                            "url": url,
                            "title": self._title_from_content(content),
                            "content": content,
                            "raw_content": content,
                            "metadata": {"sourceURL": url, "provider": "jina", "readerURL": reader_url},
                        }
                    )
                except Exception as exc:  # noqa: BLE001 — provider boundary
                    logger.warning("Jina extract error for %s: %s", url, exc)
                    documents.append(
                        {
                            "url": url,
                            "title": "",
                            "content": "",
                            "raw_content": "",
                            "error": f"Jina extract failed: {exc}",
                            "metadata": {"sourceURL": url, "provider": "jina"},
                        }
                    )
        return documents

    @staticmethod
    def _reader_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Unsupported URL for Jina Reader: {url}")
        # Jina Reader accepts the target URL as the path following /http://.
        # Quote only characters that would break the reader URL path while
        # preserving the target URL's own scheme separators and query syntax.
        return "https://r.jina.ai/http://" + quote(url, safe=":/?#[]@!$&'()*+,;=%")

    @staticmethod
    def _title_from_content(content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("Title:"):
                return stripped.partition(":")[2].strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Jina Reader",
            "badge": "free · no key · extract only",
            "tag": "Extract page content through r.jina.ai without an API key (pair with any search provider)",
            "env_vars": [],
        }
