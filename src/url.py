#!/usr/bin/env python3

from playlist_id import PlaylistID


class URL:
    BASE = "/playlists"
    HISTORY_BASE = (
        "https://github.githistory.xyz/mackorone/spotify-playlist-archive/"
        "blob/main/playlists"
    )

    @classmethod
    def plain_history(cls, playlist_id: PlaylistID) -> str:
        return cls.HISTORY_BASE + f"/plain/{playlist_id}"

    @classmethod
    def plain(cls, playlist_id: PlaylistID) -> str:
        return cls.BASE + f"/plain/{playlist_id}"

    @classmethod
    def pretty(cls, playlist_id: PlaylistID) -> str:
        return cls.BASE + f"/pretty/{playlist_id}.md"

    @classmethod
    def cumulative(cls, playlist_id: PlaylistID) -> str:
        return cls.BASE + f"/cumulative/{playlist_id}.md"
