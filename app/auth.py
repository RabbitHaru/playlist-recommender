"""Spotify login (Authorization Code + PKCE).

The user types their Spotify password only on accounts.spotify.com. This app receives a
short-lived access token limited to SCOPES and keeps it in server memory; the browser only
holds an opaque, HttpOnly session id.
"""
import base64
import hashlib
import secrets
import time

import httpx

from . import config

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
# Read-only on purpose. Adding songs to a playlist will need playlist-modify-* later.
SCOPES = "playlist-read-private playlist-read-collaborative"

COOKIE = "sid"
PENDING_TTL = 600
SESSION_TTL = 7 * 24 * 3600

_pending: dict[str, tuple[str, float]] = {}  # state -> (code_verifier, created)
_sessions: dict[str, dict] = {}  # sid -> tokens


class AuthError(Exception):
    pass


def _cleanup() -> None:
    now = time.time()
    for state in [s for s, (_, t) in _pending.items() if now - t > PENDING_TTL]:
        del _pending[state]
    for sid in [s for s, v in _sessions.items() if now - v["created"] > SESSION_TTL]:
        del _sessions[sid]


def login_url() -> str:
    _cleanup()
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    _pending[state] = (verifier, time.time())
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    query = httpx.QueryParams(
        {
            "client_id": config.SPOTIFY_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": config.SPOTIFY_REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "show_dialog": "false",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def finish_login(client: httpx.AsyncClient, code: str, state: str) -> str:
    """Exchanges the code for tokens and returns a new session id."""
    entry = _pending.pop(state, None)
    if not entry or time.time() - entry[1] > PENDING_TTL:
        raise AuthError("로그인 요청이 만료됐거나 올바르지 않아요. 다시 시도해 주세요.")
    resp = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.SPOTIFY_REDIRECT_URI,
            "client_id": config.SPOTIFY_CLIENT_ID,
            "code_verifier": entry[0],
        },
    )
    if resp.status_code != 200:
        raise AuthError("Spotify 로그인에 실패했어요. Redirect URI 설정을 확인해 주세요.")
    body = resp.json()
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "expires": time.time() + body["expires_in"],
        "created": time.time(),
    }
    return sid


async def access_token(client: httpx.AsyncClient, sid: str | None) -> str | None:
    """Valid access token for the session, refreshing it if needed; None if not logged in."""
    session = _sessions.get(sid or "")
    if not session:
        return None
    if time.time() < session["expires"] - 30:
        return session["access_token"]
    if not session["refresh_token"]:
        _sessions.pop(sid, None)
        return None
    resp = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": session["refresh_token"],
            "client_id": config.SPOTIFY_CLIENT_ID,
        },
    )
    if resp.status_code != 200:
        _sessions.pop(sid, None)
        return None
    body = resp.json()
    session["access_token"] = body["access_token"]
    session["expires"] = time.time() + body["expires_in"]
    session["refresh_token"] = body.get("refresh_token", session["refresh_token"])
    return session["access_token"]


def logout(sid: str | None) -> None:
    _sessions.pop(sid or "", None)
