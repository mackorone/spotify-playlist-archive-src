#!/usr/bin/env python3


class URL:

    BASE = "/playlists"
    HISTORY_BASE = (
        "https://github.githistory.xyz/mackorone/spotify-playlist-archive/"
        "blob/main/playlists"
    )

    @classmethod
    def plain_history(cls, playlist_id: str) -> str:
        url = cls.HISTORY_BASE + "/plain/{}".format(playlist_id)
        return cls._sanitize(url)

    @classmethod
    def plain(cls, playlist_id: str) -> str:
        url = cls.BASE + "/plain/{}".format(playlist_id)
        return cls._sanitize(url)

    @classmethod
    def pretty(cls, playlist_name: str) -> str:
        url = cls.BASE + "/pretty/{}.md".format(playlist_name)
        return cls._sanitize(url)

    @classmethod
    def cumulative(cls, playlist_name: str) -> str:
        url = cls.BASE + "/cumulative/{}.md".format(playlist_name)
        return cls._sanitize(url)

    @classmethod
    def _sanitize(cls, url: str) -> str:
        return url.replace(" ", "%20")
