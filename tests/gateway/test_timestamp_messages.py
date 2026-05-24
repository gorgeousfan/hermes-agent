"""Tests for the per-message timestamp prefix feature.

When ``GatewayConfig.timestamp_messages`` is True, the gateway prepends each
inbound user message with an ISO-8601 timestamp (rendered in the host's
local timezone) before it enters the agent loop. This gives the model a
clock signal on every turn in long-running sessions where the system
prompt's "session start" timestamp is the only built-in clock.
"""

import re
from datetime import datetime, timezone, timedelta

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource


_ISO_PREFIX_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)\] "
)


def _make_runner(config: GatewayConfig) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = config
    runner.adapters = {}
    runner._model = "openai/gpt-4.1-mini"
    runner._base_url = None
    runner._last_inbound_message_ts = {}
    return runner


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="504464026",
        chat_name="Simon",
        chat_type="private",
        user_name="Simon",
    )


@pytest.mark.asyncio
async def test_timestamp_prefix_added_when_enabled():
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
        )
    )
    # Fixed UTC time so we can assert the rendered offset is well-formed.
    fixed_ts = datetime(2026, 5, 15, 18, 25, 0, tzinfo=timezone.utc)
    source = _make_source()
    event = MessageEvent(text="hello", source=source, timestamp=fixed_ts)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result is not None
    assert _ISO_PREFIX_RE.match(result), f"missing/malformed iso prefix: {result!r}"
    assert result.endswith(" hello")


@pytest.mark.asyncio
async def test_timestamp_prefix_skipped_when_disabled_by_default():
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            # timestamp_messages defaults to False
        )
    )
    source = _make_source()
    event = MessageEvent(
        text="hello",
        source=source,
        timestamp=datetime(2026, 5, 15, 18, 25, 0, tzinfo=timezone.utc),
    )

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == "hello"


@pytest.mark.asyncio
async def test_timestamp_prefix_preserves_numeric_offset():
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
        )
    )
    # Build a tz-aware datetime in a non-UTC zone (BST-equivalent +01:00) and
    # confirm the rendered prefix carries a numeric offset (could be either
    # the original or the host-local one — astimezone() converts to local).
    bst = timezone(timedelta(hours=1))
    event_ts = datetime(2026, 5, 15, 19, 25, 0, tzinfo=bst)
    source = _make_source()
    event = MessageEvent(text="hi", source=source, timestamp=event_ts)

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result is not None
    # Must contain a numeric offset (+HH:MM or -HH:MM); never a bare time.
    assert re.search(r"[+-]\d{2}:\d{2}\]", result), f"no numeric offset: {result!r}"
    assert result.endswith(" hi")


@pytest.mark.asyncio
async def test_timestamp_prefix_applies_before_sender_prefix_in_shared_group():
    """When both shared-group sender prefix and timestamp_messages are on,
    the timestamp wraps the already-prefixed message (one prefix per line)."""
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            group_sessions_per_user=False,
            timestamp_messages=True,
        )
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1002285219667",
        chat_name="Test Group",
        chat_type="group",
        user_name="Alice",
    )
    event = MessageEvent(
        text="hello",
        source=source,
        timestamp=datetime(2026, 5, 15, 18, 25, 0, tzinfo=timezone.utc),
    )

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result is not None
    assert _ISO_PREFIX_RE.match(result), f"missing iso prefix: {result!r}"
    # The sender prefix should still be present, after the timestamp.
    assert "[Alice] hello" in result


@pytest.mark.asyncio
async def test_timestamp_prefix_survives_malformed_timestamp():
    """A bad timestamp must never block delivery — it should silently no-op."""
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
        )
    )
    source = _make_source()
    # Provide an object that masquerades as a datetime but raises on astimezone.
    class _BadTs:
        tzinfo = None

        def astimezone(self, *args, **kwargs):
            raise RuntimeError("boom")

    event = MessageEvent(text="hello", source=source)
    event.timestamp = _BadTs()  # type: ignore[assignment]

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    # Original message comes through unchanged.
    assert result == "hello"


# -----------------------------------------------------------------------------
# message_timestamp_threshold_seconds — gap-gated prefix
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_message_always_gets_prefix_regardless_of_threshold():
    """No prior inbound timestamp = re-anchor scenario; prefix must apply."""
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
            message_timestamp_threshold_seconds=600,  # default-ish
        )
    )
    source = _make_source()
    event = MessageEvent(
        text="hello",
        source=source,
        timestamp=datetime(2026, 5, 18, 9, 0, 0, tzinfo=timezone.utc),
    )

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result is not None
    assert _ISO_PREFIX_RE.match(result), f"first message missing prefix: {result!r}"


@pytest.mark.asyncio
async def test_threshold_suppresses_prefix_within_window():
    """Rapid back-and-forth: second message within threshold gets no prefix."""
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
            message_timestamp_threshold_seconds=600,  # 10 min
        )
    )
    source = _make_source()
    t0 = datetime(2026, 5, 18, 9, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=30)  # well inside 10 min window

    # First message anchors the gap tracker and emits a prefix.
    first = await runner._prepare_inbound_message_text(
        event=MessageEvent(text="hello", source=source, timestamp=t0),
        source=source,
        history=[],
    )
    assert first is not None and _ISO_PREFIX_RE.match(first)

    # Second message arrives 30s later — well inside the 10 min window.
    second = await runner._prepare_inbound_message_text(
        event=MessageEvent(text="follow up", source=source, timestamp=t1),
        source=source,
        history=[],
    )
    assert second == "follow up", f"expected no prefix, got {second!r}"


