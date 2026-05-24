"""Tests for hermes_constants.apply_ipv4_preference() — socket.getaddrinfo monkeypatch."""
import socket

import pytest

from hermes_constants import apply_ipv4_preference


@pytest.fixture(autouse=True)
def _restore_getaddrinfo():
    original = socket.getaddrinfo
    yield
    socket.getaddrinfo = original


class TestApplyIpv4Preference:
    def test_noop_when_force_false(self):
        original = socket.getaddrinfo
        apply_ipv4_preference(force=False)
        assert socket.getaddrinfo is original

    def test_default_is_force_false(self):
        original = socket.getaddrinfo
        apply_ipv4_preference()
        assert socket.getaddrinfo is original

    def test_patches_when_force_true(self):
        original = socket.getaddrinfo
        apply_ipv4_preference(force=True)
        assert socket.getaddrinfo is not original
        assert getattr(socket.getaddrinfo, "_hermes_ipv4_patched", False) is True

    def test_idempotent_double_apply(self):
        apply_ipv4_preference(force=True)
        first = socket.getaddrinfo
        apply_ipv4_preference(force=True)
        assert socket.getaddrinfo is first

    def test_passes_through_explicit_family(self, monkeypatch):
        calls = []

        def fake_resolver(host, port, family=0, type=0, proto=0, flags=0):
            calls.append(family)
            return [(family, 0, 0, "", (host, port))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_resolver)
        apply_ipv4_preference(force=True)
        socket.getaddrinfo("example.com", 80, socket.AF_INET6)
        assert calls == [socket.AF_INET6]

    def test_unspec_request_forces_ipv4(self, monkeypatch):
        calls = []

        def fake_resolver(host, port, family=0, type=0, proto=0, flags=0):
            calls.append(family)
            return [(family, 0, 0, "", (host, port))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_resolver)
        apply_ipv4_preference(force=True)
        socket.getaddrinfo("example.com", 80)
        assert calls == [socket.AF_INET]

    def test_falls_back_to_original_on_gaierror(self, monkeypatch):
        calls = []

        def fake_resolver(host, port, family=0, type=0, proto=0, flags=0):
            calls.append(family)
            if family == socket.AF_INET:
                raise socket.gaierror("no A record")
            return [(family, 0, 0, "", (host, port))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_resolver)
        apply_ipv4_preference(force=True)
        result = socket.getaddrinfo("ipv6-only.example", 80)
        assert calls == [socket.AF_INET, 0]
        assert result
