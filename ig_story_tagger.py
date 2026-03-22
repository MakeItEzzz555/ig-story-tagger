#!/usr/bin/env python3
"""
Instagram Story Tagger
Post stories from an existing post's media with invisible mention tags (20 per story, unlimited users).
Optionally add background music from a local file, YouTube URL, or Spotify URL.
Uses instagrapi (Instagram Private API).
"""

import json
import re
import sys
import random
import shutil
import subprocess
import time
from pathlib import Path

import requests

from instagrapi import Client
from instagrapi.types import StoryMention, UserShort
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    ChallengeUnknownStep,
    TwoFactorRequired,
    LoginRequired,
    ReloginAttemptExceeded,
    RateLimitError,
    PleaseWaitFewMinutes,
)
from PIL import Image

# --- Constants ---
STORY_WIDTH = 1080
STORY_HEIGHT = 1920
SESSION_DIR = Path("sessions")
TEMP_DIR = Path("temp")
CONFIG_FILE = Path("config.json")
PHOTO_STORY_DURATION = 15  # seconds — Instagram's default photo story length

# Invisible mention positioning (bottom-right corner, 1% size)
MENTION_X = 0.99
MENTION_Y = 0.99
MENTION_WIDTH = 0.01
MENTION_HEIGHT = 0.01

# URL patterns for music source detection
YOUTUBE_PATTERN = re.compile(
    r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)'
)
SPOTIFY_PATTERN = re.compile(
    r'(https?://)?(open\.)?spotify\.com/(track|album|playlist)/'
)


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"Error: {CONFIG_FILE} not found.")
        print("Copy config.example.json to config.json and fill in your details.")
        sys.exit(1)

    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)

    required = ["username", "password", "target_post", "tagged_users"]
    for key in required:
        if key not in config:
            print(f"Error: Missing '{key}' in {CONFIG_FILE}")
            sys.exit(1)

    if not config["tagged_users"]:
        print("Error: 'tagged_users' list is empty.")
        sys.exit(1)

    # Validate music_source if provided
    music_source = config.get("music_source")
    if music_source:
        is_url = YOUTUBE_PATTERN.match(music_source) or SPOTIFY_PATTERN.match(music_source)
        if not is_url and not Path(music_source).exists():
            print(f"Error: Music file not found: {music_source}")
            sys.exit(1)

    # Defaults for music settings
    config.setdefault("music_source", None)
    config.setdefault("music_volume", 0.5)
    config.setdefault("music_start", 0)

    return config


def setup_dirs():
    SESSION_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)


def login(username: str, password: str, proxy: str = None) -> Client:
    cl = Client()
    cl.delay_range = [2, 4]

    if proxy:
        cl.set_proxy(proxy)

    session_file = SESSION_DIR / f"{username}.json"

    # Try restoring saved session first
    if session_file.exists():
        print(f"Restoring session for @{username}...")
        try:
            cl.load_settings(str(session_file))
            cl.login(username, password)
            cl.get_timeline_feed()
            print("Session restored.")
            return cl
        except (LoginRequired, Exception) as e:
            print(f"Session invalid ({type(e).__name__}). Fresh login needed.")

    # Fresh login
    print(f"Logging in as @{username}...")
    try:
        cl.login(username, password)
    except TwoFactorRequired:
        code = input("  Enter 2FA code: ").strip()
        cl.login(username, password, verification_code=code)
    except (ChallengeRequired, ChallengeUnknownStep):
        print("\nInstagram requires a security challenge.")
        print("Steps to fix:")
        print("  1. Open Instagram on your phone (@" + username + ")")
        print("  2. Complete any security prompt (email/SMS code, 'Was this you?')")
        print("  3. Also try logging in at instagram.com in a browser")
        print("  4. Wait 10-15 minutes, then re-run this script")
        sys.exit(1)
    except BadPassword:
        print("Error: Incorrect password.")
        sys.exit(1)
    except (RateLimitError, PleaseWaitFewMinutes):
        print("Error: Rate limited by Instagram. Wait a few minutes and try again.")
        sys.exit(1)

    cl.dump_settings(str(session_file))
    print("Login successful. Session saved.")
    return cl


def _download_and_pad_video(video_url: str, shortcode: str) -> Path:
    """Download a video and pad it to story dimensions."""
    print("  Downloading video...")
    resp = requests.get(video_url, timeout=120)
    resp.raise_for_status()
    raw_path = TEMP_DIR / f"{shortcode}_raw.mp4"
    raw_path.write_bytes(resp.content)
    print(f"  Downloaded: {len(resp.content) / 1024 / 1024:.1f} MB")
    story_path = pad_video_for_story(raw_path)
    print(f"  Ready: {story_path}")
    return story_path


