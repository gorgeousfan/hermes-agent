"""Voice response derivation pipeline for Hermes gateway voice-out events.

This module owns the small interface between gateway turn orchestration and the
Pulse/Aegis voice event writer. It is intentionally behavior-preserving: no
turn-start acknowledgements, no generated progress narration, and no alternate
transport. Successful turns derive at most one speakable final event from the
assistant final response; legacy streaming deltas remain silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping


VoiceKind = Literal["ack", "completion", "error", "question", "progress"]
VoiceSource = Literal["assistant_final", "assistant_commentary", "legacy_event"]


@dataclass(frozen=True)
class VoiceContext:
    """Safe transport metadata for a voice event candidate.

    Keep this deliberately narrow: no raw user text, no final assistant text, no
    paths, no stack traces. The low-level publisher still applies its own
    metadata allowlist before writing JSONL.
    """

    session_id: str | None = None
    platform: str | None = None
    chat_id: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None
    source_message_id: str | None = None
    voice_profile: str = "eon"
    room_context: str = "living_room"
    input_modality: str | None = None
    output_device: str | None = None
    config_scope: str | None = None
    explicit_spoken_request: bool | None = None
    is_private_context: bool | None = None
    max_seconds: int | None = None

    @classmethod
    def from_metadata(cls, **metadata: Any) -> "VoiceContext":
        return cls(
            session_id=metadata.get("session_id"),
            platform=metadata.get("platform"),
            chat_id=metadata.get("chat_id"),
            channel_id=metadata.get("channel_id"),
            thread_id=metadata.get("thread_id"),
            source_message_id=metadata.get("source_message_id"),
            voice_profile=metadata.get("voice_profile") or "eon",
            room_context=metadata.get("room_context") or "living_room",
            input_modality=metadata.get("input_modality"),
            output_device=metadata.get("output_device"),
            config_scope=metadata.get("config_scope"),
            explicit_spoken_request=metadata.get("explicit_spoken_request"),
            is_private_context=metadata.get("is_private_context"),
            max_seconds=metadata.get("max_seconds"),
        )

    def to_metadata(self) -> dict[str, Any]:
        metadata = {
            "session_id": self.session_id,
            "platform": self.platform,
            "chat_id": self.chat_id,
            "channel_id": self.channel_id,
            "thread_id": self.thread_id,
            "source_message_id": self.source_message_id,
            "voice_profile": self.voice_profile,
            "room_context": self.room_context,
            "input_modality": self.input_modality,
            "output_device": self.output_device,
            "config_scope": self.config_scope,
            "explicit_spoken_request": self.explicit_spoken_request,
            "is_private_context": self.is_private_context,
            "max_seconds": self.max_seconds,
        }
        return {key: value for key, value in metadata.items() if value is not None}


@dataclass(frozen=True)
class VoiceEvent:
    """Sanitized voice event candidate ready for best-effort publication."""

    kind: VoiceKind
    text: str
    source: VoiceSource
    derived_from: str
    context: VoiceContext = field(default_factory=VoiceContext)
    max_seconds: int | None = None
    policy: Mapping[str, Any] = field(default_factory=dict)
    summarizer: Mapping[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_publish_kwargs(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            **self.context.to_metadata(),
            "source": self.source,
            "derived_from": self.derived_from,
            "policy": dict(self.policy),
            "summarizer": dict(self.summarizer),
            **dict(self.extra),
        }
        if self.max_seconds is not None:
            payload["max_seconds"] = self.max_seconds
        return {key: value for key, value in payload.items() if value is not None}


class VoiceResponsePipeline:
    """Derive and publish room-safe voice events for Hermes gateway turns."""

    def __init__(self, *, publisher: Callable[..., None] | None = None) -> None:
        if publisher is None:
            from gateway import pulse_voice_events

            publisher = pulse_voice_events.publish_voice_out
        self.publisher = publisher

    def event_from_final_response(
        self,
        final_response: str,
        context: VoiceContext,
        *,
        summarizer: Callable[[str], str] | None = None,
    ) -> VoiceEvent:
        """Return the final event candidate; empty text becomes suppressed telemetry downstream."""
        from gateway.pulse_voice_events import summarize_final_voice_response

        result = summarize_final_voice_response(final_response, summarizer=summarizer)
        return VoiceEvent(
            kind=result.kind,  # type: ignore[arg-type]
            text=result.text,
            source="assistant_final",
            derived_from="final_response",
            context=context,
            policy=result.policy,
            summarizer=result.summarizer,
            extra={"voice_profile": result.voice_profile},
        )

    def event_from_legacy_event(
        self,
        kind: str,
        text: str,
        context: VoiceContext,
    ) -> VoiceEvent | None:
        """Map legacy gateway voice hooks into the current final-only baseline.

        Streaming deltas are silent. Commentary remains a non-speakable
        ``progress`` event for compatibility with the current JSONL contract; the
        Aegis consumer rejects progress for speech.
        """
        from gateway.pulse_voice_events import sanitize_voice_text

        legacy_kind = str(kind or "").strip().lower()
        if legacy_kind == "delta":
            return None
        if legacy_kind == "commentary":
            mapped_kind: VoiceKind = "progress"
            source: VoiceSource = "assistant_commentary"
            derived_from = "commentary"
        elif legacy_kind in {"ack", "completion", "error", "question", "progress"}:
            mapped_kind = legacy_kind  # type: ignore[assignment]
            source = "legacy_event"
            derived_from = legacy_kind
        else:
            return None

        sanitized = sanitize_voice_text(text)
        if not str(sanitized.text or "").strip():
            return None
        return VoiceEvent(
            kind=mapped_kind,
            text=sanitized.text,
            source=source,
            derived_from=derived_from,
            context=context,
            policy=sanitized.policy,
        )

    def publish(self, event: VoiceEvent | None) -> None:
        """Publish a voice event best-effort; never let voice break text delivery."""
        if event is None:
            return
        try:
            self.publisher(event.kind, event.text, **event.to_publish_kwargs())
        except Exception:
            return

    def publish_final_response(
        self,
        final_response: str,
        context: VoiceContext,
        *,
        summarizer: Callable[[str], str] | None = None,
    ) -> None:
        self.publish(self.event_from_final_response(final_response, context, summarizer=summarizer))

    def publish_legacy_event(self, kind: str, text: str, context: VoiceContext) -> None:
        self.publish(self.event_from_legacy_event(kind, text, context))
