# Instagram Story Tagger

Repost any Instagram post (photo, video, or album) as a Story with up to 20 invisible mention tags. Tagged users receive a notification without visible clutter on the story.

## How It Works

1. Logs into Instagram using the private API (`instagrapi`)
2. Downloads media from a target post (photo, video, or first item of an album)
3. Resizes/pads media to 9:16 story format (1080×1920) with black bars
4. Resolves tagged usernames to Instagram user IDs
5. Uploads the story with invisible mentions positioned at 1% size in the bottom-right corner

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) (required for video posts, optional for photo-only)

## Setup

```bash
# Clone the repo
git clone https://github.com/MakeItEzzz555/ig-story-tagger.git
cd ig-story-tagger

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Configuration

```bash
cp config.example.json config.json
```

Edit `config.json` with your details:

```json
{
  "username": "your_instagram_username",
  "password": "your_instagram_password",
  "target_post": "POST_SHORTCODE",
  "proxy": null,
  "tagged_users": ["user1", "user2", "user3"]
}
```

| Field | Description |
|-------|-------------|
| `username` | Your Instagram username |
| `password` | Your Instagram password |
| `target_post` | The shortcode from the post URL (e.g. `DV6haDMiABK` from `instagram.com/p/DV6haDMiABK/`) |
| `proxy` | Optional HTTP/SOCKS proxy (e.g. `http://host:port`), set to `null` to disable |
| `tagged_users` | List of up to 20 Instagram usernames to tag (with or without `@`) |

## Usage

```bash
python ig_story_tagger.py
```

The script will:
- Log in (or restore a saved session)
- Download and format the post media
- Resolve all tagged usernames
- Show a confirmation prompt before posting
- Upload the story with invisible mentions

## Features

- **Session persistence** — saves login sessions to avoid repeated authentication
- **2FA support** — prompts for verification code when two-factor auth is enabled
- **Photo & video support** — handles photos, videos/reels, and album posts
- **Auto-formatting** — resizes images and pads videos to 9:16 story dimensions
- **Rate limit aware** — randomized delays between API calls to avoid blocks
- **Interactive confirmation** — requires explicit `y` before uploading

## Disclaimer

This tool uses Instagram's private API through `instagrapi`. Use at your own risk. Automated interactions may violate Instagram's Terms of Service and could result in account restrictions.

## License

MIT
