import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "")
# Optional: without it, YouTube links are disabled and everything else still works.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# Public address of the deployed site (e.g. https://name.onrender.com). Used for canonical links
# and the sitemap; when empty, the address of the incoming request is used.
SITE_URL = os.getenv("SITE_URL", "").rstrip("/")

# Must match a Redirect URI registered in the Spotify dashboard exactly.
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback")


def missing_keys() -> list[str]:
    values = {
        "SPOTIFY_CLIENT_ID": SPOTIFY_CLIENT_ID,
        "SPOTIFY_CLIENT_SECRET": SPOTIFY_CLIENT_SECRET,
        "LASTFM_API_KEY": LASTFM_API_KEY,
    }
    return [name for name, value in values.items() if not value]
