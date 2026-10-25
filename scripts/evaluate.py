"""Hold-out evaluation of the recommender.

For each genre, Last.fm's top tracks stand in for a playlist. 20% of the tracks are hidden,
the rest are fed to the recommender, and we measure:

  recall      hidden tracks that reappear in the recommendations
  genre fit   recommendations whose artist carries the genre tag
  returned    how many of the requested songs came back (low = sparse data)
  baseline    recall of picking the same number of songs at random from Last.fm's global chart

Caveat: the playlists come from the same Last.fm data the recommender uses, so recall is
optimistic. Treat it as a regression check and a way to spot weak genres, not as an
absolute quality score.

Run from the project root:  .venv\\Scripts\\python scripts\\evaluate.py
"""
import asyncio
import random
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, lastfm, recommender  # noqa: E402

GENRES = {
    "indie rock": {"indie rock", "indie"},
    "k-pop": {"k-pop", "kpop", "korean"},
    "korean": {"korean", "k-pop", "kpop", "k-indie", "korean indie"},
    "hip-hop": {"hip-hop", "hip hop", "rap"},
    "jazz": {"jazz"},
    "electronic": {"electronic", "electronica"},
}
MODES = {"익숙하게": 0.1, "균형 있게": 0.4, "새롭게": 0.85}
PLAYLIST_SIZE = 60
HIDE = 0.2
COUNT = 30


async def genre_playlist(client, tag: str) -> list[dict]:
    body = await lastfm._call(client, "tag.getTopTracks", tag=tag, limit=PLAYLIST_SIZE)
    items = body.get("tracks", {}).get("track", [])
    return [{"artist": t["artist"]["name"], "title": t["name"]} for t in items]


async def global_chart(client) -> list[dict]:
    body = await lastfm._call(client, "chart.getTopTracks", limit=200)
    return [
        {"artist": t["artist"]["name"], "title": t["name"]}
        for t in body.get("tracks", {}).get("track", [])
    ]


async def genre_fit(client, recs: list[dict], aliases: set[str]) -> float:
    if not recs:
        return 0.0
    sem = asyncio.Semaphore(5)

    async def has_tag(rec):
        async with sem:
            try:
                tags = await lastfm.top_tags(client, rec["artist"], limit=10)
            except lastfm.LastfmError:
                return False
            return bool(aliases & set(tags))

    flags = await asyncio.gather(*(has_tag(r) for r in recs))
    return sum(flags) / len(flags)


async def main():
    if config.missing_keys():
        sys.exit(f".env에 키가 없어요: {', '.join(config.missing_keys())}")
    rng = random.Random(7)
    async with httpx.AsyncClient(timeout=20) as client:
        chart = await global_chart(client)
        print(f"{'장르':<11}{'성향':<9}{'곡수':>5}{'숨김':>5}{'반환':>6}{'재현율':>8}{'무작위':>8}{'장르일치':>9}")
        totals: dict[str, list[float]] = {m: [] for m in MODES}
        for genre, aliases in GENRES.items():
            tracks = await genre_playlist(client, genre)
            if len(tracks) < 20:
                print(f"{genre:<11}데이터 부족 ({len(tracks)}곡)")
                continue
            rng.shuffle(tracks)
            cut = int(len(tracks) * HIDE)
            hidden, seeds = tracks[:cut], tracks[cut:]
            hidden_keys = {recommender.track_key(t["artist"], t["title"]) for t in hidden}
            for mode, novelty in MODES.items():
                recs = await recommender.recommend(
                    client, seeds, count=COUNT, novelty=novelty, rng=random.Random(1)
                )
                keys = {recommender.track_key(r["artist"], r["title"]) for r in recs}
                recall = len(keys & hidden_keys) / len(hidden_keys)
                pool = [
                    t for t in chart
                    if recommender.track_key(t["artist"], t["title"]) not in
                    {recommender.track_key(s["artist"], s["title"]) for s in seeds}
                ]
                picks = {
                    recommender.track_key(t["artist"], t["title"])
                    for t in rng.sample(pool, min(COUNT, len(pool)))
                }
                baseline = len(picks & hidden_keys) / len(hidden_keys)
                fit = await genre_fit(client, recs, aliases)
                totals[mode].append(recall)
                print(
                    f"{genre:<11}{mode:<9}{len(seeds):>5}{len(hidden):>5}{len(recs):>6}"
                    f"{recall:>8.0%}{baseline:>8.0%}{fit:>9.0%}"
                )
        print()
        for mode, values in totals.items():
            if values:
                print(f"{mode} 평균 재현율: {sum(values) / len(values):.0%}")


if __name__ == "__main__":
    asyncio.run(main())
