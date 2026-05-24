"""Tests for the cron ticker watchdog used by the gateway."""

import threading
from unittest.mock import MagicMock

from gateway.run import _cron_ticker_watchdog_step


class FakeThread:
    def __init__(self, alive: bool):
        self._alive = alive

    def is_alive(self):
        return self._alive


class TestCronTickerWatchdogStep:
    def test_live_ticker_does_not_restart(self):
        stop_event = threading.Event()
        ticker_thread = FakeThread(True)
        ticker_state = {"thread": ticker_thread}
        restart_ticker = MagicMock()

        result = _cron_ticker_watchdog_step(stop_event, ticker_state, restart_ticker)

        assert result is True
        restart_ticker.assert_not_called()
        assert ticker_state["thread"] is ticker_thread

    def test_dead_ticker_is_restarted(self):
        stop_event = threading.Event()
        old_thread = FakeThread(False)
        new_thread = FakeThread(True)
        ticker_state = {"thread": old_thread}
        restart_ticker = MagicMock(return_value=new_thread)

        result = _cron_ticker_watchdog_step(stop_event, ticker_state, restart_ticker)

        assert result is True
        restart_ticker.assert_called_once()
        assert ticker_state["thread"] is new_thread

    def test_shutdown_event_stops_watchdog_step(self):
        stop_event = threading.Event()
        stop_event.set()
        ticker_state = {"thread": FakeThread(False)}
        restart_ticker = MagicMock()

        result = _cron_ticker_watchdog_step(stop_event, ticker_state, restart_ticker)

        assert result is False
        restart_ticker.assert_not_called()
