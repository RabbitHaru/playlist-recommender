import asyncio
import unittest
from unittest import mock

from app.providers import ProviderError, pick_provider, youtube


class ParsePlaylistIdTest(unittest.TestCase):
    def test_links_and_bare_ids(self):
        pid = "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        self.assertEqual(youtube.parse_playlist_id(f"https://www.youtube.com/playlist?list={pid}"), pid)
        self.assertEqual(youtube.parse_playlist_id(f"https://music.youtube.com/playlist?list={pid}&si=x"), pid)
        self.assertEqual(youtube.parse_playlist_id(f"https://www.youtube.com/watch?v=abc&list={pid}"), pid)
        self.assertEqual(youtube.parse_playlist_id(pid), pid)

    def test_rejects_non_playlist_text(self):
        with self.assertRaises(youtube.YouTubeError):
            youtube.parse_playlist_id("https://www.youtube.com/watch?v=abc")


class ParseVideoTest(unittest.TestCase):
    def test_topic_channel_gives_clean_artist(self):
        self.assertEqual(
            youtube.parse_video("Creep", "Radiohead - Topic"),
            {"artist": "Radiohead", "title": "Creep"},
        )

    def test_artist_dash_title_with_decoration_removed(self):
        self.assertEqual(
            youtube.parse_video("Coldplay - Yellow (Official Video)", "Coldplay"),
            {"artist": "Coldplay", "title": "Yellow"},
        )
        self.assertEqual(
            youtube.parse_video("IU(아이유) - 좋은 날 [Official M/V]", "1theK"),
            {"artist": "IU(아이유)", "title": "좋은 날"},
        )

    def test_falls_back_to_channel_name(self):
        self.assertEqual(
            youtube.parse_video("Starlight (Lyrics)", "MuseVEVO"),
            {"artist": "Muse", "title": "Starlight"},
        )

    def test_unavailable_videos_are_skipped(self):
        self.assertIsNone(youtube.parse_video("Deleted video", ""))
        self.assertIsNone(youtube.parse_video("Private video", "x"))


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)


class ProviderTest(unittest.TestCase):
    PID = "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"

    def fetch(self, client, key="k"):
        with mock.patch("app.config.YOUTUBE_API_KEY", key):
            return asyncio.run(
                youtube.YouTubeProvider().fetch_playlist(client, f"https://youtube.com/playlist?list={self.PID}")
            )

    def test_reads_all_pages_and_skips_unavailable(self):
        item = lambda t, c: {"snippet": {"title": t, "videoOwnerChannelTitle": c}}
        client = FakeClient([
            FakeResponse(200, {"items": [{"snippet": {"title": "My Mix"}}]}),
            FakeResponse(200, {"items": [item("Creep", "Radiohead - Topic"), item("Deleted video", "")], "nextPageToken": "T2"}),
            FakeResponse(200, {"items": [item("Muse - Starlight", "Muse")]}),
        ])
        result = self.fetch(client)
        self.assertEqual(result["name"], "My Mix")
        self.assertEqual([t["title"] for t in result["tracks"]], ["Creep", "Starlight"])
        self.assertNotIn("id", result)  # reserved for Spotify playlists we can add to
        self.assertEqual(client.calls[2][1]["pageToken"], "T2")

    def test_without_api_key_it_explains_instead_of_calling(self):
        client = FakeClient([])
        with self.assertRaises(youtube.YouTubeError) as ctx:
            self.fetch(client, key="")
        self.assertIn("아직", str(ctx.exception))
        self.assertEqual(client.calls, [])

    def test_missing_playlist_and_quota_errors_are_friendly(self):
        with self.assertRaises(youtube.YouTubeError):
            self.fetch(FakeClient([FakeResponse(200, {"items": []})]))
        quota = {"error": {"errors": [{"reason": "quotaExceeded"}]}}
        with self.assertRaises(youtube.YouTubeError) as ctx:
            self.fetch(FakeClient([FakeResponse(403, quota)]))
        self.assertIn("한도", str(ctx.exception))


class RoutingTest(unittest.TestCase):
    def test_links_go_to_the_right_provider(self):
        self.assertEqual(pick_provider("https://music.youtube.com/playlist?list=PLx").name, "youtube")
        self.assertEqual(pick_provider("https://youtu.be/abc?list=PLx").name, "youtube")
        self.assertEqual(pick_provider("https://open.spotify.com/playlist/abc").name, "spotify")
        with self.assertRaises(ProviderError):
            pick_provider("https://example.com/list")


if __name__ == "__main__":
    unittest.main()
