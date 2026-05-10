---
title: "Save to Spotify"
description: "Upload personal audio to Spotify from Hermes using Spotify's official save-to-spotify CLI."
---

# Save to Spotify

Hermes can upload your existing audio files to Spotify as **personal podcast** content using Spotify's official [`save-to-spotify`](https://github.com/spotify/save-to-spotify) CLI. This is separate from Hermes' regular Spotify playback/search toolset: `save_to_spotify` is for **publishing private audio into your library**, not controlling what's already playing.

## What it does

The `save_to_spotify` toolset exposes four model-visible tools:

- `save_to_spotify_upload`
- `save_to_spotify_shows`
- `save_to_spotify_episodes`
- `save_to_spotify_timeline`

Use them to:

- Upload a local `.mp3`, `.m4a`, `.wav`, or `.ogg` file as a Spotify episode
- Reuse or create shows to organize your personal audio
- Check whether an uploaded episode is `READY`
- Add timeline companions like chapters, links, and `spotify_entity` references after the episode is ready

## Important boundary

This toolset **does not generate audio**. Generate audio first with Hermes TTS or provide an existing local media file, then upload that file to Spotify.

## Setup

### 1. Install the official CLI

Hermes shells out to the official `save-to-spotify` binary in `--json` mode. Install it first:

```bash
curl -fsSL https://saveto.spotify.com/install.sh | bash
```

Or follow the manual install steps in the upstream project:

- https://github.com/spotify/save-to-spotify

### 2. Authenticate the CLI once

```bash
save-to-spotify auth login
```

To verify:

```bash
save-to-spotify auth status
```

### 3. Enable the Hermes toolset

```bash
hermes tools
```

Toggle on `🎙️ Save to Spotify` and save. Like `spotify`, this toolset is off by default and only appears to the model after you enable it.

## Chat-first usage

Examples:

```text
Turn this standup recap audio into a Spotify episode and save it under my existing Meeting Recaps show.
```

```text
Generate a morning briefing with TTS, upload it to Spotify, and wait until it's ready.
```

```text
Upload this lesson audio to Spotify, then add chapter markers once the episode is ready.
```

## Recommended workflow

1. Generate or locate the audio file first.
2. List shows and reuse one when possible.
3. Upload the episode.
4. Check readiness with `episodes status`.
5. Only after the episode is `READY`, set timeline items if the user asked for them.

## Tool behaviors

### `save_to_spotify_upload`

Uploads a local media file and creates an episode in one step.

Key inputs:

- `file_path`
- `title`
- optional `show_id`
- optional `new_show_title`
- optional `summary`
- optional `image_path`
- optional `language`
- optional `wait`
- optional `wait_timeout`

If `wait: true`, Hermes passes through the CLI's readiness wait behavior. With no `wait_timeout`, Hermes preserves the CLI default readiness timeout. With `wait_timeout`, Hermes forwards the custom duration.

### `save_to_spotify_shows`

Actions:

- `list`
- `get`
- `create`
- `delete`

### `save_to_spotify_episodes`

Actions:

- `list`
- `create`
- `status`
- `delete`

### `save_to_spotify_timeline`

Actions:

- `get`
- `set`
- `delete`

For `action: "set"`, Hermes accepts a structured timeline object and writes the temporary JSON file the CLI expects for `--from-file`.

## Timeline guidance

- Check `episodes status` before timeline writes.
- Only set timeline items after the episode is `READY`.
- Prefer full `spotify:...` URIs for `spotify_entity`.
- Do not invent chapters, links, or companion items unless the user asked for them.

Example timeline payload:

```json
{
  "items": [
    { "chapter": { "title": "Introduction", "start_time_ms": 0 } },
    { "spotify_entity": { "start_time_ms": 60000, "uri": "spotify:track:abc123" } },
    { "link": { "start_time_ms": 90000, "duration_ms": 15000, "url": "https://example.com/slides" } }
  ]
}
```

## Failure modes

- If the `save-to-spotify` binary is missing, Hermes returns a clear install error.
- If the CLI is not authenticated, Hermes returns a clear message telling you to run `save-to-spotify auth login`.
- If the CLI hangs unexpectedly, Hermes returns a system-timeout error distinct from the CLI's normal readiness timeout handling.

## Relationship to the existing Spotify toolset

These are intentionally separate:

- `spotify`: playback, queue, playlists, albums, library
- `save_to_spotify`: upload personal audio, manage shows/episodes, set timelines

Hermes does not rename, merge, or replace the existing Spotify plugin when this toolset is enabled.
