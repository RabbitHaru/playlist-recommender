import time

import httpx

from . import config

API = "http://ws.audioscrobbler.com/2.0/"


class LastfmError(Exception):
    pass


_cache: dict[tuple, tuple[float, dict]] = {}
CACHE_TTL = 6 * 3600
CACHE_MAX = 5000


async def _call(client: httpx.AsyncClient, method: str, **params) -> dict:
    """Cached so repeated requests for the same song don't burn the free Last.fm quota."""
    cache_key = (method, tuple(sorted(params.items())))
    hit = _cache.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    body = await _fetch(client, method, **params)
    if len(_cache) >= CACHE_MAX:
        _cache.clear()
    _cache[cache_key] = (time.time(), body)
    return body


async def _fetch(client: httpx.AsyncClient, method: str, **params) -> dict:
    resp = await client.get(
        API,
        params={"method": method, "api_key": config.LASTFM_API_KEY, "format": "json", **params},
    )
    if resp.status_code != 200:
        raise LastfmError(f"Last.fm 요청 실패 (HTTP {resp.status_code})")
    body = resp.json()
    if "error" in body:
        # 6 = track not found: not fatal, the seed just has no neighbours.
        if body["error"] == 6:
            return {}
        raise LastfmError(body.get("message", "Last.fm 오류"))
    return body


async def similar_tracks(
    client: httpx.AsyncClient, artist: str, title: str, limit: int = 30
) -> list[dict]:
    """[{"artist", "title", "match"}] with match in 0..1."""
    body = await _call(
        client, "track.getSimilar", artist=artist, track=title, limit=limit, autocorrect=1
    )
    result = []
    for item in body.get("similartracks", {}).get("track", []):
        result.append(
            {
                "artist": item["artist"]["name"],
                "title": item["name"],
                "match": float(item.get("match") or 0),
            }
        )
    return result


async def listeners(client: httpx.AsyncClient, artist: str, title: str) -> int:
    body = await _call(client, "track.getInfo", artist=artist, track=title, autocorrect=1)
    return int(body.get("track", {}).get("listeners") or 0)


# Tags that say something about the listener, not the music.
_JUNK_TAGS = {"seen live", "favorites", "favourite", "favorite", "favourites", "albums i own", "my music"}


async def top_tags(client: httpx.AsyncClient, artist: str, limit: int = 5) -> list[str]:
    body = await _call(client, "artist.getTopTags", artist=artist, autocorrect=1)
    tags = []
    for tag in body.get("toptags", {}).get("tag", []):
        name = tag["name"].strip().lower()
        if name in _JUNK_TAGS or int(tag.get("count") or 0) < 20:
            continue
        tags.append(name)
        if len(tags) >= limit:
            break
    return tags