def fetch_post_media(cl: Client, shortcode: str) -> tuple:
    """Returns (file_path, media_type) where media_type is 'photo' or 'video'."""
    print(f"\nFetching post: {shortcode}")

    media_pk = cl.media_pk_from_code(shortcode)
    media = cl.media_info(media_pk)

    type_names = {1: "Photo", 2: "Video/Reel", 8: "Album"}
    print(f"  Author: @{media.user.username}")
    print(f"  Type:   {type_names.get(media.media_type, 'Unknown')}")
    if media.caption_text:
        preview = media.caption_text[:80] + ("..." if len(media.caption_text) > 80 else "")
        print(f"  Caption: {preview}")

    # Video post (Reel or Video)
    if media.media_type == 2 and media.video_url:
        return _download_and_pad_video(str(media.video_url), shortcode), "video"

    # Album — check if first item is video
    if media.media_type == 8 and media.resources:
        first = media.resources[0]
        if first.video_url:
            print("  (Album - using first video)")
            return _download_and_pad_video(str(first.video_url), shortcode), "video"
        else:
            print("  (Album - using first image)")
            image_url = str(first.thumbnail_url)
    elif media.media_type == 1:  # Photo
        image_url = str(media.thumbnail_url)
    else:
        print(f"  Unsupported media type: {media.media_type}")
        sys.exit(1)

    # Download image
    print("  Downloading image...")
    resp = requests.get(image_url, timeout=30)
    resp.raise_for_status()
    raw_path = TEMP_DIR / f"{shortcode}_raw.jpg"
    raw_path.write_bytes(resp.content)

    # Resize to story dimensions
    story_path = resize_for_story(raw_path)
    print(f"  Ready: {story_path}")
    return story_path, "photo"


def resize_for_story(image_path: Path) -> Path:
    img = Image.open(image_path)

    if img.size == (STORY_WIDTH, STORY_HEIGHT):
        return image_path

    # Scale to fit within 1080x1920
    img_ratio = img.width / img.height
    story_ratio = STORY_WIDTH / STORY_HEIGHT

    if img_ratio > story_ratio:
        new_width = STORY_WIDTH
        new_height = int(STORY_WIDTH / img_ratio)
    else:
        new_height = STORY_HEIGHT
        new_width = int(STORY_HEIGHT * img_ratio)

    if img.mode == "P":
        img = img.convert("RGBA")
    img = img.resize((new_width, new_height), Image.LANCZOS)

    # Center on black canvas
    canvas = Image.new("RGB", (STORY_WIDTH, STORY_HEIGHT), (0, 0, 0))
    x_offset = (STORY_WIDTH - new_width) // 2
    y_offset = (STORY_HEIGHT - new_height) // 2
    if img.mode in ("RGBA", "LA"):
        canvas.paste(img, (x_offset, y_offset), mask=img.split()[-1])
    else:
        canvas.paste(img, (x_offset, y_offset))

    output = image_path.with_name(image_path.stem + "_story.jpg")
    canvas.save(output, "JPEG", quality=95)
    return output


def pad_video_for_story(video_path: Path) -> Path:
    """Pad video to 9:16 (1080x1920) with black bars, keeping original content intact."""
    if not shutil.which("ffmpeg"):
        print("  Warning: ffmpeg not found. Uploading video as-is.")
        return video_path

    output = video_path.with_name(video_path.stem + "_story.mp4")

    # Scale video to fit within 1080x1920, then pad with black bars to fill the frame
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", (
            f"scale={STORY_WIDTH}:{STORY_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={STORY_WIDTH}:{STORY_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-t", "60",
        str(output),
    ]

    print("  Processing video for story format...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ffmpeg error: {result.stderr[-200:]}")
        print("  Falling back to original video.")
        return video_path

    return output


# --- Music functions ---

def resolve_music(source: str) -> Path:
    """Download or locate the music file. Returns path to a local audio file."""
    if YOUTUBE_PATTERN.match(source):
        return _download_from_youtube(source)
    elif SPOTIFY_PATTERN.match(source):
        return _download_from_spotify(source)
    else:
        # Local file
        path = Path(source)
        if not path.exists():
            print(f"  Error: Music file not found: {source}")
            sys.exit(1)
        return path


def _find_downloaded_audio() -> Path:
    """Find the downloaded audio file in TEMP_DIR (mp3, m4a, webm, opus, ogg)."""
    for ext in ["mp3", "m4a", "webm", "opus", "ogg"]:
        path = TEMP_DIR / f"music.{ext}"
        if path.exists():
            return path
    print("  Error: Downloaded audio file not found.")
    sys.exit(1)


