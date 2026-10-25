import asyncio
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import auth, main
from app.providers import spotify

PLAYLIST = "2pbTyizE0x8uthARrAPm02"
URI = "spotify:track:3dPQuX8Gs42Y7b454ybpMR"


class FakeResponse:
    def __init__(self, status):
        self.status_code = status


class FakeClient:
    """Answers POSTs with the given statuses in order and records the URLs."""

    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.urls = []

    async def post(self, url, json=None, headers=None):
        self.urls.append(url)
        return FakeResponse(self.statuses.pop(0))


class SpotifyAddTest(unittest.TestCase):
    def test_falls_back_to_tracks_endpoint_when_items_is_missing(self):
        client = FakeClient(404, 201)
        asyncio.run(spotify.add_tracks(client, PLAYLIST, [URI], "token"))
        self.assertTrue(client.urls[0].endswith("/items"))
        self.assertTrue(client.urls[1].endswith("/tracks"))

    def test_forbidden_gives_a_helpful_message(self):
        with self.assertRaises(spotify.SpotifyError) as ctx:
            asyncio.run(spotify.add_tracks(FakeClient(403), PLAYLIST, [URI], "token"))
        self.assertIn("추가할 수 없어요", str(ctx.exception))

    def test_malformed_uris_are_rejected_before_any_request(self):
        client = FakeClient()
        for bad in ("spotify:track:short", "https://evil.example/x", "spotify:album:" + "a" * 22):
            with self.assertRaises(spotify.SpotifyError):
                asyncio.run(spotify.add_tracks(client, PLAYLIST, [bad], "token"))
        self.assertEqual(client.urls, [])


class ScopeTest(unittest.TestCase):
    def setUp(self):
        auth._pending.clear()
        auth._sessions.clear()

    def test_write_scopes_only_when_requested(self):
        read = parse_qs(urlparse(auth.login_url()).query)["scope"][0]
        write = parse_qs(urlparse(auth.login_url(write=True)).query)["scope"][0]
        self.assertNotIn("modify", read)
        self.assertIn("playlist-modify-public", write)
        self.assertIn("playlist-modify-private", write)

    def test_can_write_follows_granted_scope(self):
        auth._sessions["r"] = {"scope": auth.SCOPES_READ, "created": time.time()}
        auth._sessions["w"] = {"scope": auth.SCOPES_WRITE, "created": time.time()}
        self.assertFalse(auth.can_write("r"))
        self.assertTrue(auth.can_write("w"))
        self.assertFalse(auth.can_write(None))


class AddEndpointTest(unittest.TestCase):
    def setUp(self):
        auth._sessions.clear()
        main._hits.clear()
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8000")
        self.url = f"/api/playlists/{PLAYLIST}/tracks"

    def login(self, scope):
        auth._sessions["sid"] = {
            "access_token": "t", "refresh_token": "r",
            "expires": time.time() + 3600, "created": time.time(), "scope": scope,
        }
        self.client.cookies.set(auth.COOKIE, "sid")

    def test_requires_login(self):
        self.assertEqual(self.client.post(self.url, json={"uris": [URI]}).status_code, 401)

    def test_read_only_login_is_told_to_grant_write(self):
        self.login(auth.SCOPES_READ)
        resp = self.client.post(self.url, json={"uris": [URI]})
        self.assertEqual((resp.status_code, resp.json()["detail"]), (403, "need_write"))

    def test_other_site_origin_is_refused(self):
        self.login(auth.SCOPES_WRITE)
        resp = self.client.post(self.url, json={"uris": [URI]}, headers={"Origin": "http://evil.example"})
        self.assertEqual(resp.status_code, 403)

    def test_bad_uri_and_bad_playlist_id_are_rejected(self):
        self.login(auth.SCOPES_WRITE)
        self.assertEqual(self.client.post(self.url, json={"uris": ["nope"]}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/playlists/short/tracks", json={"uris": [URI]}).status_code, 400
        )

    def test_success_calls_spotify_with_the_users_token(self):
        self.login(auth.SCOPES_WRITE)
        with mock.patch("app.main.spotify.add_tracks", new=mock.AsyncMock()) as add:
            resp = self.client.post(
                self.url, json={"uris": [URI]}, headers={"Origin": "http://127.0.0.1:8000"}
            )
        self.assertEqual((resp.status_code, resp.json()), (200, {"added": 1}))
        self.assertEqual(add.call_args.args[1:], (PLAYLIST, [URI], "t"))


if __name__ == "__main__":
    unittest.main()
