"""Tests for Mem0 API v2 compatibility — filters param and dict response unwrapping.

Salvaged from PRs #5301 (qaqcvc) and #5117 (vvvanguards).
"""

import json
import sys
import types
import pytest

from plugins.memory.mem0 import Mem0MemoryProvider, _SelfHostedMem0Client, _load_config


class FakeClientV2:
    """Fake Mem0 client that returns v2-style dict responses and captures call kwargs."""

    def __init__(self, search_results=None, all_results=None):
        self._search_results = search_results or {"results": []}
        self._all_results = all_results or {"results": []}
        self.captured_search = {}
        self.captured_get_all = {}
        self.captured_add = []

    def search(self, **kwargs):
        self.captured_search = kwargs
        return self._search_results

    def get_all(self, **kwargs):
        self.captured_get_all = kwargs
        return self._all_results

    def add(self, messages, **kwargs):
        self.captured_add.append({"messages": messages, **kwargs})


# ---------------------------------------------------------------------------
# Filter migration: bare user_id= -> filters={}
# ---------------------------------------------------------------------------


class TestMem0FiltersV2:
    """All API calls must use filters={} instead of bare user_id= kwargs."""

    def _make_provider(self, monkeypatch, client):
        provider = Mem0MemoryProvider()
        provider.initialize("test-session")
        provider._user_id = "u123"
        provider._agent_id = "hermes"
        monkeypatch.setattr(provider, "_get_client", lambda: client)
        return provider

    def test_search_uses_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)

        provider.handle_tool_call("mem0_search", {"query": "hello", "top_k": 3, "rerank": False})

        assert client.captured_search["query"] == "hello"
        assert client.captured_search["top_k"] == 3
        assert client.captured_search["rerank"] is False
        assert client.captured_search["filters"] == {"user_id": "u123"}
        # Must NOT have bare user_id kwarg
        assert "user_id" not in {k for k in client.captured_search if k != "filters"}

    def test_profile_uses_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)

        provider.handle_tool_call("mem0_profile", {})

        assert client.captured_get_all["filters"] == {"user_id": "u123"}
        assert "user_id" not in {k for k in client.captured_get_all if k != "filters"}

    def test_prefetch_uses_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)

        provider.queue_prefetch("hello")
        provider._prefetch_thread.join(timeout=2)

        assert client.captured_search["query"] == "hello"
        assert client.captured_search["filters"] == {"user_id": "u123"}
        assert "user_id" not in {k for k in client.captured_search if k != "filters"}

    def test_sync_turn_uses_write_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)

        provider.sync_turn("user said this", "assistant replied", session_id="s1")
        provider._sync_thread.join(timeout=2)

        assert len(client.captured_add) == 1
        call = client.captured_add[0]
        assert call["user_id"] == "u123"
        assert call["agent_id"] == "hermes"

    def test_conclude_uses_write_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)

        provider.handle_tool_call("mem0_conclude", {"conclusion": "user likes dark mode"})

        assert len(client.captured_add) == 1
        call = client.captured_add[0]
        assert call["user_id"] == "u123"
        assert call["agent_id"] == "hermes"
        assert call["infer"] is False

    def test_read_filters_no_agent_id(self):
        """Read filters should use user_id only — cross-session recall across agents."""
        provider = Mem0MemoryProvider()
        provider._user_id = "u123"
        provider._agent_id = "hermes"
        assert provider._read_filters() == {"user_id": "u123"}

    def test_write_filters_include_agent_id(self):
        """Write filters should include agent_id for attribution."""
        provider = Mem0MemoryProvider()
        provider._user_id = "u123"
        provider._agent_id = "hermes"
        assert provider._write_filters() == {"user_id": "u123", "agent_id": "hermes"}


# ---------------------------------------------------------------------------
# Dict response unwrapping (API v2 wraps in {"results": [...]})
# ---------------------------------------------------------------------------


class TestMem0ResponseUnwrapping:
    """API v2 returns {"results": [...]} dicts; we must extract the list."""

    def _make_provider(self, monkeypatch, client):
        provider = Mem0MemoryProvider()
        provider.initialize("test-session")
        monkeypatch.setattr(provider, "_get_client", lambda: client)
        return provider

    def test_profile_dict_response(self, monkeypatch):
        client = FakeClientV2(all_results={"results": [{"memory": "alpha"}, {"memory": "beta"}]})
        provider = self._make_provider(monkeypatch, client)

        result = json.loads(provider.handle_tool_call("mem0_profile", {}))

        assert result["count"] == 2
        assert "alpha" in result["result"]
        assert "beta" in result["result"]

    def test_profile_list_response_backward_compat(self, monkeypatch):
        """Old API returned bare lists — still works."""
        client = FakeClientV2(all_results=[{"memory": "gamma"}])
        provider = self._make_provider(monkeypatch, client)

        result = json.loads(provider.handle_tool_call("mem0_profile", {}))
        assert result["count"] == 1
        assert "gamma" in result["result"]

    def test_search_dict_response(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [{"memory": "foo", "score": 0.9}, {"memory": "bar", "score": 0.7}]
        })
        provider = self._make_provider(monkeypatch, client)

        result = json.loads(provider.handle_tool_call(
            "mem0_search", {"query": "test", "top_k": 5}
        ))

        assert result["count"] == 2
        assert result["results"][0]["memory"] == "foo"

    def test_search_list_response_backward_compat(self, monkeypatch):
        """Old API returned bare lists — still works."""
        client = FakeClientV2(search_results=[{"memory": "baz", "score": 0.8}])
        provider = self._make_provider(monkeypatch, client)

        result = json.loads(provider.handle_tool_call(
            "mem0_search", {"query": "test"}
        ))
        assert result["count"] == 1

    def test_unwrap_results_edge_cases(self):
        """_unwrap_results handles all shapes gracefully."""
        assert Mem0MemoryProvider._unwrap_results({"results": [1, 2]}) == [1, 2]
        assert Mem0MemoryProvider._unwrap_results([3, 4]) == [3, 4]
        assert Mem0MemoryProvider._unwrap_results({}) == []
        assert Mem0MemoryProvider._unwrap_results(None) == []
        assert Mem0MemoryProvider._unwrap_results("unexpected") == []

    def test_prefetch_dict_response(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [{"memory": "user prefers dark mode"}]
        })
        provider = Mem0MemoryProvider()
        provider.initialize("test-session")
        monkeypatch.setattr(provider, "_get_client", lambda: client)

        provider.queue_prefetch("preferences")
        provider._prefetch_thread.join(timeout=2)
        result = provider.prefetch("preferences")

        assert "dark mode" in result


