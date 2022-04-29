#!/usr/bin/env python3


class InvalidPlaylistIDError(Exception):
    pass


class PlaylistID(str):
    def __new__(cls, playlist_id: str) -> str:
        if not playlist_id.isalnum():
            raise InvalidPlaylistIDError(playlist_id)
        return super().__new__(cls, playlist_id)
