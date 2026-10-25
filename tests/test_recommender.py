import asyncio
import random
import unittest
from unittest import mock

from app import recommender

PLAYLIST = [
    {"artist": "A", "title": "Seed One"},
    {"artist": "B", "title": "Seed Two"},
]

SIMILAR = {
    "Seed One": [
        {"artist": "X", "title": "Both", "match": 0.9},
        {"artist": "Y", "title": "Only One", "match": 0.8},
        {"artist": "B", "title": "Seed Two (Remastered)", "match": 0.7},  # already owned
    ],
    "Seed Two": [
        {"artist": "X", "title": "Both", "match": 0.8},
        {"artist": "Z", "title": "Obscure", "match": 0.6},
    ],
}
LISTENERS = {"Both": 1_000_000, "Only One": 500_000, "Obscure": 1_000}


async def fake_similar(client, artist, title, limit=30):
    return SIMILAR[title]


async def fake_listeners(client, artist, title):
    return LISTENERS[title]


TAGS = {"A": ["rock", "indie"], "B": ["rock", "pop"], "X": ["rock", "indie"], "Y": ["jazz"], "Z": ["jazz"]}


async def fake_top_tags(client, artist, limit=5):
    return TAGS.get(artist, [])


def run(**kwargs):
    with mock.patch("app.lastfm.similar_tracks", fake_similar), mock.patch(
        "app.lastfm.listeners", fake_listeners
    ), mock.patch("app.lastfm.top_tags", fake_top_tags):
        return asyncio.run(
            recommender.recommend(None, PLAYLIST, rng=random.Random(0), **kwargs)
        )


class RecommenderTest(unittest.TestCase):
    def test_excludes_songs_already_in_playlist(self):
        titles = [r["title"] for r in run(novelty=0)]
        self.assertNotIn("Seed Two (Remastered)", titles)

    def test_agreement_between_seeds_ranks_first_when_safe(self):
        results = run(novelty=0)
        self.assertEqual(results[0]["title"], "Both")
        self.assertTrue(any("2곡에서 공통 추천" in line for line in results[0]["reasons"]))

    def test_high_novelty_prefers_obscure_songs(self):
        results = run(novelty=1)
        self.assertEqual(results[0]["title"], "Obscure")
        self.assertTrue(any("덜 알려진" in line for line in results[0]["reasons"]))

    def test_reasons_cite_closest_seed_shared_tags_and_artist_status(self):
        both = run(novelty=0)[0]
        text = " ".join(both["reasons"])
        self.assertIn("가장 비슷해요 (유사도 0.90)", text)
        self.assertIn("rock", text)
        self.assertIn("새 아티스트", text)

    def test_reasons_are_at_most_two_lines(self):
        for rec in run(novelty=0.5):
            self.assertLessEqual(len(rec["reasons"]), 2)

    def test_reasons_differ_between_songs(self):
        results = run(novelty=0)
        self.assertGreater(len({tuple(r["reasons"]) for r in results}), 1)

    def test_history_is_excluded_for_repeat_requests(self):
        key = recommender.track_key("X", "Both")
        titles = [r["title"] for r in run(novelty=0, exclude={key})]
        self.assertNotIn("Both", titles)

    def test_count_limits_results(self):
        self.assertEqual(len(run(novelty=0, count=1)), 1)

    def test_track_key_ignores_remaster_and_feat(self):
        self.assertEqual(
            recommender.track_key("A", "Song (feat. B)"),
            recommender.track_key("a", "Song - Remastered 2011"),
        )

    def test_parse_track_lines(self):
        tracks = recommender.parse_track_lines("Artist - Title\nbad line\nA - B - C")
        self.assertEqual(tracks[0], {"artist": "Artist", "title": "Title"})
        self.assertEqual(tracks[1], {"artist": "A", "title": "B - C"})
        self.assertEqual(len(tracks), 2)


class PoolDepthTest(unittest.TestCase):
    """Obscure songs sit below rank 60 by similarity; only a wider pool can surface them."""

    def run_with_many_candidates(self, novelty):
        many = [
            {"artist": f"Art{i}", "title": f"Song{i}", "match": 1.0 - i / 200}
            for i in range(120)
        ]

        async def similar(client, artist, title, limit=30):
            return many

        async def listeners(client, artist, title):
            return 10_000_000 if int(title[4:]) < 70 else 500  # deep candidates are obscure

        async def tags(client, artist, limit=5):
            return []

        with mock.patch("app.lastfm.similar_tracks", similar), mock.patch(
            "app.lastfm.listeners", listeners
        ), mock.patch("app.lastfm.top_tags", tags):
            return asyncio.run(
                recommender.recommend(
                    None, PLAYLIST, count=10, novelty=novelty, rng=random.Random(0)
                )
            )

    def test_safe_mode_stays_with_top_matches(self):
        titles = {int(r["title"][4:]) for r in self.run_with_many_candidates(0)}
        self.assertTrue(all(i < 60 for i in titles))

    def test_novel_mode_reaches_deeper_obscure_songs(self):
        titles = {int(r["title"][4:]) for r in self.run_with_many_candidates(1)}
        self.assertTrue(any(i >= 70 for i in titles))


if __name__ == "__main__":
    unittest.main()
