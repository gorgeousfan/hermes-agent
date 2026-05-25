from gateway.voice_response_pipeline import VoiceContext, VoiceEvent, VoiceResponsePipeline


def test_voice_context_round_trips_safe_metadata():
    context = VoiceContext.from_metadata(
        session_id="sess-1",
        platform="discord",
        chat_id="channel-1",
        channel_id="discord-channel-1",
        thread_id="thread-1",
        source_message_id="message-1",
        voice_profile="eon",
        input_modality="voice",
        output_device="discord_voice",
        config_scope="discord_voice",
        explicit_spoken_request=True,
        is_private_context=False,
        max_seconds=7,
    )

    assert context.to_metadata() == {
        "session_id": "sess-1",
        "platform": "discord",
        "chat_id": "channel-1",
        "channel_id": "discord-channel-1",
        "thread_id": "thread-1",
        "source_message_id": "message-1",
        "voice_profile": "eon",
        "room_context": "living_room",
        "input_modality": "voice",
        "output_device": "discord_voice",
        "config_scope": "discord_voice",
        "explicit_spoken_request": True,
        "is_private_context": False,
        "max_seconds": 7,
    }


def test_final_response_event_is_derived_from_assistant_final(monkeypatch):
    monkeypatch.setattr(
        "gateway.pulse_voice_events._final_summary_config",
        lambda: {"mode": "deterministic", "timeout_ms": 1000, "max_spoken_chars": 180, "voice_profile": "eon"},
    )
    pipeline = VoiceResponsePipeline(publisher=lambda *args, **kwargs: None)
    context = VoiceContext(session_id="sess-final", platform="telegram")

    event = pipeline.event_from_final_response(
        "I updated the voice bridge and ran the focused tests. Extra details follow.",
        context,
    )

    assert event is not None
    assert event.kind == "completion"
    assert event.text == "I updated the voice bridge and ran the focused tests."
    assert event.source == "assistant_final"
    assert event.derived_from == "final_response"
    assert event.context == context


def test_delta_event_is_ignored():
    pipeline = VoiceResponsePipeline(publisher=lambda *args, **kwargs: None)

    assert pipeline.event_from_legacy_event(
        "delta",
        "raw token that must not be spoken",
        VoiceContext(session_id="sess-delta"),
    ) is None


def test_commentary_maps_to_progress_without_becoming_speakable_ack():
    pipeline = VoiceResponsePipeline(publisher=lambda *args, **kwargs: None)

    event = pipeline.event_from_legacy_event(
        "commentary",
        "I’ll inspect that now. More detail follows.",
        VoiceContext(session_id="sess-progress"),
    )

    assert event is not None
    assert event.kind == "progress"
    assert event.text == "I’ll inspect that now."
    assert event.source == "assistant_commentary"
    assert event.derived_from == "commentary"


def test_publish_drops_none_and_swallows_publisher_failure():
    calls = []

    def failing_publisher(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("publisher failed")

    pipeline = VoiceResponsePipeline(publisher=failing_publisher)
    pipeline.publish(None)
    pipeline.publish(
        VoiceEvent(
            kind="completion",
            text="Done.",
            source="assistant_final",
            derived_from="final_response",
            context=VoiceContext(session_id="sess-safe"),
        )
    )

    assert len(calls) == 1


def test_pipeline_does_not_generate_turn_start_ack():
    pipeline = VoiceResponsePipeline(publisher=lambda *args, **kwargs: None)

    assert not hasattr(pipeline, "turn_ack_text")
    assert pipeline.event_from_legacy_event("turn_start", "", VoiceContext()) is None


def test_final_response_with_no_safe_speech_returns_suppressed_candidate(monkeypatch):
    monkeypatch.setattr(
        "gateway.pulse_voice_events._final_summary_config",
        lambda: {"mode": "deterministic", "timeout_ms": 1000, "max_spoken_chars": 180, "voice_profile": "eon"},
    )
    pipeline = VoiceResponsePipeline(publisher=lambda *args, **kwargs: None)

    event = pipeline.event_from_final_response(
        "MEDIA:/tmp/report.pdf\n```python\nprint('nothing safe')\n```",
        VoiceContext(session_id="sess-empty"),
    )

    assert event is not None
    assert event.text == ""
    assert event.policy["allowed"] is False
    assert event.policy["suppressed"] is True


def test_event_publish_kwargs_do_not_include_raw_source_text(monkeypatch):
    monkeypatch.setattr(
        "gateway.pulse_voice_events._final_summary_config",
        lambda: {"mode": "deterministic", "timeout_ms": 1000, "max_spoken_chars": 180, "voice_profile": "eon"},
    )
    pipeline = VoiceResponsePipeline(publisher=lambda *args, **kwargs: None)
    event = pipeline.event_from_final_response(
        "I checked /Users/brenno/private/file.py and finished.",
        VoiceContext(session_id="sess-metadata"),
    )

    payload = event.to_publish_kwargs() if event is not None else {}
    assert "final_response" not in payload
    assert "/Users/brenno" not in repr(payload)


def test_compatibility_wrappers_preserve_policy_metadata(monkeypatch):
    monkeypatch.setattr(
        "gateway.pulse_voice_events._final_summary_config",
        lambda: {"mode": "deterministic", "timeout_ms": 1000, "max_spoken_chars": 180, "voice_profile": "eon"},
    )
    calls = []

    def publisher(kind, text, **kwargs):
        calls.append((kind, text, kwargs))

    pipeline = VoiceResponsePipeline(publisher=publisher)
    context = VoiceContext.from_metadata(
        session_id="sess-policy",
        platform="discord",
        chat_id="chat-1",
        channel_id="channel-1",
        thread_id="thread-1",
        source_message_id="message-1",
        input_modality="voice",
        output_device="discord_voice",
        config_scope="discord_voice",
        explicit_spoken_request=True,
        is_private_context=False,
        max_seconds=7,
    )

    pipeline.publish_final_response("I finished the migration and tests passed.", context)

    assert len(calls) == 1
    _kind, _text, kwargs = calls[0]
    assert kwargs["session_id"] == "sess-policy"
    assert kwargs["platform"] == "discord"
    assert kwargs["chat_id"] == "chat-1"
    assert kwargs["channel_id"] == "channel-1"
    assert kwargs["thread_id"] == "thread-1"
    assert kwargs["source_message_id"] == "message-1"
    assert kwargs["input_modality"] == "voice"
    assert kwargs["output_device"] == "discord_voice"
    assert kwargs["config_scope"] == "discord_voice"
    assert kwargs["explicit_spoken_request"] is True
    assert kwargs["is_private_context"] is False
    assert kwargs["max_seconds"] == 7


def test_publish_forwards_empty_final_candidate_for_suppressed_observability():
    calls = []

    def publisher(*args, **kwargs):
        calls.append((args, kwargs))

    pipeline = VoiceResponsePipeline(publisher=publisher)
    pipeline.publish(
        VoiceEvent(
            kind="completion",
            text="",
            source="assistant_final",
            derived_from="final_response",
            context=VoiceContext(session_id="sess-suppressed"),
        )
    )

    assert len(calls) == 1
    assert calls[0][0] == ("completion", "")
    assert calls[0][1]["session_id"] == "sess-suppressed"
