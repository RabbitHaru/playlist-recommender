"""Music platform adapters.

A provider reads a playlist from one platform and can link a recommended song back to
that platform. Recommendation itself is platform independent (see recommender.py), so a
new platform only needs a class with the same three members as SpotifyProvider.

Only official, ToS-compliant APIs belong here. Unofficial scrapers (e.g. ytmusicapi)
are deliberately not used because this app may be deployed publicly.
"""


class ProviderError(Exception):
    """User-facing error raised by a provider."""


def get_providers() -> list:
    from .spotify import SpotifyProvider
    from .youtube import YouTubeProvider

    # Order matters: the first provider is also used to link recommended songs.
    return [SpotifyProvider(), YouTubeProvider()]


def pick_provider(text: str):
    for provider in get_providers():
        if provider.can_handle(text):
            return provider
    raise ProviderError("지원하지 않는 링크예요. YouTube(Music)·Spotify 플레이리스트 링크나 곡 목록을 붙여넣어 주세요.")
