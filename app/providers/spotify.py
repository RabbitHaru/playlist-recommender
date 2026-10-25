import base64
import re
import time

import httpx

from .. import config
from . import ProviderError

TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

_token: dict = {"value": None, "expires": 0.0}


class SpotifyError(ProviderError):
    pass


def parse_playlist_id(text: str) -> str:
    """Accepts an open.spotify.com link, a spotify: URI, or a bare id."""
    text = text.strip()
    match = re.search(r"playlist[/:]([A-Za-z0-9]{22})", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{22}", text):
        return text
    raise SpotifyError("Spotify 플레이리스트 링크를 인식하지 못했어요.")


async def _get_token(client: httpx.AsyncClient) -> str:
    if _token["value"] and time.time() < _token["expires"] - 30:
        return _token["value"]
    raw = f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode()
    resp = await client.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers={"Authorization": "Basic " + base64.b64encode(raw).decode()},
    )
    if resp.status_code != 200:
        raise SpotifyError("Spotify 인증에 실패했어요. Client ID/Secret을 확인해 주세요.")
    body = resp.json()
    _token["value"] = body["access_token"]
    _token["expires"] = time.time() + body["expires_in"]
    return _token["value"]


async def _get(
    client: httpx.AsyncClient, path: str, user_token: str | None = None, **params
) -> httpx.Response:
    """Uses the logged-in user's token when given, else the app-only token."""
    token = user_token or await _get_token(client)
    return await client.get(
        API + path, params=params, headers={"Authorization": f"Bearer {token}"}
    )


PLAYLIST_ID_RE = re.compile(r"[A-Za-z0-9]{22}")
TRACK_URI_RE = re.compile(r"spotify:track:[A-Za-z0-9]{22}")


def valid_playlist_id(value: str) -> bool:
    return bool(PLAYLIST_ID_RE.fullmatch(value))


def valid_track_uri(value: str) -> bool:
    return bool(TRACK_URI_RE.fullmatch(value))


def _track_from_item(item: dict) -> dict | None:
    track = item.get("track") or item.get("item")
    if not track or track.get("type", "track") != "track" or not track.get("name"):
        return None
    artists = track.get("artists") or []
    if not artists:
        return None
    return {"artist": artists[0]["name"], "title": track["name"]}


async def fetch_playlist(
    client: httpx.AsyncClient, playlist_id: str, user_token: str | None = None
) -> dict:
    """Returns {"name": str, "tracks": [{"artist", "title"}]}.

    Spotify only lets a logged-in user read the track list of playlists they own or
    collaborate on, so `user_token` is needed in practice.
    """
    head = await _get(client, f"/playlists/{playlist_id}", user_token, fields="name")
    if head.status_code == 404:
        raise SpotifyError("플레이리스트를 찾지 못했어요. 링크를 확인해 주세요.")
    if head.status_code != 200:
        raise SpotifyError(f"플레이리스트 정보를 가져오지 못했어요 (HTTP {head.status_code}).")
    name = head.json().get("name", "")

    tracks: list[dict] = []
    offset = 0
    endpoint = None
    while True:
        # Spotify renamed /tracks to /items; use whichever the first page accepts.
        candidates = [endpoint] if endpoint else ["items", "tracks"]
        for candidate in candidates:
            resp = await _get(
                client, f"/playlists/{playlist_id}/{candidate}", user_token, limit=100, offset=offset
            )
            if resp.status_code == 200:
                endpoint = candidate
                break
        if resp.status_code in (401, 403):
            if user_token:
                raise SpotifyError(
                    "이 플레이리스트의 곡 목록을 읽을 수 없어요. 내가 만들었거나 "
                    "함께 편집하는 플레이리스트만 읽을 수 있어요."
                )
            raise SpotifyError(
                "곡 목록을 읽으려면 Spotify 로그인이 필요해요. 위의 ‘Spotify로 로그인’을 눌러 주세요."
            )
        if resp.status_code != 200:
            raise SpotifyError(f"곡 목록을 가져오지 못했어요 (HTTP {resp.status_code}).")
        body = resp.json()
        for item in body.get("items", []):
            track = _track_from_item(item)
            if track:
                tracks.append(track)
        if not body.get("next"):
            break
        offset += 100
    return {"id": playlist_id, "name": name, "tracks": tracks}


async def add_tracks(
    client: httpx.AsyncClient, playlist_id: str, uris: list[str], user_token: str
) -> None:
    """Appends tracks to a playlist the user owns or collaborates on (needs playlist-modify-*)."""
    if not valid_playlist_id(playlist_id) or not uris or not all(valid_track_uri(u) for u in uris):
        raise SpotifyError("추가할 곡 정보가 올바르지 않아요.")
    resp = None
    # Spotify renamed /tracks to /items; fall back if the first one is not found.
    for endpoint in ("items", "tracks"):
        resp = await client.post(
            f"{API}/playlists/{playlist_id}/{endpoint}",
            json={"uris": uris},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        if resp.status_code in (200, 201):
            return
        if resp.status_code != 404:
            break
    if resp.status_code in (401, 403):
        raise SpotifyError(
            "이 플레이리스트에 곡을 추가할 수 없어요. 내가 만들었거나 함께 편집하는 "
            "플레이리스트인지, 추가 권한을 허용했는지 확인해 주세요."
        )
    if resp.status_code == 429:
        raise SpotifyError("요청이 많아요. 잠시 후 다시 시도해 주세요.")
    raise SpotifyError(f"곡을 추가하지 못했어요 (HTTP {resp.status_code}).")


async def search_track(client: httpx.AsyncClient, artist: str, title: str) -> dict | None:
    """Finds the Spotify track for a Last.fm result so we can link to it."""
    resp = await _get(
        client, "/search", q=f"track:{title} artist:{artist}", type="track", limit=1
    )
    if resp.status_code != 200:
        return None
    items = resp.json().get("tracks", {}).get("items", [])
    if not items:
        return None
    track = items[0]
    images = track.get("album", {}).get("images", [])
    return {
        "uri": track["uri"],
        "url": track["external_urls"].get("spotify"),
        "image": images[-1]["url"] if images else None,
    }


class SpotifyProvider:
    name = "spotify"

    def can_handle(self, text: str) -> bool:
        return "spotify" in text.lower()

    async def fetch_playlist(
        self, client: httpx.AsyncClient, text: str, user_token: str | None = None
    ) -> dict:
        return await fetch_playlist(client, parse_playlist_id(text), user_token)

    async def add_tracks(
        self, client: httpx.AsyncClient, playlist_id: str, uris: list[str], user_token: str
    ) -> None:
        await add_tracks(client, playlist_id, uris, user_token)

    async def find_link(self, client: httpx.AsyncClient, artist: str, title: str) -> dict | None:
        return await search_track(client, artist, title)
