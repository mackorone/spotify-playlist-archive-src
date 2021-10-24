#!/usr/bin/env python3


class InvalidPlaylistIDError(Exception):
    pass


class PlaylistID(str):
    def __new__(self, playlist_id: str) -> str:
        if not playlist_id.isalnum():
            raise InvalidPlaylistIDError(playlist_id)
        return str.__new__(str, playlist_id)
