#!/usr/bin/env python3

import base64
import json
import logging
from typing import Dict, List

import aiohttp

from plants.external import external
from playlist_id import PlaylistID

logger: logging.Logger = logging.getLogger(__name__)


class UnsupportedEncodingError(Exception):
    pass


class GitHub:
    @classmethod
    async def get_published_cumulative_playlists(
        cls,
    ) -> Dict[PlaylistID, List[PlaylistID]]:
        async with cls._get_session() as session:
            url = (
                "https://api.github.com/repos/"
                "mackorone/spotify-playlist-publisher/"
                "contents/playlists.json"
            )
            async with session.get(url) as response:
                data = await response.json()
        encoding = data["encoding"]
        if encoding != "base64":
            raise UnsupportedEncodingError(f"Unsupported encoding: {encoding}")
        content = base64.b64decode(data["content"])
        playlists = json.loads(content)
        published_cumulative_playlists: Dict[PlaylistID, List[PlaylistID]] = {}
        for playlist in playlists["mappings"]:
            scraped_playlist_id = PlaylistID(playlist["scraped_playlist_id"])
            published_cumulative_playlists[scraped_playlist_id] = [
                PlaylistID(playlist_id)
                for playlist_id in playlist["published_playlist_ids"]
            ]
        return published_cumulative_playlists

    @classmethod
    @external
    def _get_session(cls) -> aiohttp.ClientSession:
        return aiohttp.ClientSession()