@pytest.mark.asyncio
async def test_threshold_restores_prefix_after_gap():
    """Re-anchor after a longer-than-threshold gap."""
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
            message_timestamp_threshold_seconds=600,  # 10 min
        )
    )
    source = _make_source()
    t0 = datetime(2026, 5, 18, 9, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=900)  # 15 min later — past the 10 min window

    await runner._prepare_inbound_message_text(
        event=MessageEvent(text="hello", source=source, timestamp=t0),
        source=source,
        history=[],
    )

    second = await runner._prepare_inbound_message_text(
        event=MessageEvent(text="back", source=source, timestamp=t1),
        source=source,
        history=[],
    )
    assert second is not None
    assert _ISO_PREFIX_RE.match(second), f"expected re-anchor prefix, got {second!r}"


@pytest.mark.asyncio
async def test_threshold_zero_always_prefixes():
    """Threshold=0 preserves the original always-on behaviour."""
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
            message_timestamp_threshold_seconds=0,
        )
    )
    source = _make_source()
    t0 = datetime(2026, 5, 18, 9, 0, 0, tzinfo=timezone.utc)

    # Three back-to-back messages, all 1s apart — every one should be prefixed.
    for i in range(3):
        msg = await runner._prepare_inbound_message_text(
            event=MessageEvent(
                text=f"msg{i}",
                source=source,
                timestamp=t0 + timedelta(seconds=i),
            ),
            source=source,
            history=[],
        )
        assert msg is not None
        assert _ISO_PREFIX_RE.match(msg), f"msg{i} missing prefix at threshold=0: {msg!r}"


@pytest.mark.asyncio
async def test_threshold_tracks_per_session_independently():
    """Two sessions in the same runner must not share the gap tracker."""
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
            message_timestamp_threshold_seconds=600,
        )
    )
    source_a = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="111",
        chat_name="Alice",
        chat_type="private",
        user_name="Alice",
    )
    source_b = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="222",
        chat_name="Bob",
        chat_type="private",
        user_name="Bob",
    )
    t0 = datetime(2026, 5, 18, 9, 0, 0, tzinfo=timezone.utc)

    # Session A: anchor.
    await runner._prepare_inbound_message_text(
        event=MessageEvent(text="hi", source=source_a, timestamp=t0),
        source=source_a,
        history=[],
    )

    # Session B's first message: must still get a prefix (independent gap
    # tracker), even though session A just received one moments ago.
    msg_b = await runner._prepare_inbound_message_text(
        event=MessageEvent(
            text="hello",
            source=source_b,
            timestamp=t0 + timedelta(seconds=5),
        ),
        source=source_b,
        history=[],
    )
    assert msg_b is not None
    assert _ISO_PREFIX_RE.match(msg_b), f"session B should re-anchor: {msg_b!r}"


@pytest.mark.asyncio
async def test_threshold_updates_last_ts_even_when_prefix_suppressed():
    """Suppressed messages still bump the anchor: gap is rolling, not anchored
    to last *prefixed* message."""
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
            message_timestamp_threshold_seconds=600,  # 10 min
        )
    )
    source = _make_source()
    t0 = datetime(2026, 5, 18, 9, 0, 0, tzinfo=timezone.utc)

    # Three messages 5 min apart. Without rolling updates the third (10 min
    # after t0) would re-anchor; with rolling updates it stays inside the
    # window from the previous (suppressed) message.
    first = await runner._prepare_inbound_message_text(
        event=MessageEvent(text="m1", source=source, timestamp=t0),
        source=source,
        history=[],
    )
    second = await runner._prepare_inbound_message_text(
        event=MessageEvent(text="m2", source=source, timestamp=t0 + timedelta(seconds=300)),
        source=source,
        history=[],
    )
    third = await runner._prepare_inbound_message_text(
        event=MessageEvent(text="m3", source=source, timestamp=t0 + timedelta(seconds=600)),
        source=source,
        history=[],
    )

    assert first is not None and _ISO_PREFIX_RE.match(first)
    assert second == "m2"  # within 5min of m1 (≤600s)
    assert third == "m3"  # within 5min of m2 (≤600s), rolling


@pytest.mark.asyncio
async def test_threshold_handles_out_of_order_delivery_gracefully():
    """A message with a timestamp earlier than the previous one is treated as
    'within window' (negative gap) — never re-anchors retroactively."""
    runner = _make_runner(
        GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
            timestamp_messages=True,
            message_timestamp_threshold_seconds=600,
        )
    )
    source = _make_source()
    t_late = datetime(2026, 5, 18, 9, 5, 0, tzinfo=timezone.utc)
    t_early = datetime(2026, 5, 18, 9, 0, 0, tzinfo=timezone.utc)  # arrives second but timestamped earlier

    await runner._prepare_inbound_message_text(
        event=MessageEvent(text="later", source=source, timestamp=t_late),
        source=source,
        history=[],
    )
    out_of_order = await runner._prepare_inbound_message_text(
        event=MessageEvent(text="earlier", source=source, timestamp=t_early),
        source=source,
        history=[],
    )
    # Late-arriving older message must not trigger a misleading re-anchor.
    assert out_of_order == "earlier"
