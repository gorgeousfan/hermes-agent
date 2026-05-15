---
name: fal-media-pipeline
description: Create images, edits, and videos with FAL.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Creative, Media, FAL, Image, Video]
    requires_tools: [image_generate, image_edit, video_generate]
---

# FAL Media Pipeline Skill

Use this skill to build a FAL-backed media workflow from concept image to
edited start frame to video. It coordinates Hermes media tools; it does not
replace tool configuration, API keys, or user review of generated assets.

## When to Use

- The user asks for image-to-video, img2img, video edit, video extend, or a
  complete concept-to-video workflow.
- The user provides local or public reference images that need to become a
  FAL edit or video start frame.
- The user needs precise image correction before animation, such as a product
  edit, mask-guided change, text rendering fix, or identity-preserving edit.

## Prerequisites

- `image_generate`, `image_edit`, and `video_generate` must be available.
- Hermes Image Generation and Video Generation should be configured for FAL,
  either through direct FAL credentials or the managed gateway.
- Local files can be passed to `image_edit` and FAL `video_generate`; Hermes
  uploads them to FAL storage before calling the edit/video endpoint.

## How to Run

Invoke this workflow through the media tools, or validate it through the
`terminal` tool with a prompt such as:

```bash
hermes --toolsets image_gen,video_gen -q "Use the FAL media pipeline to create a product hero image, refine it, and animate it."
```

## Quick Reference

| Goal | Tool | Key arguments |
| --- | --- | --- |
| Create a first frame | `image_generate` | `prompt`, `aspect_ratio` |
| Edit a public image | `image_edit` | `prompt`, `image_url`, `model` |
| Edit local images | `image_edit` | `prompt`, `image_path` or `image_paths` |
| Mask-guided edit | `image_edit` | `model=openai/gpt-image-2/edit`, `mask_url` |
| Fast semantic edit | `image_edit` | `model=fal-ai/nano-banana-2/edit` |
| Animate a still | `video_generate` | `prompt`, `image_url` or local path, `duration`, `audio` |
| Use a final frame | `video_generate` | `image_url`, `end_image_url` |
| Edit a video | `video_generate` | `operation=edit`, `video_url` |
| Extend a video | `video_generate` | `operation=extend`, `video_url` |

## Procedure

1. Confirm the goal in one sentence: subject, format, aspect ratio, duration,
   audio intent, and final deliverable.
2. If no source frame exists, create one with `image_generate`.
3. Inspect the chosen frame. If composition, text, identity, or aspect ratio is
   wrong, refine it with `image_edit` before animation.
4. Prefer `openai/gpt-image-2/edit` when the edit needs precise typography,
   product fidelity, or a `mask_url`.
5. Prefer `fal-ai/nano-banana-2/edit` when the edit is semantic, fast, or uses
   several references.
6. Animate the final still with `video_generate` by passing the edited image URL
   as `image_url`.
7. For Seedance or Kling start-to-end transitions, pass the desired final frame
   as `end_image_url`.
8. For existing clips, call `video_generate` with `operation=edit` or
   `operation=extend` and pass `video_url`.
9. Return the image URLs, video URLs, model choices, and exact prompts so the
   user can iterate.

## Pitfalls

- Do not ask `video_generate` to repair visual defects that should be fixed
  first with `image_edit`.
- Do not skip the edit step when a start frame has bad framing, unreadable
  text, inconsistent character identity, or the wrong aspect ratio.
- FAL `video_generate` accepts local `image_url`, `video_url`, final-frame, and
  reference paths; Hermes uploads them before calling FAL. Do not use
  no-op `image_edit` as a file-hosting workaround.
- Some FAL families expose native audio without an `audio` toggle; do not
  assume every backend honors `audio=true`.
- Some video edit and extend endpoints are family-specific; if one family
  rejects an operation, choose a family that advertises that route.

## Verification

Run a dry validation through the `terminal` tool after configuration:

```bash
hermes --toolsets image_gen,video_gen -q "Create a square test image, edit it with Nano Banana 2, then describe the video prompt you would use without generating video."
```
