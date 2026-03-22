# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Single-script Python tool that reposts an Instagram post as a Story with invisible mention tags. Supports unlimited users (batched into groups of 20, one story per batch) and optional background music from local files, YouTube, or Spotify. Uses the `instagrapi` library (Instagram Private API). Automated interactions may violate Instagram's ToS.

## Running

```bash
# Activate virtualenv (Python 3.12)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run
python ig_story_tagger.py
```

Requires `ffmpeg` on PATH for video posts and music features. Optional: `spotdl` for full Spotify track downloads.

## Configuration

Copy `config.example.json` to `config.json` and fill in credentials, post shortcode, and tagged usernames. `config.json` contains real credentials and is gitignored — never commit it.

Music fields (`music_source`, `music_volume`, `music_start`) are optional. `music_source` accepts a local file path, YouTube URL, or Spotify URL. Set to `null` to disable.

## Architecture

Everything lives in `ig_story_tagger.py` with a linear `main()` flow:

1. **`load_config()`** — reads and validates `config.json` (no user cap — unlimited tagged users)
2. **`login()`** — restores session from `sessions/<username>.json` or does fresh login (handles 2FA, challenges, rate limits)
3. **`fetch_post_media()`** — downloads photo/video/album from a post shortcode; calls `resize_for_story()` (images) or `pad_video_for_story()` (videos via ffmpeg)
4. **Music processing** (if `music_source` is set):
   - `resolve_music()` — detects source type (local/YouTube/Spotify) and downloads audio via `yt-dlp` or `spotdl`
   - `photo_to_video_with_audio()` — converts photo + audio into a 15s video story
   - `merge_audio_to_video()` — mixes music into existing video (preserves original audio)
5. **`resolve_users()`** — looks up each username via API with randomized delays
6. **`chunk_users()`** — splits resolved users into batches of 20
7. **Batch prompt** — asks how many stories to post (1-N or 'all')
8. **`confirm_batch()`** / **`upload_story()`** — confirms and uploads each batch with 30-60s delays between stories

## Key Details

- Mentions are placed at bottom-right corner at 1% size to be invisible (`MENTION_X/Y = 0.99`, `MENTION_WIDTH/HEIGHT = 0.01`)
- Images are resized/padded to 1080x1920 (9:16) on a black canvas; videos are processed with ffmpeg to the same dimensions, capped at 60 seconds
- Sessions are persisted as JSON in `sessions/` to avoid repeated logins
- `temp/` holds downloaded/processed media and is cleaned up after upload
- Max 20 mentions per story (Instagram limit); users are auto-batched across multiple stories
- Music is embedded as audio in the video — it does NOT appear as an Instagram music sticker (no song name, artist, or lyrics overlay)
- Photo + music automatically becomes a video story (15s duration)
