#!/usr/bin/env python3

import asyncio
import base64
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, NamedTuple, Optional, Sequence

import aiohttp

from external import external
from playlist_id import PlaylistID

logger: logging.Logger = logging.getLogger(__name__)


class Artist(NamedTuple):
    url: str
    name: str


class Album(NamedTuple):
    url: str
    name: str


class Track(NamedTuple):
    url: str
    name: str
    album: Album
    artists: Sequence[Artist]
    duration_ms: int


class Playlist(NamedTuple):
    url: str
    name: str
    description: str
    tracks: Sequence[Track]


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

        # Playlist names can't have "/" or "\" so use " " instead
        name = name.replace("/", " ")
        name = name.replace("\\", " ")
        # Windows filenames can't have ":" so use " -" instead
        name = name.replace(":", " -")
        # Windows filenames can't have "|" so use "-" instead
        name = name.replace("|", "-")
        # Windows filenames can't have "?" so just remove them
        name = name.replace("?", "")
        # Playlist names shouldn't have enclosing spaces or dots
        name = name.strip(" .")

        if not name:
            raise FailedToGetPlaylistError(f"Empty playlist name: {playlist_id}")

        description = data["description"]
        url = self._get_url(data["external_urls"])
        tracks = await self._get_tracks(playlist_id)
        return Playlist(url=url, name=name, description=description, tracks=tracks)

    async def _get_tracks(self, playlist_id: PlaylistID) -> Sequence[Track]:
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
                raise FailedToGetTracksError("Failed to get tracks: {}".format(error))

            for item in data["items"]:
                track = item["track"]
                if not track:
                    continue

                url = self._get_url(track["external_urls"])
                duration_ms = track["duration_ms"]

                name = track["name"]
                album = track["album"]["name"]

                if not name:
                    logger.warning("Empty track name: {}".format(url))
                    name = "<MISSING>"
                if not album:
                    logger.warning("Empty track album: {}".format(url))
                    album = "<MISSING>"

                artists = []
                for artist in track["artists"]:
                    artists.append(
                        Artist(
                            url=self._get_url(artist["external_urls"]),
                            name=artist["name"],
                        )
                    )

                if not artists:
                    logger.warning("Empty track artists: {}".format(url))

                tracks.append(
                    Track(
                        url=url,
                        name=name,
                        album=Album(
                            url=self._get_url(track["album"]["external_urls"]),
                            name=album,
                        ),
                        artists=artists,
                        duration_ms=duration_ms,
                    )
                )

            tracks_href = data["next"]

        return tracks

    @classmethod
    def _get_url(cls, external_urls: Dict[str, str]) -> str:
        return external_urls.get("spotify") or ""

    @classmethod
    def _get_playlist_href(cls, playlist_id: PlaylistID) -> str:
        rest = "{}?fields=external_urls,name,description"
        template = cls.BASE_URL + rest
        return template.format(playlist_id)

    @classmethod
    def _get_tracks_href(cls, playlist_id: PlaylistID) -> str:
        rest = (
            "{}/tracks?fields=next,items.track(id,external_urls,"
            "duration_ms,name,album(external_urls,name),artists)"
        )
        template = cls.BASE_URL + rest
        return template.format(playlist_id)

    @classmethod
    async def get_access_token(cls, client_id: str, client_secret: str) -> str:
        joined = "{}:{}".format(client_id, client_secret)
        encoded = base64.b64encode(joined.encode()).decode()
        async with cls._get_session() as session:
            async with session.post(
                url="https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                headers={"Authorization": "Basic {}".format(encoded)},
            ) as response:
                try:
                    data = await response.json()
                except Exception as e:
                    raise FailedToGetAccessTokenError from e

        error = data.get("error")
        if error:
            raise FailedToGetAccessTokenError(
                "Failed to get access token: {}".format(error)
            )

        access_token = data.get("access_token")
        if not access_token:
            raise FailedToGetAccessTokenError(
                "Invalid access token: {}".format(access_token)
            )

        token_type = data.get("token_type")
        if token_type != "Bearer":
            raise FailedToGetAccessTokenError(
                "Invalid token type: {}".format(token_type)
            )

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
