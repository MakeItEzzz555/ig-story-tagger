#!/usr/bin/env python3
"""
Instagram Story Tagger
Post a story from an existing post's image with up to 20 invisible mention tags.
Uses instagrapi (Instagram Private API).
"""

import json
import os
import sys
import random
import shutil
import subprocess
import time
from pathlib import Path

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

# Invisible mention positioning (bottom-right corner, 1% size)
MENTION_X = 0.99
MENTION_Y = 0.99
MENTION_WIDTH = 0.01
MENTION_HEIGHT = 0.01


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

    if len(config["tagged_users"]) > 20:
        print("Warning: Instagram allows max 20 mentions per story.")
        print("         Only the first 20 will be used.")
        config["tagged_users"] = config["tagged_users"][:20]

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

    import requests

    # Video post (Reel or Video)
    if media.media_type == 2 and media.video_url:
        video_url = str(media.video_url)
        print("  Downloading video...")
        resp = requests.get(video_url, timeout=120)
        resp.raise_for_status()
        raw_path = TEMP_DIR / f"{shortcode}_raw.mp4"
        raw_path.write_bytes(resp.content)
        print(f"  Downloaded: {len(resp.content) / 1024 / 1024:.1f} MB")
        story_path = pad_video_for_story(raw_path)
        print(f"  Ready: {story_path}")
        return story_path, "video"

    # Album — check if first item is video
    if media.media_type == 8 and media.resources:
        first = media.resources[0]
        if first.video_url:
            print("  (Album - using first video)")
            resp = requests.get(str(first.video_url), timeout=120)
            resp.raise_for_status()
            raw_path = TEMP_DIR / f"{shortcode}_raw.mp4"
            raw_path.write_bytes(resp.content)
            print(f"  Downloaded: {len(resp.content) / 1024 / 1024:.1f} MB")
            story_path = pad_video_for_story(raw_path)
            print(f"  Ready: {story_path}")
            return story_path, "video"
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

    img = img.resize((new_width, new_height), Image.LANCZOS)

    # Center on black canvas
    canvas = Image.new("RGB", (STORY_WIDTH, STORY_HEIGHT), (0, 0, 0))
    x_offset = (STORY_WIDTH - new_width) // 2
    y_offset = (STORY_HEIGHT - new_height) // 2
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


def confirm(shortcode: str, media_path: Path, media_type: str, users: list, failed: list) -> bool:
    print()
    print("=" * 55)
    print("  CONFIRM STORY UPLOAD")
    print("=" * 55)
    print(f"  Post:  https://instagram.com/p/{shortcode}/")
    print(f"  Media: {media_path.name}")
    print(f"  Tags:  {len(users)} invisible mentions")
    print()
    for i, u in enumerate(users, 1):
        print(f"    {i:2d}. @{u.username}")
    if failed:
        print(f"\n  Skipped (not found): {', '.join('@' + f for f in failed)}")
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

    # 3. Resolve usernames
    users, failed = resolve_users(cl, config["tagged_users"])

    if not users:
        print("\nNo users could be resolved. Nothing to do.")
        sys.exit(1)

    # 4. Confirm
    if not confirm(config["target_post"], media_path, media_type, users, failed):
        print("\nCancelled.")
        cleanup()
        sys.exit(0)

    # 5. Build invisible mentions and upload
    mentions = build_mentions(users)
    upload_story(cl, media_path, media_type, mentions)

    # 6. Save session and clean up
    session_file = SESSION_DIR / f"{config['username']}.json"
    cl.dump_settings(str(session_file))
    cleanup()

    print("\nDone!")


if __name__ == "__main__":
    main()
