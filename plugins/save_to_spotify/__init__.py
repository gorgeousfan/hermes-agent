"""Bundled Save to Spotify integration.

Registers CLI-backed tools that save personal audio content to Spotify via the
official ``save-to-spotify`` binary. This is intentionally separate from the
existing Spotify playback/search plugin.
"""

from __future__ import annotations

from plugins.save_to_spotify.tools import (
    SAVE_TO_SPOTIFY_EPISODES_SCHEMA,
    SAVE_TO_SPOTIFY_SHOWS_SCHEMA,
    SAVE_TO_SPOTIFY_TIMELINE_SCHEMA,
    SAVE_TO_SPOTIFY_UPLOAD_SCHEMA,
    handle_save_to_spotify_episodes,
    handle_save_to_spotify_shows,
    handle_save_to_spotify_timeline,
    handle_save_to_spotify_upload,
)

_TOOLS = (
    ("save_to_spotify_upload", SAVE_TO_SPOTIFY_UPLOAD_SCHEMA, handle_save_to_spotify_upload, "📤"),
    ("save_to_spotify_shows", SAVE_TO_SPOTIFY_SHOWS_SCHEMA, handle_save_to_spotify_shows, "🎙️"),
    ("save_to_spotify_episodes", SAVE_TO_SPOTIFY_EPISODES_SCHEMA, handle_save_to_spotify_episodes, "🎧"),
    ("save_to_spotify_timeline", SAVE_TO_SPOTIFY_TIMELINE_SCHEMA, handle_save_to_spotify_timeline, "⏱️"),
)


def register(ctx) -> None:
    """Register Save to Spotify tools."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="save_to_spotify",
            schema=schema,
            handler=handler,
            emoji=emoji,
        )