def _run_ytdlp(url: str, label: str, no_playlist: bool = False) -> Path:
    """Download audio via yt-dlp. Shared by YouTube and Spotify fallback."""
    if not shutil.which("yt-dlp"):
        print(f"  Error: yt-dlp is required for {label} URLs.")
        print("  Install it: pip install yt-dlp")
        sys.exit(1)

    cmd = [
        "yt-dlp", "-x",
        "--audio-format", "mp3",
        "--audio-quality", "192K",
        "-o", str(TEMP_DIR / "music.%(ext)s"),
        "--quiet", "--progress",
    ]
    if no_playlist:
        cmd.append("--no-playlist")
    cmd.append(url)

    print(f"  Downloading audio from {label}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  yt-dlp error: {result.stderr[-300:]}")
        print(f"  Failed to download audio from {label}.")
        sys.exit(1)

    music_path = _find_downloaded_audio()
    print(f"  Downloaded: {music_path.name}")
    return music_path


def _download_from_youtube(url: str) -> Path:
    """Download audio from YouTube using yt-dlp."""
    return _run_ytdlp(url, "YouTube", no_playlist=True)


def _resolve_spotify_track_name(url: str) -> str:
    """Get the track name from a Spotify URL via the oEmbed API (no auth needed)."""
    clean_url = url.split("?")[0]  # strip query params like ?si=...
    oembed_url = f"https://open.spotify.com/oembed?url={clean_url}"
    try:
        resp = requests.get(oembed_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("title", "")
    except Exception:
        return ""


def _download_from_spotify(url: str) -> Path:
    """Download audio from Spotify using spotdl, falling back to yt-dlp YouTube search."""
    # Try spotdl first (better Spotify support, gets full tracks)
    if shutil.which("spotdl"):
        result = _download_spotify_spotdl(url)
        if result:
            return result
        print("  spotdl failed, trying YouTube Music search fallback...")

    # Fallback: resolve track name from Spotify, then search YouTube Music via yt-dlp
    if shutil.which("yt-dlp"):
        track_name = _resolve_spotify_track_name(url)
        if track_name:
            print(f"  Searching YouTube Music for: {track_name}")
            return _run_ytdlp(f"ytsearch1:{track_name}", "YouTube Music search", no_playlist=True)
        else:
            print("  Could not resolve track name from Spotify URL.")

    print("  Error: Could not download from Spotify.")
    print("  Install spotdl (pip install spotdl) or check your internet connection.")
    sys.exit(1)


def _download_spotify_spotdl(url: str) -> Path | None:
    """Download from Spotify using spotdl. Returns None on failure (allows fallback)."""
    output_template = str(TEMP_DIR / "music.{output-ext}")
    cmd = [
        "spotdl", "download", url,
        "--output", output_template,
        "--format", "mp3",
        "--bitrate", "192k",
        "--simple-tui",
        "--headless",
    ]

    print("  Downloading audio from Spotify (spotdl)...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        print("  spotdl timed out (Spotify may be rate-limiting).")
        return None

    if result.returncode != 0:
        error_output = (result.stderr or result.stdout or "")[-300:]
        print(f"  spotdl error: {error_output}")
        return None

    # spotdl may name file as "Artist - Title.mp3" despite template
    music_path = TEMP_DIR / "music.mp3"
    if not music_path.exists():
        mp3_files = list(TEMP_DIR.glob("*.mp3"))
        if mp3_files:
            music_path = mp3_files[0]
        else:
            print("  spotdl produced no output file.")
            return None

    print(f"  Downloaded: {music_path.name}")
    return music_path


def _video_has_audio(video_path: Path) -> bool:
    """Check if a video file has an audio stream."""
    if not shutil.which("ffprobe"):
        return False
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and "audio" in result.stdout


def photo_to_video_with_audio(image_path: Path, audio_path: Path,
                               volume: float, start: int) -> Path:
    """Convert a still image + audio into a video story."""
    output = TEMP_DIR / "story_photo_music.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",                    # loop the image
        "-i", str(image_path),           # input image
        "-ss", str(start),              # start position in audio
        "-i", str(audio_path),           # input audio
        "-t", str(PHOTO_STORY_DURATION), # duration (15s for photo stories)
        "-vf", f"scale={STORY_WIDTH}:{STORY_HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={STORY_WIDTH}:{STORY_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black",
        "-af", f"volume={volume}",       # music volume
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",           # compatibility
        "-movflags", "+faststart",
        "-shortest",                     # end when shorter input ends
        str(output),
    ]

    print("  Converting photo + music to video story...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ffmpeg error: {result.stderr[-300:]}")
        print("  Failed to create video from photo + music.")
        sys.exit(1)

    print(f"  Created: {output.name} ({PHOTO_STORY_DURATION}s video)")
    return output


def merge_audio_to_video(video_path: Path, audio_path: Path,
                          volume: float, start: int) -> Path:
    """Merge music audio into an existing video."""
    output = TEMP_DIR / "story_video_music.mp4"
    has_audio = _video_has_audio(video_path)

    if has_audio:
        # Mix original audio with music
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),           # input video (with audio)
            "-ss", str(start),
            "-i", str(audio_path),           # input music
            "-filter_complex",
            f"[0:a]volume=1.0[orig];"
            f"[1:a]volume={volume}[music];"
            f"[orig][music]amix=inputs=2:duration=first:dropout_transition=2",
            "-c:v", "copy",                  # don't re-encode video
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-t", "60",                      # Instagram story limit
            str(output),
        ]
    else:
        # No original audio — just add the music track
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),           # input video (no audio)
            "-ss", str(start),
            "-i", str(audio_path),           # input music
            "-af", f"volume={volume}",
            "-c:v", "copy",                  # don't re-encode video
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",                     # end when video ends
            "-t", "60",
            str(output),
        ]

    print("  Merging music into video...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ffmpeg error: {result.stderr[-300:]}")
        print("  Failed to merge music. Uploading video without music.")
        return video_path

    print(f"  Merged: {output.name}")
    return output


