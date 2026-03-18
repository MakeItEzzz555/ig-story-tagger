# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Single-script Python tool that reposts an Instagram post as a Story with up to 20 invisible mention tags. Uses the `instagrapi` library (Instagram Private API) to log in, fetch post media, resolve usernames, and upload stories.

## Running

```bash
# Activate virtualenv (Python 3.12)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run
python ig_story_tagger.py
```

Requires `ffmpeg` on PATH for video posts (falls back to raw upload without it).

## Configuration

Copy `config.example.json` to `config.json` and fill in credentials, post shortcode, and tagged usernames. `config.json` contains real credentials and is gitignored — never commit it.

## Architecture

Everything lives in `ig_story_tagger.py` with a linear `main()` flow:

1. **`load_config()`** — reads and validates `config.json`
2. **`login()`** — restores session from `sessions/<username>.json` or does fresh login (handles 2FA, challenges, rate limits)
3. **`fetch_post_media()`** — downloads photo/video/album from a post shortcode; calls `resize_for_story()` (images) or `pad_video_for_story()` (videos via ffmpeg)
4. **`resolve_users()`** — looks up each username via API with randomized delays to avoid rate limits
5. **`confirm()`** — interactive y/N prompt before uploading
6. **`upload_story()`** — posts via `photo_upload_to_story` or `video_upload_to_story` with invisible `StoryMention` objects positioned at (0.99, 0.99) with 1% size

## Key Details

- Mentions are placed at bottom-right corner at 1% size to be invisible (`MENTION_X/Y = 0.99`, `MENTION_WIDTH/HEIGHT = 0.01`)
- Images are resized/padded to 1080x1920 (9:16) on a black canvas; videos are processed with ffmpeg to the same dimensions, capped at 60 seconds
- Sessions are persisted as JSON in `sessions/` to avoid repeated logins
- `temp/` holds downloaded/processed media and is cleaned up after upload
- Max 20 mentions per story (Instagram limit); excess usernames are silently truncated
