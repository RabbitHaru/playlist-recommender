import asyncio
import base64
import hashlib
import unittest
from urllib.parse import parse_qs, urlparse
from unittest import mock

from app import auth


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.posted = []

    async def post(self, url, data=None, **kwargs):
        self.posted.append(data)
        return self.response


class AuthTest(unittest.TestCase):
    def setUp(self):
        auth._pending.clear()
        auth._sessions.clear()

    def test_login_url_uses_pkce_state_and_read_only_scopes(self):
        query = parse_qs(urlparse(auth.login_url()).query)
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn("modify", query["scope"][0])
        state = query["state"][0]
        verifier = auth._pending[state][0]
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.assertEqual(query["code_challenge"], [expected])

    def test_unknown_state_is_rejected(self):
        client = FakeClient(FakeResponse())
        with self.assertRaises(auth.AuthError):
            asyncio.run(auth.finish_login(client, "code", "forged-state"))
        self.assertEqual(client.posted, [])

    def test_state_is_single_use(self):
        parse_qs(urlparse(auth.login_url()).query)
        state = next(iter(auth._pending))
        client = FakeClient(FakeResponse(200, {"access_token": "t", "refresh_token": "r", "expires_in": 3600}))
        asyncio.run(auth.finish_login(client, "code", state))
        with self.assertRaises(auth.AuthError):
            asyncio.run(auth.finish_login(client, "code", state))

    def test_login_sends_verifier_but_never_client_secret(self):
        auth.login_url()
        state = next(iter(auth._pending))
        client = FakeClient(FakeResponse(200, {"access_token": "t", "refresh_token": "r", "expires_in": 3600}))
        sid = asyncio.run(auth.finish_login(client, "code", state))
        self.assertIn("code_verifier", client.posted[0])
        self.assertNotIn("client_secret", client.posted[0])
        self.assertEqual(asyncio.run(auth.access_token(client, sid)), "t")

    def test_expired_token_is_refreshed_and_logout_clears_session(self):
        auth._sessions["sid"] = {
            "access_token": "old", "refresh_token": "r", "expires": 0, "created": 1e12,
        }
        client = FakeClient(FakeResponse(200, {"access_token": "new", "expires_in": 3600}))
        self.assertEqual(asyncio.run(auth.access_token(client, "sid")), "new")
        auth.logout("sid")
        self.assertIsNone(asyncio.run(auth.access_token(client, "sid")))

    def test_no_session_means_not_logged_in(self):
        self.assertIsNone(asyncio.run(auth.access_token(FakeClient(FakeResponse()), None)))


if __name__ == "__main__":
    unittest.main()