# --- Core functions ---

def resolve_users(cl: Client, usernames: list) -> tuple:
    resolved = []
    failed = []
    total = len(usernames)

    print(f"\nResolving {total} users...")

    for i, raw in enumerate(usernames, 1):
        username = raw.lstrip("@").strip()
        try:
            user = cl.user_info_by_username(username)
            short = UserShort(
                pk=user.pk,
                username=user.username,
                full_name=user.full_name,
                profile_pic_url=user.profile_pic_url,
                is_private=user.is_private,
            )
            resolved.append(short)
            print(f"  [{i}/{total}] @{username} - OK")
        except Exception as e:
            print(f"  [{i}/{total}] @{username} - FAILED ({e})")
            failed.append(username)

        # Delay between lookups to avoid triggering challenges
        if i < total:
            time.sleep(random.uniform(1.0, 2.0))

    return resolved, failed


def chunk_users(users: list, size: int = 20) -> list:
    """Split users into batches of `size`."""
    return [users[i:i + size] for i in range(0, len(users), size)]


def build_mentions(users: list) -> list:
    return [
        StoryMention(
            user=user,
            x=MENTION_X,
            y=MENTION_Y,
            width=MENTION_WIDTH,
            height=MENTION_HEIGHT,
        )
        for user in users
    ]


def confirm_batch(shortcode: str, media_path: Path, media_type: str,
                   batch_users: list, batch_num: int, total_batches: int,
                   has_music: bool = False) -> bool:
    print()
    print("=" * 55)
    print(f"  STORY {batch_num}/{total_batches}")
    print("=" * 55)
    print(f"  Post:  https://instagram.com/p/{shortcode}/")
    print(f"  Media: {media_path.name}")
    if has_music:
        print(f"  Music: embedded (not an Instagram music sticker)")
    print(f"  Tags:  {len(batch_users)} invisible mentions")
    print()
    for i, u in enumerate(batch_users, 1):
        print(f"    {i:2d}. @{u.username}")
    print()
    print(f"  Mention size: {MENTION_WIDTH}x{MENTION_HEIGHT} at ({MENTION_X}, {MENTION_Y})")
    print("  All tagged users WILL receive a notification.")
    print("=" * 55)

    answer = input("\n  Post this story? [y/N]: ").strip().lower()
    return answer == "y"


def upload_story(cl: Client, media_path: Path, media_type: str, mentions: list):
    print(f"\nUploading {media_type} story...")
    if media_type == "video":
        story = cl.video_upload_to_story(
            path=str(media_path),
            mentions=mentions,
        )
    else:
        story = cl.photo_upload_to_story(
            path=str(media_path),
            mentions=mentions,
        )
    print(f"\nStory posted!")
    print(f"  Story PK: {story.pk}")
    print(f"  Mentions: {len(mentions)} users (invisible)")
    return story


def cleanup():
    for f in TEMP_DIR.iterdir():
        if f.is_file():
            f.unlink()


