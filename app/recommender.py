import asyncio
import math
import random
import re
from collections import Counter, defaultdict

import httpx

from . import lastfm

MAX_SEEDS = 30
POOL_SIZE = 60
MAX_PER_ARTIST = 2
TAG_BONUS = 0.15


def track_key(artist: str, title: str) -> str:
    """Normalised identity so '(Remastered)' / 'feat.' variants count as the same song."""
    title = re.sub(r"[\(\[].*?[\)\]]", "", title)
    title = re.sub(r"\s-\s.*$", "", title)
    return f"{artist.strip().lower()}|{title.strip().lower()}"


def parse_track_lines(text: str) -> list[dict]:
    """'Artist - Title' per line."""
    tracks = []
    for line in text.splitlines():
        if " - " in line:
            artist, title = line.split(" - ", 1)
            if artist.strip() and title.strip():
                tracks.append({"artist": artist.strip(), "title": title.strip()})
    return tracks


def _people(n: int) -> str:
    return f"{n / 10000:.0f}만 명" if n >= 10000 else f"{n:,}명"


def _reasons(
    cand: dict,
    seed_total: int,
    shared_tags: list[tuple[str, int]],
    listeners: int | None,
    popularity: float | None,
    same_artist: bool,
) -> list[str]:
    """At most two short lines: the closest song, then a one-line summary of the rest."""
    best_title, best_artist, best_match = max(cand["sources"], key=lambda s: s[2])
    n = len(cand["sources"])
    first = f"‘{best_title}’({best_artist})와 가장 비슷해요 (유사도 {best_match:.2f})"
    if n >= 2:
        first += f" · {seed_total}곡 중 {n}곡에서 공통 추천"

    facts = []
    if shared_tags:
        facts.append("·".join(tag for tag, _ in shared_tags) + " 장르 겹침")
    facts.append("이미 있는 아티스트" if same_artist else "새 아티스트")
    if listeners:
        label = "덜 알려진 곡" if popularity is not None and popularity < 0.6 else "대중적인 곡"
        facts.append(f"청취자 {_people(listeners)} ({label})")
    return [first, " · ".join(facts)]


async def recommend(
    client: httpx.AsyncClient,
    tracks: list[dict],
    count: int = 20,
    novelty: float = 0.3,
    exclude: set[str] | None = None,
    rng: random.Random | None = None,
) -> list[dict]:
    """novelty 0 = safest (most-agreed, popular), 1 = most adventurous."""
    rng = rng or random.Random()
    exclude = exclude or set()
    owned = {track_key(t["artist"], t["title"]) for t in tracks}
    owned_artists = {t["artist"].strip().lower() for t in tracks}

    seeds = tracks if len(tracks) <= MAX_SEEDS else rng.sample(tracks, MAX_SEEDS)
    sem = asyncio.Semaphore(5)

    async def fetch(seed: dict):
        async with sem:
            try:
                return seed, await lastfm.similar_tracks(client, seed["artist"], seed["title"])
            except lastfm.LastfmError:
                return seed, []

    candidates: dict[str, dict] = {}
    for seed, similar in await asyncio.gather(*(fetch(s) for s in seeds)):
        for item in similar:
            key = track_key(item["artist"], item["title"])
            if key in owned or key in exclude:
                continue
            cand = candidates.setdefault(
                key,
                {"artist": item["artist"], "title": item["title"], "sources": [], "score": 0.0},
            )
            cand["sources"].append((seed["title"], seed["artist"], item["match"]))
            cand["score"] += item["match"]

    if not candidates:
        return []

    pool = sorted(candidates.values(), key=lambda c: -c["score"])[:POOL_SIZE]
    top = pool[0]["score"] or 1.0

    async def tags_of(artist: str):
        async with sem:
            try:
                return artist.strip().lower(), await lastfm.top_tags(client, artist)
            except lastfm.LastfmError:
                return artist.strip().lower(), []

    # Genre profile of the playlist: how many of the analysed songs' artists carry each tag.
    wanted = {s["artist"] for s in seeds} | {c["artist"] for c in pool}
    artist_tags = dict(await asyncio.gather(*(tags_of(a) for a in wanted)))
    profile: Counter = Counter()
    for seed in seeds:
        profile.update(set(artist_tags.get(seed["artist"].strip().lower(), [])))

    listener_counts: dict[str, int] = {}
    popularity: dict[str, float] = {}
    if novelty > 0:
        async def pop(cand: dict):
            async with sem:
                try:
                    return cand, await lastfm.listeners(client, cand["artist"], cand["title"])
                except lastfm.LastfmError:
                    return cand, 0

        counts = await asyncio.gather(*(pop(c) for c in pool))
        biggest = max((n for _, n in counts), default=0) or 1
        for cand, n in counts:
            key = track_key(cand["artist"], cand["title"])
            listener_counts[key] = n
            popularity[key] = math.log10(n + 1) / math.log10(biggest + 1)

    ranked = []
    for cand in pool:
        key = track_key(cand["artist"], cand["title"])
        pop_value = popularity.get(key)
        same_artist = cand["artist"].strip().lower() in owned_artists
        shared = [
            (tag, profile[tag])
            for tag in artist_tags.get(cand["artist"].strip().lower(), [])
            if profile[tag] >= 2
        ][:2]
        final = cand["score"] / top
        final *= 1 + TAG_BONUS * (len(shared) / 2)
        if pop_value is not None:
            final *= 1 - novelty * 0.9 * pop_value
        if not same_artist:
            final *= 1 + novelty * 0.3
        ranked.append((final, cand, key, shared, same_artist))
    ranked.sort(key=lambda r: -r[0])

    picked, per_artist = [], defaultdict(int)
    for final, cand, key, shared, same_artist in ranked:
        artist = cand["artist"].strip().lower()
        if per_artist[artist] >= MAX_PER_ARTIST:
            continue
        per_artist[artist] += 1
        reasons = _reasons(
            cand, len(seeds), shared, listener_counts.get(key), popularity.get(key), same_artist
        )
        picked.append(
            {
                "artist": cand["artist"],
                "title": cand["title"],
                "reasons": reasons,
                "score": round(final, 3),
            }
        )
        if len(picked) >= count:
            break
    return picked
