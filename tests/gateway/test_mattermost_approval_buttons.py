"""Tests for Mattermost interactive approval buttons."""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the repo root is importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from gateway.platforms.mattermost import MattermostAdapter
from gateway.config import Platform, PlatformConfig


def _make_adapter(*, callback_url: str = "http://hermes.local:8644/hermes-approval") -> MattermostAdapter:
    """Create a MattermostAdapter with mocked internals for unit testing."""
    config = PlatformConfig(enabled=True, token="xoxb-test-token")
    adapter = MattermostAdapter(config)
    adapter._base_url = "http://mattermost.local"
    adapter._bot_user_id = "bot_user_id"
    adapter._bot_username = "hermes-bot"
    adapter._session = MagicMock()
    # Pre-configure callback URL and route registration flag.
    if callback_url:
        os.environ["MATTERMOST_CALLBACK_URL"] = callback_url
        adapter._callback_route_registered = True
    else:
        os.environ.pop("MATTERMOST_CALLBACK_URL", None)
        adapter._callback_route_registered = False
    return adapter


def _make_request(body: dict) -> MagicMock:
    """Build a fake aiohttp Request carrying the given JSON body."""
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


# ===========================================================================
# send_exec_approval — interactive card rendering
# ===========================================================================

class TestMattermostExecApproval:
    """Tests for send_exec_approval."""

    def setup_method(self):
        os.environ["MATTERMOST_CALLBACK_URL"] = "http://hermes.local:8644/hermes-approval"

    def teardown_method(self):
        os.environ.pop("MATTERMOST_CALLBACK_URL", None)
        os.environ.pop("MATTERMOST_ALLOWED_USERS", None)

    @pytest.mark.asyncio
    async def test_no_callback_url(self):
        """Returns failure when MATTERMOST_CALLBACK_URL is not set."""
        os.environ.pop("MATTERMOST_CALLBACK_URL", None)
        adapter = _make_adapter(callback_url="")
        result = await adapter.send_exec_approval(
            chat_id="ch1", command="rm -rf /", session_key="s1",
        )
        assert result.success is False
        assert "MATTERMOST_CALLBACK_URL" in (result.error or "")

    @pytest.mark.asyncio
    async def test_route_not_registered(self):
        """Returns failure when the callback route could not be registered."""
        adapter = _make_adapter()
        adapter._callback_route_registered = False
        result = await adapter.send_exec_approval(
            chat_id="ch1", command="rm -rf /", session_key="s1",
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_sends_interactive_card(self):
        """Posts a Mattermost interactive message with four buttons."""
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(return_value={"id": "post_abc"})

        result = await adapter.send_exec_approval(
            chat_id="ch1",
            command="rm -rf /important",
            session_key="agent:main:mm:dm:ch1:1111",
            description="recursive delete",
        )

        assert result.success is True
        assert result.message_id == "post_abc"
        adapter._api_post.assert_called_once()
        path, payload = adapter._api_post.call_args[0]
        assert path == "posts"
        assert payload["channel_id"] == "ch1"
        assert "Command approval required" in payload["message"]
        attachments = payload["props"]["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["callback_id"] == "hermes_approval"
        # Duplicate header must NOT appear inside the attachment text
        assert "Command Approval Required" not in attachments[0]["text"]
        assert "Reason:" in attachments[0]["text"]
        actions = attachments[0]["actions"]
        assert len(actions) == 4
        # Mattermost silently rejects action ids with underscores or hyphens
        # (github.com/mattermost/mattermost/issues/25747)
        for action in actions:
            assert "_" not in action["id"] and "-" not in action["id"], (
                f"Mattermost silently rejects action ids containing _ or - . Got: {action['id']}"
            )

    @pytest.mark.asyncio
    async def test_button_fields_are_mattermost_format(self):
        """Button objects use name/integration.url/integration.context (not Slack fields)."""
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(return_value={"id": "post_xyz"})

        await adapter.send_exec_approval(
            chat_id="ch1",
            command="echo test",
            session_key="sk-test",
        )

        _, payload = adapter._api_post.call_args[0]
        actions = payload["props"]["attachments"][0]["actions"]
        choices = []
        for action in actions:
            assert "name" in action, "Mattermost button must use 'name' not 'text'"
            assert "integration" in action
            assert "url" in action["integration"]
            assert "context" in action["integration"]
            ctx = action["integration"]["context"]
            assert "approval_id" in ctx, "Button context must use 'approval_id', not 'session_key'"
            assert "session_key" not in ctx, "session_key must not be exposed in button context"
            assert "choice" in ctx
            choices.append(ctx["choice"])
            # Must NOT have Slack-style fields
            assert "action_id" not in action
            assert "value" not in action

        assert sorted(choices) == sorted(["once", "session", "always", "deny"])

    @pytest.mark.asyncio
    async def test_button_choices_and_labels(self):
        """Each button carries the correct choice and label."""
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(return_value={"id": "post1"})

        await adapter.send_exec_approval(
            chat_id="ch1", command="ls", session_key="sk",
        )

        _, payload = adapter._api_post.call_args[0]
        actions = payload["props"]["attachments"][0]["actions"]
        by_choice = {
            a["integration"]["context"]["choice"]: a for a in actions
        }
        assert by_choice["once"]["name"] == "Allow Once"
        assert by_choice["session"]["name"] == "Allow Session"
        assert by_choice["always"]["name"] == "Always Allow"
        assert by_choice["deny"]["name"] == "Deny"

    @pytest.mark.asyncio
    async def test_approval_id_embedded_in_buttons_and_state_maps_to_session(self):
        """Each button embeds approval_id (not session_key); _approval_state maps it back."""
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(return_value={"id": "p1"})

        session_key = "agent:main:mm:dm:ch1:9999"
        await adapter.send_exec_approval(
            chat_id="ch1", command="cmd", session_key=session_key,
        )

        _, payload = adapter._api_post.call_args[0]
        actions = payload["props"]["attachments"][0]["actions"]

        # All four buttons must carry the same approval_id token (not session_key).
        approval_ids = {
            action["integration"]["context"]["approval_id"]
            for action in actions
        }
        assert len(approval_ids) == 1, "All buttons for one card must share a single approval_id"
        (approval_id,) = approval_ids

        # The approval_id must NOT be the session_key itself.
        assert approval_id != session_key

        # _approval_state must map approval_id → session_key.
        assert adapter._approval_state[approval_id] == session_key

    @pytest.mark.asyncio
    async def test_callback_url_embedded_in_buttons(self):
        """The MATTERMOST_CALLBACK_URL is used as integration.url on every button."""
        url = "http://hermes.local:8644/hermes-approval"
        os.environ["MATTERMOST_CALLBACK_URL"] = url
        adapter = _make_adapter(callback_url=url)
        adapter._api_post = AsyncMock(return_value={"id": "p2"})

        await adapter.send_exec_approval(
            chat_id="ch1", command="cmd", session_key="sk",
        )

        _, payload = adapter._api_post.call_args[0]
        for action in payload["props"]["attachments"][0]["actions"]:
            assert action["integration"]["url"] == url

    @pytest.mark.asyncio
    async def test_truncates_long_command(self):
        """Commands longer than 3800 chars are truncated with '...'."""
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(return_value={"id": "p3"})

        long_cmd = "x" * 5000
        await adapter.send_exec_approval(
            chat_id="ch1", command=long_cmd, session_key="sk",
        )

        _, payload = adapter._api_post.call_args[0]
        text = payload["props"]["attachments"][0]["text"]
        assert "..." in text
        assert len(text) < 5000

    @pytest.mark.asyncio
    async def test_stores_approval_state_entry(self):
        """On success, approval_id is stored in _approval_state mapped to session_key."""
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(return_value={"id": "post_flag"})

        session_key = "sk-store-test"
        await adapter.send_exec_approval(
            chat_id="ch1", command="cmd", session_key=session_key,
        )

        assert len(adapter._approval_state) == 1
        stored_session = next(iter(adapter._approval_state.values()))
        assert stored_session == session_key

    @pytest.mark.asyncio
    async def test_api_failure_returns_send_result_failure(self):
        """When _api_post raises, send_exec_approval returns success=False."""
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(side_effect=Exception("connection refused"))

        result = await adapter.send_exec_approval(
            chat_id="ch1", command="cmd", session_key="sk",
        )

        assert result.success is False

    @pytest.mark.asyncio
    async def test_api_exception_leaves_no_orphan_in_approval_state(self):
        """When _api_post raises, _approval_state must stay empty.

        Regression test for the bug where approval_id was registered before
        _api_post was called: a failed post would leave an entry that could
        never be resolved and would grow _approval_state unboundedly under
        repeated failures.
        """
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(side_effect=Exception("network error"))

        result = await adapter.send_exec_approval(
            chat_id="ch1", command="cmd", session_key="sk-orphan",
        )

        assert result.success is False
        assert adapter._approval_state == {}, (
            "_approval_state must be empty after a network exception — no orphaned entries"
        )

    @pytest.mark.asyncio
    async def test_http_error_response_returns_failure_and_no_orphan(self):
        """When _api_post returns {} (HTTP 4xx/5xx, no exception), send_exec_approval
        returns success=False and leaves _approval_state empty.

        _api_post logs the HTTP error and returns {} without raising, so the
        outer try/except does not fire.  The missing post_id is treated as a
        failure so the gateway can fall back to the plain-text prompt, and no
        orphaned approval_state entry is created.
        """
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(return_value={})

        result = await adapter.send_exec_approval(
            chat_id="ch1", command="cmd", session_key="sk-http-error",
        )

        assert result.success is False
        assert "post_id" in (result.error or "")
        assert adapter._approval_state == {}


# ===========================================================================
# _handle_approval_action — button click handler
# ===========================================================================

class TestMattermostApprovalAction:
    """Tests for _handle_approval_action."""

    def setup_method(self):
        os.environ["MATTERMOST_ALLOWED_USERS"] = "user_allowed"

    def teardown_method(self):
        os.environ.pop("MATTERMOST_ALLOWED_USERS", None)
        os.environ.pop("MATTERMOST_CALLBACK_URL", None)

    @pytest.mark.asyncio
    async def test_resolves_approval_once(self):
        """Allow Once click calls resolve_gateway_approval with 'once'."""
        adapter = _make_adapter()
        adapter._approval_state["aid1"] = "sk-test"
        adapter._lookup_username = AsyncMock(return_value="norbert")

        request = _make_request({
            "user_id": "user_allowed",
            "post_id": "post1",
            "context": {"approval_id": "aid1", "choice": "once"},
        })

        with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
            response = await adapter._handle_approval_action(request)

        mock_resolve.assert_called_once_with("sk-test", "once")
        body = response.body if hasattr(response, "body") else b""
        import json as _json
        data = _json.loads(body)
        assert "update" in data
        assert "Approved once by norbert" in data["update"]["message"]

    @pytest.mark.asyncio
    async def test_resolves_approval_session(self):
        """Allow Session click calls resolve_gateway_approval with 'session'."""
        adapter = _make_adapter()
        adapter._approval_state["aid2"] = "sk2"
        adapter._lookup_username = AsyncMock(return_value="alice")

        request = _make_request({
            "user_id": "user_allowed",
            "post_id": "post2",
            "context": {"approval_id": "aid2", "choice": "session"},
        })

        with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
            response = await adapter._handle_approval_action(request)

        mock_resolve.assert_called_once_with("sk2", "session")
        import json as _json
        data = _json.loads(response.body)
        assert "Approved for session by alice" in data["update"]["message"]

    @pytest.mark.asyncio
    async def test_resolves_approval_always(self):
        """Always Allow click calls resolve_gateway_approval with 'always'."""
        adapter = _make_adapter()
        adapter._approval_state["aid3"] = "sk3"
        adapter._lookup_username = AsyncMock(return_value="bob")

        request = _make_request({
            "user_id": "user_allowed",
            "post_id": "post3",
            "context": {"approval_id": "aid3", "choice": "always"},
        })

        with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
            response = await adapter._handle_approval_action(request)

        mock_resolve.assert_called_once_with("sk3", "always")
        import json as _json
        data = _json.loads(response.body)
        assert "Approved permanently by bob" in data["update"]["message"]

    @pytest.mark.asyncio
    async def test_resolves_approval_deny(self):
        """Deny click calls resolve_gateway_approval with 'deny'."""
        adapter = _make_adapter()
        adapter._approval_state["aid4"] = "sk4"
        adapter._lookup_username = AsyncMock(return_value="carol")

        request = _make_request({
            "user_id": "user_allowed",
            "post_id": "post4",
            "context": {"approval_id": "aid4", "choice": "deny"},
        })

        with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
            response = await adapter._handle_approval_action(request)

        mock_resolve.assert_called_once_with("sk4", "deny")
        import json as _json
        data = _json.loads(response.body)
        assert "Denied by carol" in data["update"]["message"]

    @pytest.mark.asyncio
    async def test_unauthorized_user_returns_403(self):
        """Clicks by users not in MATTERMOST_ALLOWED_USERS are rejected with 403.

        The approval token must NOT be consumed on a 403 so the legitimate user
        can still click.
        """
        adapter = _make_adapter()
        adapter._approval_state["aid5"] = "sk5"

        request = _make_request({
            "user_id": "intruder",
            "post_id": "post5",
            "context": {"approval_id": "aid5", "choice": "once"},
        })

        with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
            response = await adapter._handle_approval_action(request)

        assert response.status == 403
        mock_resolve.assert_not_called()
        # Token must still be present so the authorized user can still click.
        assert "aid5" in adapter._approval_state

    @pytest.mark.asyncio
    async def test_invalid_choice_returns_400(self):
        """An unknown choice value is rejected with 400.

        The approval token must NOT be consumed on a 400 so the user can retry.
        """
        adapter = _make_adapter()
        adapter._approval_state["aid6"] = "sk6"

        request = _make_request({
            "user_id": "user_allowed",
            "post_id": "post6",
            "context": {"approval_id": "aid6", "choice": "hack"},
        })

        with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
            response = await adapter._handle_approval_action(request)

        assert response.status == 400
        mock_resolve.assert_not_called()
        # Token must still be present after a rejected click.
        assert "aid6" in adapter._approval_state

    @pytest.mark.asyncio
    async def test_double_click_guard(self):
        """Second click on the same approval_id is silently ignored."""
        adapter = _make_adapter()
        adapter._approval_state["aid7"] = "sk7"
        adapter._lookup_username = AsyncMock(return_value="dave")

        body = {
            "user_id": "user_allowed",
            "post_id": "post7",
            "context": {"approval_id": "aid7", "choice": "once"},
        }

        with patch("tools.approval.resolve_gateway_approval", return_value=1):
            await adapter._handle_approval_action(_make_request(body))

        # Second click — approval_id has been popped from _approval_state.
        with patch("tools.approval.resolve_gateway_approval") as mock_resolve2:
            response2 = await adapter._handle_approval_action(_make_request(body))

        mock_resolve2.assert_not_called()
        # Should return a 200 ephemeral (not an error)
        assert response2.status == 200

    @pytest.mark.asyncio
    async def test_malformed_payload_returns_400(self):
        """A request whose body cannot be parsed as JSON returns 400."""
        adapter = _make_adapter()
        request = MagicMock()
        request.json = AsyncMock(side_effect=ValueError("bad json"))

        response = await adapter._handle_approval_action(request)
        assert response.status == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize("non_dict_body", [None, [], "string", 42])
    async def test_non_dict_body_returns_400(self, non_dict_body):
        """Valid JSON that is not a dict (null, array, string, int) returns 400.

        request.json() does not raise for these values, so without an explicit
        isinstance check the handler would call None.get(...) / [].get(...)
        and raise AttributeError outside the try/except, producing a 500.
        """
        adapter = _make_adapter()
        request = MagicMock()
        request.json = AsyncMock(return_value=non_dict_body)

        with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
            response = await adapter._handle_approval_action(request)

        assert response.status == 400
        mock_resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_approval_id_returns_already_resolved(self):
        """A click with no approval_id (e.g. stale pre-refactor card) is handled gracefully.

        pop("", None) returns None → the handler treats it as already resolved
        rather than crashing.  The response must be a 200 ephemeral, not a 500.
        """
        adapter = _make_adapter()
        # _approval_state is empty — nothing to pop.

        request = _make_request({
            "user_id": "user_allowed",
            "post_id": "old_post",
            "context": {"choice": "once"},  # approval_id key absent entirely
        })

        with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
            response = await adapter._handle_approval_action(request)

        mock_resolve.assert_not_called()
        assert response.status == 200
        import json as _json
        data = _json.loads(response.body)
        assert "already been resolved" in data.get("ephemeral_text", "")

    @pytest.mark.asyncio
    async def test_response_has_update_and_ephemeral(self):
        """Valid click response contains both update.message and ephemeral_text."""
        adapter = _make_adapter()
        adapter._approval_state["aid8"] = "sk8"
        adapter._lookup_username = AsyncMock(return_value="eve")

        request = _make_request({
            "user_id": "user_allowed",
            "post_id": "post8",
            "context": {"approval_id": "aid8", "choice": "once"},
        })

        with patch("tools.approval.resolve_gateway_approval", return_value=1):
            response = await adapter._handle_approval_action(request)

        import json as _json
        data = _json.loads(response.body)
        assert "update" in data
        assert "message" in data["update"]
        assert "props" in data["update"]
        assert "ephemeral_text" in data

    @pytest.mark.asyncio
    async def test_resolver_error_returns_500(self):
        """When resolve_gateway_approval raises, a 500 is returned.

        The approval token is NOT re-added to _approval_state; the user can
        retry by clicking /approve or /deny in the channel.
        """
        adapter = _make_adapter()
        adapter._approval_state["aid9"] = "sk9"
        adapter._lookup_username = AsyncMock(return_value="frank")

        request = _make_request({
            "user_id": "user_allowed",
            "post_id": "post9",
            "context": {"approval_id": "aid9", "choice": "once"},
        })

        with patch(
            "tools.approval.resolve_gateway_approval",
            side_effect=RuntimeError("queue error"),
        ):
            response = await adapter._handle_approval_action(request)

        assert response.status == 500
        # Token is consumed (not re-armed); the ephemeral message tells the
        # user to fall back to /approve or /deny.
        assert "aid9" not in adapter._approval_state

    @pytest.mark.asyncio
    async def test_wildcard_allowed_users(self):
        """MATTERMOST_ALLOWED_USERS=* accepts any user_id."""
        os.environ["MATTERMOST_ALLOWED_USERS"] = "*"
        adapter = _make_adapter()
        adapter._approval_state["aid10"] = "sk10"
        adapter._lookup_username = AsyncMock(return_value="anyone")

        request = _make_request({
            "user_id": "random_user",
            "post_id": "post10",
            "context": {"approval_id": "aid10", "choice": "deny"},
        })

        with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
            response = await adapter._handle_approval_action(request)

        mock_resolve.assert_called_once_with("sk10", "deny")
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_approval_id_resolves_specific_session(self):
        """Clicking the button for card B resolves session-B only, not session-A.

        This is the core regression test: with session_key embedded directly in
        the button, a click could pop the FIFO-oldest entry from the queue and
        accidentally resolve session-A when the button was shown for session-B.
        With approval_id binding the click is always targeted.
        """
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(side_effect=[
            {"id": "post_A"},
            {"id": "post_B"},
        ])
        adapter._lookup_username = AsyncMock(return_value="tester")

        # Render two separate approval cards.
        await adapter.send_exec_approval(
            chat_id="ch1", command="cmd_A", session_key="session-A",
        )
        await adapter.send_exec_approval(
            chat_id="ch1", command="cmd_B", session_key="session-B",
        )

        assert len(adapter._approval_state) == 2
        ids = list(adapter._approval_state.keys())
        # Find the approval_id for session-B specifically.
        aid_b = next(k for k, v in adapter._approval_state.items() if v == "session-B")
        aid_a = next(k for k, v in adapter._approval_state.items() if v == "session-A")

        # Simulate a click on the card shown for session-B.
        request = _make_request({
            "user_id": "user_allowed",
            "post_id": "post_B",
            "context": {"approval_id": aid_b, "choice": "once"},
        })

        with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
            response = await adapter._handle_approval_action(request)

        assert response.status == 200
        # Only session-B was resolved.
        mock_resolve.assert_called_once_with("session-B", "once")
        # session-A approval must still be pending.
        assert aid_a in adapter._approval_state
        assert adapter._approval_state[aid_a] == "session-A"
        # session-B approval must have been consumed.
        assert aid_b not in adapter._approval_state

    @pytest.mark.asyncio
    async def test_both_parallel_approvals_resolve_independently(self):
        """Clicking card A then card B resolves each session exactly once.

        Extends test_approval_id_resolves_specific_session: verifies the second
        click (after the first has already consumed its token) still resolves
        the correct session and does not find a stale/wrong entry.
        """
        adapter = _make_adapter()
        adapter._api_post = AsyncMock(side_effect=[
            {"id": "post_A"},
            {"id": "post_B"},
        ])
        adapter._lookup_username = AsyncMock(return_value="tester")

        await adapter.send_exec_approval(
            chat_id="ch1", command="cmd_A", session_key="session-A",
        )
        await adapter.send_exec_approval(
            chat_id="ch1", command="cmd_B", session_key="session-B",
        )

        aid_a = next(k for k, v in adapter._approval_state.items() if v == "session-A")
        aid_b = next(k for k, v in adapter._approval_state.items() if v == "session-B")

        resolved = []

        def fake_resolve(sk, choice):
            resolved.append((sk, choice))
            return 1

        with patch("tools.approval.resolve_gateway_approval", side_effect=fake_resolve):
            # Click card A first.
            await adapter._handle_approval_action(_make_request({
                "user_id": "user_allowed",
                "post_id": "post_A",
                "context": {"approval_id": aid_a, "choice": "once"},
            }))
            # Then click card B.
            await adapter._handle_approval_action(_make_request({
                "user_id": "user_allowed",
                "post_id": "post_B",
                "context": {"approval_id": aid_b, "choice": "deny"},
            }))

        assert resolved == [("session-A", "once"), ("session-B", "deny")]
        # Both tokens consumed; state must be empty.
        assert adapter._approval_state == {}


# ===========================================================================
# WebhookAdapter.register_extra_route
# ===========================================================================

class TestWebhookRegisterExtraRoute:
    """Tests for the register_extra_route helper on WebhookAdapter."""

    def _make_webhook(self):
        from gateway.platforms.webhook import WebhookAdapter
        cfg = PlatformConfig(enabled=True, token="")
        adapter = WebhookAdapter(cfg)
        return adapter

    def test_registers_post_handler(self):
        adapter = self._make_webhook()
        handler = AsyncMock()
        result = adapter.register_extra_route("POST", "/hermes-approval", handler)
        assert result is True
        assert adapter._extra_handlers["/hermes-approval"] is handler

    def test_dedup_registration(self):
        adapter = self._make_webhook()
        h1 = AsyncMock()
        h2 = AsyncMock()
        adapter.register_extra_route("POST", "/hermes-approval", h1)
        adapter.register_extra_route("POST", "/hermes-approval", h2)
        # Second call is a no-op; first handler wins
        assert adapter._extra_handlers["/hermes-approval"] is h1

    def test_unsupported_method_returns_false(self):
        adapter = self._make_webhook()
        result = adapter.register_extra_route("GET", "/hermes-approval", AsyncMock())
        assert result is False
        assert "/hermes-approval" not in adapter._extra_handlers


# ===========================================================================
# _ensure_callback_route — lazy retry / init-order race fix
# ===========================================================================

class TestEnsureCallbackRoute:
    """Tests for the init-order-safe _ensure_callback_route / _delayed_callback_route_register."""

    def setup_method(self):
        os.environ["MATTERMOST_CALLBACK_URL"] = "http://hermes.local:8644/hermes-approval"

    def teardown_method(self):
        os.environ.pop("MATTERMOST_CALLBACK_URL", None)

    def _make_bare_adapter(self) -> MattermostAdapter:
        """Adapter with no mocks beyond the minimum for unit testing."""
        config = PlatformConfig(enabled=True, token="tok")
        adapter = MattermostAdapter(config)
        adapter._base_url = "http://mm.local"
        return adapter

    def _mock_webhook_in_runner(self, adapter):
        """Inject a fake webhook adapter into a fake gateway runner."""
        from gateway.platforms.webhook import WebhookAdapter
        webhook_cfg = PlatformConfig(enabled=True, token="")
        webhook = WebhookAdapter(webhook_cfg)

        fake_runner = MagicMock()
        fake_runner.adapters = {Platform.WEBHOOK: webhook}

        fake_run_mod = MagicMock()
        fake_run_mod._gateway_runner_ref = MagicMock(return_value=fake_runner)
        return fake_run_mod, webhook

    def test_registers_when_webhook_present(self):
        """Route is registered when WebhookAdapter is already in runner.adapters."""
        adapter = self._make_bare_adapter()
        fake_run_mod, webhook = self._mock_webhook_in_runner(adapter)

        with patch.dict("sys.modules", {"gateway.run": fake_run_mod}):
            adapter._ensure_callback_route(log_on_fail=True)

        assert adapter._callback_route_registered is True
        assert "/hermes-approval" in webhook._extra_handlers

    def test_no_warning_when_log_on_fail_false(self):
        """log_on_fail=False suppresses the warning on first (race) attempt."""
        adapter = self._make_bare_adapter()

        fake_runner = MagicMock()
        fake_runner.adapters = {}  # webhook not connected yet
        fake_run_mod = MagicMock()
        fake_run_mod._gateway_runner_ref = MagicMock(return_value=fake_runner)

        with patch.dict("sys.modules", {"gateway.run": fake_run_mod}):
            with patch("gateway.platforms.mattermost.logger") as mock_log:
                adapter._ensure_callback_route(log_on_fail=False)

        mock_log.warning.assert_not_called()
        assert adapter._callback_route_registered is False

    def test_warning_when_log_on_fail_true(self):
        """log_on_fail=True emits the warning when webhook is absent."""
        adapter = self._make_bare_adapter()

        fake_runner = MagicMock()
        fake_runner.adapters = {}
        fake_run_mod = MagicMock()
        fake_run_mod._gateway_runner_ref = MagicMock(return_value=fake_runner)

        with patch.dict("sys.modules", {"gateway.run": fake_run_mod}):
            with patch("gateway.platforms.mattermost.logger") as mock_log:
                adapter._ensure_callback_route(log_on_fail=True)

        mock_log.warning.assert_called_once()
        assert "WEBHOOK_ENABLED" in mock_log.warning.call_args[0][0]

    def test_idempotent_when_already_registered(self):
        """Second call when already registered is a no-op (no double-register)."""
        adapter = self._make_bare_adapter()
        adapter._callback_route_registered = True
        fake_run_mod, webhook = self._mock_webhook_in_runner(adapter)

        with patch.dict("sys.modules", {"gateway.run": fake_run_mod}):
            adapter._ensure_callback_route(log_on_fail=True)
            adapter._ensure_callback_route(log_on_fail=True)

        # register_extra_route should not have been called at all
        assert webhook._extra_handlers == {}

    def test_no_op_when_callback_url_unset(self):
        """No webhook lookup when MATTERMOST_CALLBACK_URL is not set."""
        os.environ.pop("MATTERMOST_CALLBACK_URL", None)
        adapter = self._make_bare_adapter()
        fake_run_mod, webhook = self._mock_webhook_in_runner(adapter)

        with patch.dict("sys.modules", {"gateway.run": fake_run_mod}):
            adapter._ensure_callback_route(log_on_fail=True)

        assert adapter._callback_route_registered is False
        assert webhook._extra_handlers == {}

    @pytest.mark.asyncio
    async def test_delayed_register_retries_after_sleep(self):
        """_delayed_callback_route_register waits 3 s then calls _ensure_callback_route."""
        adapter = self._make_bare_adapter()

        call_log = []

        def fake_ensure(*, log_on_fail):
            call_log.append(log_on_fail)
            adapter._callback_route_registered = True

        adapter._ensure_callback_route = fake_ensure

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await adapter._delayed_callback_route_register()

        mock_sleep.assert_awaited_once_with(3.0)
        assert call_log == [True]
        assert adapter._callback_route_registered is True