def main():
    print()
    print("=" * 55)
    print("  INSTAGRAM STORY TAGGER")
    print("  Post -> Story with invisible mention tags")
    print("=" * 55)

    config = load_config()
    setup_dirs()

    # 1. Login
    cl = login(
        config["username"],
        config["password"],
        config.get("proxy"),
    )

    # 2. Fetch post media (photo or video)
    media_path, media_type = fetch_post_media(cl, config["target_post"])

    # 3. Handle music — prompt user or use config default
    has_music = False
    music_source = config.get("music_source")

    # Ask the user if they want to provide a music link
    print()
    add_music = input("  Do you have a YouTube/Spotify link or audio file for music? [y/N]: ").strip().lower()
    if add_music == "y":
        music_source = input("  Paste the YouTube URL, Spotify URL, or local file path: ").strip()
        if not music_source:
            print("  No link provided. Skipping music.")
            music_source = None
    elif music_source:
        print(f"  Using music from config: {music_source}")
    else:
        print("  No music. Continuing without audio.")

    if music_source:
        if not shutil.which("ffmpeg"):
            print("\n  Warning: ffmpeg is required for music. Skipping music.")
        else:
            print(f"\n  Music source: {music_source}")
            audio_path = resolve_music(music_source)
            volume = config["music_volume"]
            start = config["music_start"]

            if media_type == "photo":
                # Photo + music → becomes a video story
                print("\n  Note: Photo + music will be uploaded as a video story.")
                media_path = photo_to_video_with_audio(media_path, audio_path, volume, start)
                media_type = "video"
            else:
                # Video + music → merge audio
                media_path = merge_audio_to_video(media_path, audio_path, volume, start)

            has_music = True
            print()
            print("  Note: Music is embedded as audio in the video file.")
            print("  It will NOT appear as an Instagram music sticker")
            print("  (no song name, artist, or lyrics overlay).")

    # 4. Resolve ALL usernames upfront
    users, failed = resolve_users(cl, config["tagged_users"])

    if not users:
        print("\nNo users could be resolved. Nothing to do.")
        sys.exit(1)

    # 5. Split into batches of 20
    batches = chunk_users(users, 20)
    total_batches = len(batches)

    print()
    print("=" * 55)
    print(f"  {len(users)} users resolved -> {total_batches} stor{'y' if total_batches == 1 else 'ies'} (20 per story)")
    if failed:
        print(f"  {len(failed)} failed: {', '.join('@' + f for f in failed)}")
    print("=" * 55)

    # 6. Ask how many stories to post
    if total_batches == 1:
        num_to_post = 1
    else:
        while True:
            answer = input(f"\n  How many stories to post? [1-{total_batches} or 'all']: ").strip().lower()
            if answer == "all":
                num_to_post = total_batches
                break
            try:
                num_to_post = int(answer)
                if 1 <= num_to_post <= total_batches:
                    break
                print(f"  Please enter a number between 1 and {total_batches}.")
            except ValueError:
                print(f"  Please enter a number between 1 and {total_batches}, or 'all'.")

    # 7. Post each batch
    posted = 0
    total_tagged = 0
    errored = False
    for i, batch in enumerate(batches[:num_to_post], 1):
        if not confirm_batch(config["target_post"], media_path, media_type,
                             batch, i, num_to_post, has_music):
            print(f"\n  Skipped story {i}/{num_to_post}.")
            continue

        mentions = build_mentions(batch)
        try:
            upload_story(cl, media_path, media_type, mentions)
            posted += 1
            total_tagged += len(batch)
        except (RateLimitError, PleaseWaitFewMinutes):
            print(f"\n  Rate limited on story {i}/{num_to_post}.")
            print(f"  Successfully posted {posted} stor{'y' if posted == 1 else 'ies'} before being limited.")
            print("  Wait a few minutes and try again.")
            errored = True
            break
        except Exception as e:
            print(f"\n  Upload failed on story {i}/{num_to_post}: {e}")
            print(f"  Successfully posted {posted} stor{'y' if posted == 1 else 'ies'} before failure.")
            errored = True
            break

        # Delay between stories to avoid rate limits
        if i < num_to_post:
            delay = random.uniform(30, 60)
            print(f"\n  Waiting {delay:.0f}s before next story...")
            time.sleep(delay)

    # 8. Save session and clean up
    session_file = SESSION_DIR / f"{config['username']}.json"
    cl.dump_settings(str(session_file))
    if not errored:
        cleanup()
    else:
        print("\n  Temp files kept for retry (run again to resume).")

    print()
    print("=" * 55)
    print(f"  DONE — Posted {posted}/{num_to_post} stories, {total_tagged} users tagged")
    print("=" * 55)


if __name__ == "__main__":
    main()
