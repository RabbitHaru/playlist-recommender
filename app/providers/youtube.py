"""YouTube / YouTube Music playlists through the official YouTube Data API v3.

Only public (or unlisted) playlists can be read, with just an API key and no user login.
Each page of 50 items costs 1 quota unit of the free daily quota.
"""
import re

import httpx

from .. import config
from . import ProviderError

API = "https://www.googleapis.com/youtube/v3"
MAX_PAGES = 4  # 200 videos is plenty to describe a playlist's taste

_LIST_PARAM = re.compile(r"[?&]list=([A-Za-z0-9_-]{10,})")
_BARE_ID = re.compile(r"(PL|OLAK5uy_|UU|FL)[A-Za-z0-9_-]{10,}")
# "(Official MV)", "[Lyrics]", "【MV】", "(Audio)", "(가사)" ... — decoration, not part of the song.
_NOISE = re.compile(
    r"[\(\[（【][^\)\]）】]*?(official|m/v|\bmv\b|music video|video|audio|lyric|visualizer|"
    r"\bhd\b|\bhq\b|4k|가사|뮤직비디오|뮤비)[^\)\]）】]*?[\)\]）】]",
    re.I,
)
_SEPARATORS = (" - ", " – ", " — ", " ‐ ")
_UNAVAILABLE = {"deleted video", "private video"}


class YouTubeError(ProviderError):
    pass


def parse_playlist_id(text: str) -> str:
    text = text.strip()
    match = _LIST_PARAM.search(text)
    if match:
        return match.group(1)
    if _BARE_ID.fullmatch(text):
        return text
    raise YouTubeError("YouTube 플레이리스트 링크를 인식하지 못했어요. 주소에 list= 가 들어 있어야 해요.")


def parse_video(title: str, channel: str) -> dict | None:
    """Best-effort split of a video into {"artist", "title"}; None if unusable."""
    if not title or title.strip().lower() in _UNAVAILABLE:
        return None
    cleaned = re.sub(r"\s{2,}", " ", _NOISE.sub("", title)).strip(" |-–—")

    channel = (channel or "").strip()
    if channel.endswith(" - Topic"):
        # Auto-generated YouTube Music uploads: channel is the artist, title is the song.
        return {"artist": channel[: -len(" - Topic")].strip(), "title": cleaned}

    for sep in _SEPARATORS:
        if sep in cleaned:
            artist, song = cleaned.split(sep, 1)
            if artist.strip() and song.strip():
                return {"artist": artist.strip(), "title": song.strip()}

    artist = re.sub(r"(VEVO|Official|공식)\s*$", "", channel, flags=re.I).strip()
    if artist and cleaned:
        return {"artist": artist, "title": cleaned}
    return None


async def _get(client: httpx.AsyncClient, path: str, **params) -> httpx.Response:
    return await client.get(f"{API}{path}", params={"key": config.YOUTUBE_API_KEY, **params})


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    reason = ""
    try:
        reason = resp.json()["error"]["errors"][0]["reason"]
    except Exception:
        pass
    if reason == "quotaExceeded":
        raise YouTubeError("오늘 YouTube 조회 한도를 다 썼어요. 내일 다시 시도하거나 곡 목록을 붙여넣어 주세요.")
    if resp.status_code in (400, 403):
        raise YouTubeError("YouTube API 키가 올바르지 않거나 사용할 수 없어요.")
    if resp.status_code == 404:
        raise YouTubeError("플레이리스트를 찾지 못했어요. 공개 플레이리스트인지 확인해 주세요.")
    raise YouTubeError(f"YouTube 요청 실패 (HTTP {resp.status_code}).")


class YouTubeProvider:
    name = "youtube"

    def can_handle(self, text: str) -> bool:
        lowered = text.lower()
        return any(host in lowered for host in ("youtube.com", "youtu.be"))

    async def fetch_playlist(
        self, client: httpx.AsyncClient, text: str, user_token: str | None = None
    ) -> dict:
        if not config.YOUTUBE_API_KEY:
            raise YouTubeError(
                "이 사이트에서는 아직 YouTube 링크를 쓸 수 없어요. 곡 목록을 붙여넣어 주세요."
            )
        playlist_id = parse_playlist_id(text)

        head = await _get(client, "/playlists", part="snippet", id=playlist_id)
        _raise_for(head)
        found = head.json().get("items", [])
        if not found:
            raise YouTubeError(
                "플레이리스트를 찾지 못했어요. 비공개이거나 자동 생성 믹스일 수 있어요."
            )
        name = found[0]["snippet"].get("title", "")

        tracks, token = [], None
        for _ in range(MAX_PAGES):
            params = {"part": "snippet", "playlistId": playlist_id, "maxResults": 50}
            if token:
                params["pageToken"] = token
            resp = await _get(client, "/playlistItems", **params)
            _raise_for(resp)
            body = resp.json()
            for item in body.get("items", []):
                snippet = item.get("snippet", {})
                parsed = parse_video(
                    snippet.get("title", ""), snippet.get("videoOwnerChannelTitle", "")
                )
                if parsed:
                    tracks.append(parsed)
            token = body.get("nextPageToken")
            if not token:
                break
        # No "id": that field is reserved for Spotify playlists the app can add songs to.
        return {"name": name, "tracks": tracks}
