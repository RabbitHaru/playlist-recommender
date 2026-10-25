import asyncio
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, config, lastfm, recommender
from .providers import spotify
from .providers import ProviderError, get_providers, pick_provider

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Playlist Recommender")

# The server keeps no user data: playlists are read per request, and the list of
# already-recommended songs lives in the browser and is sent back as `exclude`.

RATE_LIMIT = 10  # requests per IP per minute; keeps the free API quotas safe when public
_hits: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    hits = _hits[ip]
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= RATE_LIMIT:
        raise HTTPException(429, "요청이 너무 많아요. 잠시 후 다시 시도해 주세요.")
    hits.append(now)


class RecommendRequest(BaseModel):
    playlist_url: str = ""
    tracks_text: str = Field("", max_length=20000, description="'가수 - 제목' 줄 단위")
    count: int = Field(20, ge=1, le=30)
    novelty: float = Field(0.3, ge=0, le=1)
    exclude: list[str] = Field(default_factory=list, max_length=2000)


@app.get("/api/health")
async def health():
    return {"ok": not config.missing_keys(), "missing": config.missing_keys()}


@app.post("/api/recommend")
async def recommend(body: RecommendRequest, request: Request):
    _check_rate_limit(request)
    if config.missing_keys():
        raise HTTPException(503, f".env에 키가 없어요: {', '.join(config.missing_keys())}")

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            if body.playlist_url.strip():
                user_token = await auth.access_token(client, request.cookies.get(auth.COOKIE))
                playlist = await pick_provider(body.playlist_url).fetch_playlist(
                    client, body.playlist_url, user_token
                )
            else:
                tracks = recommender.parse_track_lines(body.tracks_text)
                playlist = {"name": "직접 입력", "tracks": tracks}
            if not playlist["tracks"]:
                raise ProviderError("플레이리스트에서 곡을 찾지 못했어요.")

            recs = await recommender.recommend(
                client,
                playlist["tracks"],
                count=body.count,
                novelty=body.novelty,
                exclude=set(body.exclude),
            )

            # Link each result back to the platform (Spotify for now).
            provider = get_providers()[0]
            sem = asyncio.Semaphore(5)

            async def attach(rec: dict):
                async with sem:
                    rec["link"] = await provider.find_link(client, rec["artist"], rec["title"])
                rec["key"] = recommender.track_key(rec["artist"], rec["title"])

            await asyncio.gather(*(attach(r) for r in recs))
        except (ProviderError, lastfm.LastfmError) as exc:
            raise HTTPException(400, str(exc))

    return {
        "playlist": playlist["name"],
        "playlist_id": playlist.get("id"),
        "seed_count": len(playlist["tracks"]),
        "results": recs,
    }


SECURE_COOKIE = config.SPOTIFY_REDIRECT_URI.startswith("https://")


@app.get("/auth/login")
async def login(write: bool = False):
    """write=1 asks Spotify for playlist-modify permission on top of read access."""
    if config.missing_keys():
        raise HTTPException(503, f".env에 키가 없어요: {', '.join(config.missing_keys())}")
    return RedirectResponse(auth.login_url(write=write))


@app.get("/callback")
async def callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/?login=cancelled")
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            sid = await auth.finish_login(client, code, state)
        except auth.AuthError:
            return RedirectResponse("/?login=failed")
    response = RedirectResponse("/?login=ok")
    response.set_cookie(
        auth.COOKIE, sid, max_age=auth.SESSION_TTL,
        httponly=True, samesite="lax", secure=SECURE_COOKIE,
    )
    return response


async def _me(client: httpx.AsyncClient, token: str) -> dict | None:
    resp = await spotify._get(client, "/me", token)
    return resp.json() if resp.status_code == 200 else None


@app.get("/auth/me")
async def me(request: Request):
    async with httpx.AsyncClient(timeout=15) as client:
        token = await auth.access_token(client, request.cookies.get(auth.COOKIE))
        if not token:
            return {"logged_in": False}
        profile = await _me(client, token) or {}
    return {
        "logged_in": True,
        "name": profile.get("display_name") or profile.get("id") or "",
        "can_write": auth.can_write(request.cookies.get(auth.COOKIE)),
    }


@app.post("/auth/logout")
async def logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE))
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response


@app.get("/api/playlists")
async def my_playlists(request: Request):
    """Playlists the logged-in user owns or collaborates on (the only readable ones)."""
    _check_rate_limit(request)
    async with httpx.AsyncClient(timeout=15) as client:
        token = await auth.access_token(client, request.cookies.get(auth.COOKIE))
        if not token:
            raise HTTPException(401, "로그인이 필요해요.")
        profile = await _me(client, token) or {}
        resp = await spotify._get(client, "/me/playlists", token, limit=50)
        if resp.status_code != 200:
            raise HTTPException(400, f"플레이리스트 목록을 가져오지 못했어요 (HTTP {resp.status_code}).")
        items = resp.json().get("items", [])
    mine = [
        {"id": p["id"], "name": p["name"], "url": p["external_urls"]["spotify"]}
        for p in items
        if p and (p.get("owner", {}).get("id") == profile.get("id") or p.get("collaborative"))
    ]
    return {"playlists": mine}


class AddRequest(BaseModel):
    uris: list[str] = Field(min_length=1, max_length=50)


def _check_same_origin(request: Request) -> None:
    """Browsers send Origin on cross-site POSTs; refuse anything not from this site."""
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
        raise HTTPException(403, "허용되지 않은 요청이에요.")


@app.post("/api/playlists/{playlist_id}/tracks")
async def add_to_playlist(playlist_id: str, body: AddRequest, request: Request):
    _check_same_origin(request)
    _check_rate_limit(request)
    sid = request.cookies.get(auth.COOKIE)
    if not spotify.valid_playlist_id(playlist_id) or not all(
        spotify.valid_track_uri(u) for u in body.uris
    ):
        raise HTTPException(400, "추가할 곡 정보가 올바르지 않아요.")
    async with httpx.AsyncClient(timeout=15) as client:
        token = await auth.access_token(client, sid)
        if not token:
            raise HTTPException(401, "로그인이 필요해요.")
        if not auth.can_write(sid):
            raise HTTPException(403, "need_write")
        try:
            await spotify.add_tracks(client, playlist_id, body.uris, token)
        except ProviderError as exc:
            raise HTTPException(400, str(exc))
    return {"added": len(body.uris)}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