# ---------------------------------------------------------------------------
# Default preservation
# ---------------------------------------------------------------------------


class TestMem0Defaults:
    """Ensure we don't break existing users' defaults."""

    def test_default_user_id_hermes_user(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM0_API_KEY", "test-key")
        monkeypatch.delenv("MEM0_USER_ID", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        provider = Mem0MemoryProvider()
        provider.initialize("test")

        assert provider._user_id == "hermes-user"

    def test_default_agent_id_hermes(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM0_API_KEY", "test-key")
        monkeypatch.delenv("MEM0_AGENT_ID", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        provider = Mem0MemoryProvider()
        provider.initialize("test")

        assert provider._agent_id == "hermes"

    def test_mem0_host_env_loaded(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM0_API_KEY", "test-key")
        monkeypatch.setenv("MEM0_HOST", "https://mem0.example.com")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        cfg = _load_config()

        assert cfg["host"] == "https://mem0.example.com"

    def test_mem0_json_host_overrides_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM0_API_KEY", "test-key")
        monkeypatch.setenv("MEM0_HOST", "https://env.example.com")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "mem0.json").write_text(
            json.dumps({"host": "https://file.example.com"}),
            encoding="utf-8",
        )

        cfg = _load_config()

        assert cfg["host"] == "https://file.example.com"

    def test_save_config_removes_blank_host(self, tmp_path):
        config_path = tmp_path / "mem0.json"
        config_path.write_text(
            json.dumps({
                "host": "https://mem0.example.com",
                "user_id": "hermes-user",
            }),
            encoding="utf-8",
        )
        provider = Mem0MemoryProvider()

        provider.save_config({"host": ""}, tmp_path)

        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        assert "host" not in cfg
        assert cfg["user_id"] == "hermes-user"

    def test_memory_client_omits_empty_host(self, monkeypatch):
        captured_kwargs = {}

        class FakeMemoryClient:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

        fake_mem0 = types.SimpleNamespace(MemoryClient=FakeMemoryClient)
        monkeypatch.setitem(sys.modules, "mem0", fake_mem0)

        provider = Mem0MemoryProvider()
        provider._api_key = "test-key"
        provider._host = ""

        provider._get_client()

        assert captured_kwargs == {"api_key": "test-key"}

    def test_memory_client_uses_configured_host(self, monkeypatch):
        provider = Mem0MemoryProvider()
        provider._api_key = "test-key"
        provider._host = "https://mem0.example.com"

        client = provider._get_client()

        assert isinstance(client, _SelfHostedMem0Client)

    def test_self_hosted_client_uses_oss_routes(self, monkeypatch):
        captured_calls = []

        class FakeResponse:
            content = b"{}"

            def raise_for_status(self):
                pass

            def json(self):
                return {"results": []}

        def fake_request(method, url, **kwargs):
            captured_calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

        fake_requests = types.SimpleNamespace(request=fake_request)
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        client = _SelfHostedMem0Client(
            api_key="test-key",
            host="https://mem0.example.com",
        )
        client.add([{"role": "user", "content": "hello"}], user_id="u123", agent_id="hermes")
        client.search(query="hello", filters={"user_id": "u123"}, top_k=3, rerank=False)
        client.get_all(filters={"user_id": "u123"})

        assert captured_calls[0]["method"] == "POST"
        assert captured_calls[0]["url"] == "https://mem0.example.com/memories"
        assert captured_calls[0]["headers"]["X-API-Key"] == "test-key"
        assert captured_calls[0]["json"]["user_id"] == "u123"
        assert captured_calls[0]["json"]["agent_id"] == "hermes"

        assert captured_calls[1]["method"] == "POST"
        assert captured_calls[1]["url"] == "https://mem0.example.com/search"
        assert captured_calls[1]["json"] == {
            "query": "hello",
            "filters": {"user_id": "u123"},
            "top_k": 3,
            "rerank": False,
        }

        assert captured_calls[2]["method"] == "GET"
        assert captured_calls[2]["url"] == "https://mem0.example.com/memories"
        assert captured_calls[2]["params"] == {"user_id": "u123"}

    def test_self_hosted_api_key_uses_only_x_api_key(self, monkeypatch):
        captured_calls = []

        class FakeResponse:
            content = b"{}"

            def raise_for_status(self):
                pass

            def json(self):
                return {}

        def fake_request(method, url, **kwargs):
            captured_calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

        fake_requests = types.SimpleNamespace(request=fake_request)
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        client = _SelfHostedMem0Client(api_key="mem0-test-key", host="https://mem0.example.com")
        client.get_all(filters={"user_id": "u123"})

        headers = captured_calls[0]["headers"]
        assert headers["X-API-Key"] == "mem0-test-key"
        assert "Authorization" not in headers
