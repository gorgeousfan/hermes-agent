---
name: save-to-spotify
description: "Save local or Hermes-generated audio to Spotify as personal podcast episodes."
version: 1.0.0
author: Hermes Agent
license: MIT
prerequisites:
  tools: [save_to_spotify_upload, save_to_spotify_shows, save_to_spotify_episodes, save_to_spotify_timeline]
metadata:
  hermes:
    tags: [spotify, audio, podcast, upload, media]
    related_skills: [spotify]
---

# Save to Spotify

Upload audio to Spotify as personal podcast content. This toolset saves media you already have; it does not generate audio on its own.

## When to use this skill

The user wants to put a briefing, recap, lesson, memo, or other audio file into Spotify so they can listen from their normal library and devices.

## Core rules

- This capability uploads media; it does not generate audio.
- Use Hermes TTS or existing local media first, then upload the result.
- Think of it as: text/content -> TTS or recording -> Spotify upload.
- Prefer reusing an existing show before creating a new one.
- Check `save_to_spotify_episodes` with `action: "status"` before writing timeline items.
- Only set timeline items after the episode is `READY`.
- Prefer full `spotify:...` URIs for `spotify_entity`.
- Do not invent timeline items unless the user explicitly asked for them.
- If auth fails, tell the user to run `hermes auth save-to-spotify` and mention `--no-browser` for SSH/headless sessions.

## Typical workflow

1. If the user does not already have a file, generate audio first with Hermes TTS or use an existing local media file.
2. List shows and reuse one if it already fits the content.
3. Upload the file with a title and optional show choice.
4. If timeline/chapter content matters, check episode readiness first.
5. Only after the episode is `READY`, set timeline items if the user asked for them.

Good fits:

- Morning briefing
- Meeting summary
- Lesson recap
- Language practice
- Release notes narration
- Dev podcast episode
- Agent audio log
- Research summary
- Build update recap

## Minimal patterns

### Upload a file into an existing show

```
save_to_spotify_shows({"action": "list"})
save_to_spotify_upload({
  "file_path": "/path/to/briefing.mp3",
  "title": "Morning Briefing",
  "show_id": "spotify:show:abc123"
})
```

### Upload and wait until ready

```
save_to_spotify_upload({
  "file_path": "/path/to/recap.mp3",
  "title": "Weekly Recap",
  "show_id": "spotify:show:abc123",
  "wait": true
})
```

### Create a show only when reuse is not appropriate

```
save_to_spotify_upload({
  "file_path": "/path/to/lesson.mp3",
  "title": "Lesson 12",
  "new_show_title": "Spanish Practice"
})
```

### Timeline write after readiness

```
save_to_spotify_episodes({"action": "status", "episode_id": "spotify:episode:def456"})
save_to_spotify_timeline({
  "action": "set",
  "episode_id": "spotify:episode:def456",
  "timeline": {
    "items": [
      {"chapter": {"title": "Intro", "start_time_ms": 0}},
      {"spotify_entity": {"start_time_ms": 60000, "uri": "spotify:track:abc123"}}
    ]
  }
})
```

## What not to do

- Do not describe this as a Spotify playback or library-saving feature; it is an upload pipeline for personal podcast-style audio.
- Do not create a new show automatically if an existing one clearly matches and the user did not ask for separation.
- Do not set timeline data before the episode is `READY`.
- Do not pass bare IDs or web URLs for `spotify_entity` when a full `spotify:...` URI is available.
