"""Smoke tests for gateway /busy command dispatch and persistence."""

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_runner(busy_mode="interrupt", suppress_ack=False):
    """Create a GatewayRunner with known busy mode."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.session_store = None
    runner._busy_input_mode = busy_mode
    runner._suppress_busy_ack = suppress_ack
    return runner


def _make_event(text: str, chat_id: str = "chat-test") -> MessageEvent:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id=f"user-{chat_id}",
        chat_id=chat_id,
        user_name="tester",
        chat_type="dm",
    )
    return MessageEvent(text=text, source=source)


class TestBusyCommandStatus:
    """Test /busy status display — no config persistence needed."""

    @pytest.mark.asyncio
    async def test_status_returns_current_mode(self):
        """/busy status shows the current busy mode."""
        runner = _make_runner(busy_mode="queue")
        event = _make_event("/busy status")
        result = await runner._handle_busy_command(event)
        assert "queue" in str(result).lower()

    @pytest.mark.asyncio
    async def test_bare_busy_shows_status(self):
        """/busy without arguments shows status."""
        runner = _make_runner(busy_mode="steer")
        event = _make_event("/busy")
        result = await runner._handle_busy_command(event)
        assert "steer" in str(result).lower()

    @pytest.mark.asyncio
    async def test_busy_question_mark_shows_status(self):
        """/busy ? shows status."""
        runner = _make_runner(busy_mode="interrupt")
        event = _make_event("/busy ?")
        result = await runner._handle_busy_command(event)
        assert "interrupt" in str(result).lower()

    @pytest.mark.asyncio
    async def test_unknown_mode_shows_usage(self):
        """/busy with invalid arg returns error."""
        runner = _make_runner()
        event = _make_event("/busy bananas")
        result = await runner._handle_busy_command(event)
        assert "unknown" in str(result).lower()

    @pytest.mark.asyncio
    async def test_status_response_includes_mode_and_behavior(self):
        """Status response mentions the mode and behavior description."""
        runner = _make_runner(busy_mode="queue")
        event = _make_event("/busy status")
        result = await runner._handle_busy_command(event)
        reply_text = str(result)
        assert "queue" in reply_text.lower()
        assert "queues" in reply_text.lower() or "busy" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_status_shows_ack_state(self):
        """Status response includes the ack banner state."""
        runner = _make_runner(busy_mode="queue", suppress_ack=True)
        event = _make_event("/busy status")
        result = await runner._handle_busy_command(event)
        reply_text = str(result)
        assert "ack" in reply_text.lower()
        assert "off" in reply_text.lower()


class TestBusyCommandSetMode:
    """Test /busy mode switching with mocked persistence."""

    @pytest.fixture(autouse=True)
    def _mock_persistence(self, monkeypatch):
        """Mock config load so tests don't need a real config.yaml."""
        monkeypatch.setattr(
            gateway_run, "_load_gateway_config",
            lambda: {"display": {"busy_input_mode": "interrupt"}},
        )
        monkeypatch.setattr(gateway_run, "atomic_yaml_write", lambda _p, _c: None)
        yield

    @pytest.mark.asyncio
    async def test_busy_queue_subcommand(self):
        """/busy queue sets mode to queue."""
        runner = _make_runner(busy_mode="interrupt")
        event = _make_event("/busy queue")
        result = await runner._handle_busy_command(event)
        assert "set to" in str(result).lower()
        assert "queue" in str(result).lower()
        assert runner._busy_input_mode == "queue"

    @pytest.mark.asyncio
    async def test_busy_steer_subcommand(self):
        """/busy steer sets mode to steer."""
        runner = _make_runner(busy_mode="queue")
        event = _make_event("/busy steer")
        result = await runner._handle_busy_command(event)
        assert "set to" in str(result).lower()
        assert "steer" in str(result).lower()
        assert runner._busy_input_mode == "steer"

    @pytest.mark.asyncio
    async def test_busy_interrupt_subcommand(self):
        """/busy interrupt sets mode to interrupt."""
        runner = _make_runner(busy_mode="steer")
        event = _make_event("/busy interrupt")
        result = await runner._handle_busy_command(event)
        assert "set to" in str(result).lower()
        assert "interrupt" in str(result).lower()
        assert runner._busy_input_mode == "interrupt"


