#!/usr/bin/env python3

import asyncio
import base64
import datetime
import logging
from typing import Any, Dict, List, Optional, Set

import aiohttp

from external import external
from playlist_id import PlaylistID
from playlist_types import Album, Artist, Owner, Playlist, Track

logger: logging.Logger = logging.getLogger(__name__)


class InvalidDataError(Exception):
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

    async def _get_with_retry(self, href: str) -> Dict[str, Any]:
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
                    data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        raise InvalidDataError(f"Invalid response: {data}")
                    if not data:
                        raise InvalidDataError(f"Empty response: {data}")
                    if "error" in data:
                        raise InvalidDataError(f"Error response: {data}")
                    return data
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
            data = await self._get_with_retry(href)
            playlist_ids |= self._extract_playlist_ids(data)
            href = data.get("next")
        return playlist_ids

    async def get_featured_playlist_ids(self) -> Set[PlaylistID]:
        logger.info("Fetching featured playlist IDs")
        playlist_ids: Set[PlaylistID] = set()
        href = "https://api.spotify.com/v1/browse/featured-playlists?limit=50"
        while href:
            data = await self._get_with_retry(href)
            playlists = data.get("playlists")
            if not isinstance(playlists, dict):
                raise InvalidDataError(f"Invalid playlists: {playlists}")
            playlist_ids |= self._extract_playlist_ids(playlists)
            href = data.get("next")
        return playlist_ids

    @classmethod
    def _extract_playlist_ids(cls, data: Dict[str, Any]) -> Set[PlaylistID]:
        playlist_ids: Set[PlaylistID] = set()
        items = data.get("items")
        if not isinstance(items, list):
            raise InvalidDataError(f"Invalid items: {items}")
        for item in items:
            if not isinstance(item, dict):
                raise InvalidDataError(f"Invalid item: {item}")
            playlist_id = item.get("id")
            if not isinstance(playlist_id, str):
                raise InvalidDataError(f"Invalid playlist ID: {playlist_id}")
            playlist_ids.add(PlaylistID(playlist_id))
        return playlist_ids

    async def get_playlist(
        self, playlist_id: PlaylistID, aliases: Dict[PlaylistID, str]
    ) -> Playlist:
        href = self._get_playlist_href(playlist_id)
        data = await self._get_with_retry(href)

        # If the playlist has an alias, use it
        if playlist_id in aliases:
            name = aliases[playlist_id]
        else:
            name = data.get("name")

        if not isinstance(name, str):
            raise InvalidDataError(f"Invalid playlist name: {name}")
        if (not name) or name.isspace():
            raise InvalidDataError(f"Empty playlist name: {repr(name)}")

        description = data.get("description")
        if not isinstance(description, str):
            raise InvalidDataError(f"Invalid description: {description}")

        playlist_urls = data.get("external_urls")
        if not isinstance(playlist_urls, dict):
            raise InvalidDataError(f"Invalid playlist URLs: {playlist_urls}")
        playlist_url = self._get_url(playlist_urls)
        if not isinstance(playlist_url, str):
            raise InvalidDataError(f"Invalid playlist URL: {playlist_url}")

        tracks = await self._get_tracks(playlist_id)

        snapshot_id = data.get("snapshot_id")
        if not isinstance(snapshot_id, str):
            raise InvalidDataError(f"Invalid snapshot ID: {snapshot_id}")

        followers = data.get("followers")
        if not isinstance(followers, dict):
            raise InvalidDataError(f"Invalid followers: {followers}")
        num_followers = followers.get("total")
        if not isinstance(num_followers, int):
            raise InvalidDataError(f"Invalid num followers: {num_followers}")

        owner = data.get("owner")
        if not isinstance(owner, dict):
            raise InvalidDataError(f"Invalid owner: {owner}")
        owner_urls = owner.get("external_urls")
        if not isinstance(owner_urls, dict):
            raise InvalidDataError(f"Invalid owner URLs: {owner_urls}")
        owner_url = self._get_url(owner_urls)
        if not isinstance(owner_url, str):
            raise InvalidDataError(f"Invalid owner URL: {owner_url}")
        owner_name = owner.get("display_name")
        if not isinstance(owner_name, str):
            raise InvalidDataError(f"Invalid owner name: {owner_name}")
        if not owner_name:
            logger.warning(f"Empty owner name: {owner_url}")

        return Playlist(
            url=playlist_url,
            name=name,
            description=description,
            tracks=tracks,
            snapshot_id=snapshot_id,
            num_followers=num_followers,
            owner=Owner(
                url=owner_url,
                name=owner_name,
            ),
        )

    async def _get_tracks(self, playlist_id: PlaylistID) -> List[Track]:
        tracks = []
        href = self._get_tracks_href(playlist_id)

        while href:
            data = await self._get_with_retry(href)

            items = data.get("items")
            if not isinstance(items, list):
                raise InvalidDataError(f"Invalid items: {items}")

            for item in items:
                if not isinstance(item, dict):
                    raise InvalidDataError(f"Invalid item: {item}")

                track = item.get("track")
                if not isinstance(track, (dict, type(None))):
                    raise InvalidDataError(f"Invalid track: {track}")
                if not track:
                    continue

                track_urls = track.get("external_urls")
                if not isinstance(track_urls, dict):
                    raise InvalidDataError(f"Invalid track URLs: {track_urls}")
                track_url = self._get_url(track_urls)
                if not isinstance(track_url, str):
                    raise InvalidDataError(f"Invalid track URL: {track_url}")
                if not track_url:
                    logger.warning("Skipping track with empty URL")
                    continue

                track_name = track.get("name")
                if not isinstance(track_name, str):
                    raise InvalidDataError(f"Invalid track name: {track_name}")
                if not track_name:
                    logger.warning(f"Empty track name: {track_url}")

                album = track.get("album")
                if not isinstance(album, dict):
                    raise InvalidDataError(f"Invalid album: {album}")
                album_urls = album.get("external_urls")
                if not isinstance(album_urls, dict):
                    raise InvalidDataError(f"Invalid album URLs: {album_urls}")
                album_url = self._get_url(album_urls)
                if not isinstance(album_url, str):
                    raise InvalidDataError(f"Invalid album URL: {album_url}")
                album_name = album.get("name")
                if not isinstance(album_name, str):
                    raise InvalidDataError(f"Invalid album name: {album_name}")
                if not album_name:
                    logger.warning(f"Empty album name: {album_url}")

                artists = track.get("artists")
                if not isinstance(artists, list):
                    raise InvalidDataError(f"Invalid artists: {artists}")

                artist_objs = []
                for artist in artists:
                    if not isinstance(artist, dict):
                        raise InvalidDataError(f"Invalid artist: {artist}")
                    artist_urls = artist.get("external_urls")
                    if not isinstance(artist_urls, dict):
                        raise InvalidDataError(f"Invalid artist URLs: {artist_urls}")
                    artist_url = self._get_url(artist_urls)
                    if not isinstance(artist_url, str):
                        raise InvalidDataError(f"Invalid artist URL: {artist_url}")
                    artist_name = artist.get("name")
                    if not isinstance(artist_name, str):
                        raise InvalidDataError(f"Invalid artist name: {artist_name}")
                    if not artist_name:
                        logger.warning(f"Empty artist name: {artist_url}")
                    artist_objs.append(Artist(url=artist_url, name=artist_name))

                if not artist_objs:
                    logger.warning(f"Empty track artists: {track_url}")

                duration_ms = track.get("duration_ms")
                if not isinstance(duration_ms, int):
                    raise InvalidDataError(f"Invalid duration: {duration_ms}")

                added_at_string = item.get("added_at")
                if not isinstance(added_at_string, (str, type(None))):
                    raise InvalidDataError(f"Invalid added at: {added_at_string}")
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
                        artists=artist_objs,
                        duration_ms=duration_ms,
                        added_at=added_at,
                    )
                )

            href = data.get("next")

        return tracks

    @classmethod
    def _get_url(cls, external_urls: Dict[str, str]) -> str:
        return external_urls.get("spotify") or ""

    @classmethod
    def _get_playlist_href(cls, playlist_id: PlaylistID) -> str:
        rest = (
            "{}?fields=external_urls,name,description,snapshot_id,"
            "owner(display_name,external_urls),followers.total"
        )
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
                    raise InvalidDataError from e

        error = data.get("error")
        if error:
            raise InvalidDataError(f"Failed to get access token: {error}")

        access_token = data.get("access_token")
        if not access_token:
            raise InvalidDataError(f"Invalid access token: {access_token}")

        token_type = data.get("token_type")
        if token_type != "Bearer":
            raise InvalidDataError(f"Invalid token type: {token_type}")

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
