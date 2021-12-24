#!/usr/bin/env python3

import asyncio
import base64
import datetime
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, List, Optional, Set

import aiohttp

from external import external
from playlist_id import PlaylistID
from playlist_types import Album, Artist, Playlist, Track

logger: logging.Logger = logging.getLogger(__name__)


class FailedToGetAccessTokenError(Exception):
    pass


class FailedToGetPlaylistError(Exception):
    pass


class FailedToGetTracksError(Exception):
    pass


class RetryBudgetExceededError(Exception):
    pass


class Spotify:

    BASE_URL = "https://api.spotify.com/v1/playlists/"

    def __init__(self, access_token: str) -> None:
        headers = {"Authorization": f"Bearer {access_token}"}
        self._session: aiohttp.ClientSession = self._get_session(headers=headers)
        # Handle rate limiting by retrying
        self._retry_budget_seconds: int = 30

    @asynccontextmanager
    async def _get_with_retry(
        self, href: str
    ) -> AsyncIterator[aiohttp.client_reqrep.ClientResponse]:
        while True:
            async with self._session.get(href) as response:
                if response.status == 429:
                    # Add an extra second, just to be safe
                    # https://stackoverflow.com/a/30557896/3176152
                    backoff_seconds = int(response.headers["Retry-After"]) + 1
                    reason = "Rate limited"
                elif response.status in [500, 502, 504]:
                    backoff_seconds = 1
                    reason = "Server error"
                else:
                    yield response
                    return
                self._retry_budget_seconds -= backoff_seconds
                if self._retry_budget_seconds <= 0:
                    raise RetryBudgetExceededError("Retry budget exceeded")
                else:
                    logger.warning(f"{reason}, will retry after {backoff_seconds}s")
                    await self._sleep(backoff_seconds)

    async def shutdown(self) -> None:
        await self._session.close()
        # Sleep to allow underlying connections to close
        # https://docs.aiohttp.org/en/stable/client_advanced.html#graceful-shutdown
        await self._sleep(0)

    async def get_spotify_user_playlist_ids(self) -> Set[PlaylistID]:
        logger.info("Fetching @spotify playlist IDs")
        playlist_ids: Set[PlaylistID] = set()
        href = "https://api.spotify.com/v1/users/spotify/playlists?limit=50"
        while href:
            async with self._get_with_retry(href) as response:
                data = await response.json(content_type=None)
            playlist_ids |= {PlaylistID(item["id"]) for item in data["items"]}
            href = data.get("next")
        return playlist_ids

    async def get_featured_playlist_ids(self) -> Set[PlaylistID]:
        logger.info("Fetching featured playlist IDs")
        playlist_ids: Set[PlaylistID] = set()
        href = "https://api.spotify.com/v1/browse/featured-playlists?limit=50"
        while href:
            async with self._get_with_retry(href) as response:
                data = await response.json(content_type=None)
            playlist_ids |= {
                PlaylistID(item["id"]) for item in data["playlists"]["items"]
            }
            href = data.get("next")
        return playlist_ids

    async def get_playlist(
        self, playlist_id: PlaylistID, aliases: Dict[PlaylistID, str]
    ) -> Playlist:
        playlist_href = self._get_playlist_href(playlist_id)
        async with self._get_with_retry(playlist_href) as response:
            data = await response.json(content_type=None)

        if not isinstance(data, dict):
            raise FailedToGetPlaylistError(f"Invalid response: {data}")
        if not data:
            raise FailedToGetPlaylistError(f"Empty response: {data}")

        error = data.get("error")
        if error:
            # status 401 = invalid access token
            # status 403 = private playlist
            # status 404 = invalid playlist
            raise FailedToGetPlaylistError(f"Failed to get playlist: {error}")

        # If the playlist has an alias, use it
        if playlist_id in aliases:
            name = aliases[playlist_id]
        else:
            name = data["name"]

        # Make sure the name is nonemty and contains readable characters
        if (not name) or name.isspace():
            raise FailedToGetPlaylistError(f"Empty playlist name: {playlist_id}")

        description = data["description"]
        url = self._get_url(data["external_urls"])
        tracks = await self._get_tracks(playlist_id)
        snapshot_id = data["snapshot_id"]
        return Playlist(
            url=url,
            name=name,
            description=description,
            tracks=tracks,
            snapshot_id=snapshot_id,
        )

    async def _get_tracks(self, playlist_id: PlaylistID) -> List[Track]:
        tracks = []
        tracks_href = self._get_tracks_href(playlist_id)

        while tracks_href:
            async with self._get_with_retry(tracks_href) as response:
                data = await response.json(content_type=None)

            if not isinstance(data, dict):
                raise FailedToGetTracksError(f"Invalid response: {data}")
            if not data:
                raise FailedToGetTracksError(f"Empty response: {data}")

            error = data.get("error")
            if error:
                raise FailedToGetTracksError(f"Failed to get tracks: {error}")

            for item in data["items"]:
                track = item["track"]
                if not track:
                    continue

                track_url = self._get_url(track["external_urls"])
                if not track_url:
                    logger.warning("Skipping track with empty URL")
                    continue

                track_name = track["name"]
                if not track_name:
                    logger.warning(f"Empty track name: {track_url}")

                album_url = self._get_url(track["album"]["external_urls"])
                album_name = track["album"]["name"]
                if not album_name:
                    logger.warning(f"Empty album name: {album_url}")

                artists = []
                for artist in track["artists"]:
                    artist_url = self._get_url(artist["external_urls"])
                    artist_name = artist["name"]
                    if not artist_name:
                        logger.warning(f"Empty artist name: {artist_url}")
                    artists.append(Artist(url=artist_url, name=artist_name))

                if not artists:
                    logger.warning(f"Empty track artists: {track_url}")

                duration_ms = track["duration_ms"]

                added_at_string = item["added_at"]
                if added_at_string and added_at_string != "1970-01-01T00:00:00Z":
                    added_at = datetime.datetime.strptime(
                        added_at_string, "%Y-%m-%dT%H:%M:%SZ"
                    )
                else:
                    added_at = None

                tracks.append(
                    Track(
                        url=track_url,
                        name=track_name,
                        album=Album(
                            url=album_url,
                            name=album_name,
                        ),
                        artists=artists,
                        duration_ms=duration_ms,
                        added_at=added_at,
                    )
                )

            tracks_href = data["next"]

        return tracks

    @classmethod
    def _get_url(cls, external_urls: Dict[str, str]) -> str:
        return external_urls.get("spotify") or ""

    @classmethod
    def _get_playlist_href(cls, playlist_id: PlaylistID) -> str:
        rest = "{}?fields=external_urls,name,description,snapshot_id"
        template = cls.BASE_URL + rest
        return template.format(playlist_id)

    @classmethod
    def _get_tracks_href(cls, playlist_id: PlaylistID) -> str:
        rest = (
            "{}/tracks?fields=items(added_at,track(id,external_urls,"
            "duration_ms,name,album(external_urls,name),artists)),next"
        )
        template = cls.BASE_URL + rest
        return template.format(playlist_id)

    @classmethod
    async def get_access_token(cls, client_id: str, client_secret: str) -> str:
        joined = f"{client_id}:{client_secret}"
        encoded = base64.b64encode(joined.encode()).decode()
        async with cls._get_session() as session:
            async with session.post(
                url="https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {encoded}"},
            ) as response:
                try:
                    data = await response.json()
                except Exception as e:
                    raise FailedToGetAccessTokenError from e

        error = data.get("error")
        if error:
            raise FailedToGetAccessTokenError(f"Failed to get access token: {error}")

        access_token = data.get("access_token")
        if not access_token:
            raise FailedToGetAccessTokenError(f"Invalid access token: {access_token}")

        token_type = data.get("token_type")
        if token_type != "Bearer":
            raise FailedToGetAccessTokenError(f"Invalid token type: {token_type}")

        return access_token

    @classmethod
    @external
    def _get_session(
        cls, headers: Optional[Dict[str, str]] = None
    ) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(headers=headers)

    @classmethod
    @external
    async def _sleep(cls, seconds: float) -> None:
        await asyncio.sleep(seconds)