class TestBusyCommandAck:
    """Test /busy ack subcommand with mocked persistence."""

    @pytest.fixture(autouse=True)
    def _mock_persistence(self, monkeypatch):
        monkeypatch.setattr(
            gateway_run, "_load_gateway_config",
            lambda: {"display": {}},
        )
        monkeypatch.setattr(gateway_run, "atomic_yaml_write", lambda _p, _c: None)
        yield

    @pytest.mark.asyncio
    async def test_ack_shows_state(self):
        """/busy ack shows current ack state."""
        runner = _make_runner(suppress_ack=True)
        event = _make_event("/busy ack")
        result = await runner._handle_busy_command(event)
        reply = str(result).lower()
        assert "off" in reply or "suppressed" in reply

    @pytest.mark.asyncio
    async def test_ack_on_enables_banners(self):
        """/busy ack on enables banners."""
        runner = _make_runner(suppress_ack=True)
        event = _make_event("/busy ack on")
        result = await runner._handle_busy_command(event)
        assert "enabled" in str(result).lower()
        assert runner._suppress_busy_ack is False

    @pytest.mark.asyncio
    async def test_ack_off_suppresses_banners(self):
        """/busy ack off suppresses banners."""
        runner = _make_runner(suppress_ack=False)
        event = _make_event("/busy ack off")
        result = await runner._handle_busy_command(event)
        assert "suppressed" in str(result).lower()
        assert runner._suppress_busy_ack is True

    @pytest.mark.asyncio
    async def test_ack_status_shows_state(self):
        """/busy ack status shows state."""
        runner = _make_runner(suppress_ack=False)
        event = _make_event("/busy ack status")
        result = await runner._handle_busy_command(event)
        reply = str(result).lower()
        assert "on" in reply or "enabled" in reply

    @pytest.mark.asyncio
    async def test_ack_unknown_arg(self):
        """/busy ack with invalid arg returns error."""
        runner = _make_runner()
        event = _make_event("/busy ack maybe")
        result = await runner._handle_busy_command(event)
        assert "unknown" in str(result).lower()


class TestBusyCommandPersistence:
    """Test that persistence layer is actually called."""

    @pytest.fixture(autouse=True)
    def _mock_persistence(self, monkeypatch):
        monkeypatch.setattr(
            gateway_run, "_load_gateway_config",
            lambda: {"display": {"busy_input_mode": "interrupt"}},
        )
        monkeypatch.setattr(gateway_run, "atomic_yaml_write", lambda _p, _c: None)
        yield

    @pytest.mark.asyncio
    async def test_persistence_called_for_mode(self, monkeypatch):
        """Verify atomic_yaml_write is called with correct mode."""
        written = {}
        def _fake_write(path, config):
            written["path"] = path
            written["config"] = config

        monkeypatch.setattr(gateway_run, "atomic_yaml_write", _fake_write)
        runner = _make_runner(busy_mode="interrupt")
        event = _make_event("/busy queue")
        await runner._handle_busy_command(event)

        assert written, "atomic_yaml_write was never called"
        assert written["config"]["display"]["busy_input_mode"] == "queue"

    @pytest.mark.asyncio
    async def test_persistence_failure_falls_back_to_session_only(self, monkeypatch):
        """When write fails, mode still updates in-memory but response says session-only."""
        def _fail_write(_p, _c):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(gateway_run, "atomic_yaml_write", _fail_write)
        runner = _make_runner(busy_mode="interrupt")
        event = _make_event("/busy queue")
        result = await runner._handle_busy_command(event)
        assert runner._busy_input_mode == "queue"
        assert "session only" in str(result).lower()

    @pytest.mark.asyncio
    async def test_persistence_called_for_ack(self, monkeypatch):
        """Verify atomic_yaml_write is called with correct ack state."""
        written = {}
        def _fake_write(path, config):
            written["path"] = path
            written["config"] = config

        monkeypatch.setattr(gateway_run, "atomic_yaml_write", _fake_write)
        runner = _make_runner(suppress_ack=True)
        event = _make_event("/busy ack on")
        await runner._handle_busy_command(event)

        assert written, "atomic_yaml_write was never called for ack"
        assert written["config"]["display"]["suppress_busy_ack"] is False

    @pytest.mark.asyncio
    async def test_ack_persistence_failure_falls_back_to_session(self, monkeypatch):
        """When ack write fails, mode still updates in-memory."""
        def _fail_write(_p, _c):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(gateway_run, "atomic_yaml_write", _fail_write)
        runner = _make_runner(suppress_ack=True)
        event = _make_event("/busy ack on")
        result = await runner._handle_busy_command(event)
        assert runner._suppress_busy_ack is False  # still updated in-memory
        assert "session only" in str(result).lower()
