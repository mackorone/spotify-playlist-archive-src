#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import base64
import dataclasses
import datetime
import enum
import logging
from types import TracebackType
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Set,
    Type,
    TypeVar,
)

import aiohttp

from alias import Alias
from plants.cache import Cache, NoCache
from plants.external import external
from playlist_id import PlaylistID
from playlist_types import Album, Artist, Owner, Playlist, Track

logger: logging.Logger = logging.getLogger(__name__)


T = TypeVar("T")


#
# Errors that propagate to the caller
#


class InvalidDataError(Exception):
    pass


class RequestFailedError(Exception):
    """Unhandled client error (4xx)"""


class ResourceNotFoundError(Exception):
    pass


class RetryBudgetExceededError(Exception):
    pass


#
# Errors that are transparently retried
#


@dataclasses.dataclass
class RetryableError(Exception):
    message: str
    sleep_seconds: float = 1
    refresh_access_token: bool = False


class InvalidAccessTokenError(Exception):
    pass


@dataclasses.dataclass
class RateLimitedError(Exception):
    retry_after: int


@dataclasses.dataclass
class ServerError(Exception):
    status: int


class UnexpectedEmptyResponseError(Exception):
    pass


class HttpMethod(enum.Enum):
    GET = enum.auto()
    PUT = enum.auto()
    POST = enum.auto()
    DELETE = enum.auto()


class ResponseType(enum.Enum):
    JSON = enum.auto()
    EMPTY = enum.auto()


# What to do if a dictionary key is missing or the value is null
class IfNull(enum.Enum):
    RAISE = enum.auto()
    COALESCE = enum.auto()


class Spotify:
    BASE_URL = "https://api.spotify.com/v1/"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        cache: Optional[Cache[str, Dict[str, Any]]] = None,
    ) -> None:
        self._client_id: str = client_id
        self._client_secret: str = client_secret
        self._cache: Cache[str, Dict[str, Any]] = cache or NoCache()
        self._access_token: Optional[str] = None
        self._retry_budget_seconds: float = 300
        self._session: aiohttp.ClientSession = self._get_session()

    async def __aenter__(self) -> Spotify:
        return self

    async def __aexit__(
        self,
        exc_type: Type[BaseException],
        exc_value: BaseException,
        traceback: TracebackType,
    ) -> None:
        await self.shutdown()

    async def _get_with_retry(
        self, url: str, *, max_spend_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        async def _get(url: str) -> Dict[str, Any]:
            return await self._make_retryable_request(
                method=HttpMethod.GET,
                url=url,
                max_spend_seconds=max_spend_seconds,
            )

        return await self._cache.get(key=url, func=_get)

    async def _make_retryable_request(
        self,
        method: HttpMethod,
        url: str,
        json: Optional[Mapping[str, Any]] = None,
        *,
        expected_response_type: ResponseType = ResponseType.JSON,
        max_spend_seconds: Optional[float] = None,
        raise_if_request_fails: bool = True,
    ) -> Dict[str, Any]:
        if max_spend_seconds is None:
            max_spend_seconds = self._retry_budget_seconds

        while True:
            # Lazily fetch access token
            if not self._access_token:
                logger.info("Getting new access token")
                self._access_token = await self.get_access_token(
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                )
                logger.info("Got new access token")

            # Choose the correct method
            func = {
                HttpMethod.GET: self._session.get,
                HttpMethod.PUT: self._session.put,
                HttpMethod.POST: self._session.post,
                HttpMethod.DELETE: self._session.delete,
            }[method]

            # Prepare the request
            aenter_to_send_request = func(
                url=url,
                json=json,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )

            # Make a retryable attempt
            try:
                return await self._send_request_and_coerce_errors(
                    aenter_to_send_request,
                    expected_response_type,
                    raise_if_request_fails,
                )
            except RetryableError as e:
                if e.refresh_access_token:
                    self._access_token = None
                self._retry_budget_seconds -= e.sleep_seconds
                if self._retry_budget_seconds <= 0:
                    raise RetryBudgetExceededError("Overall retry budget exceeded")
                max_spend_seconds -= e.sleep_seconds
                if max_spend_seconds <= 0:
                    raise RetryBudgetExceededError("Request retry budget exceeded")
                logger.warning(f"{e.message}, will retry after {e.sleep_seconds}s")
                await self._sleep(e.sleep_seconds)

    @classmethod
    async def _send_request_and_coerce_errors(
        cls,
        aenter_to_send_request: aiohttp.client._RequestContextManager,
        expected_response_type: ResponseType,
        raise_if_request_fails: bool,
    ) -> Dict[str, Any]:
        """Catch retryable errors and coerce them into uniform type"""
        try:
            return await cls._send_request(
                aenter_to_send_request,
                expected_response_type,
                raise_if_request_fails,
            )
        except InvalidAccessTokenError:
            raise RetryableError(
                message="Invalid access token",
                refresh_access_token=True,
            )
        except RateLimitedError as e:
            raise RetryableError(
                message="Rate limited",
                # Add an extra second, just to be safe
                # https://stackoverflow.com/a/30557896/3176152
                sleep_seconds=e.retry_after + 1,
            )
        except ServerError as e:
            raise RetryableError(f"Server error ({e.status})")
        except aiohttp.ContentTypeError:
            raise RetryableError("Invalid response (invalid JSON)")
        except UnexpectedEmptyResponseError:
            raise RetryableError("Invalid response (empty JSON)")
        except aiohttp.client_exceptions.ClientConnectionError:
            raise RetryableError("Connection problem")
        except asyncio.exceptions.TimeoutError:
            raise RetryableError("Asyncio timeout")

    @classmethod
    async def _send_request(
        cls,
        aenter_to_send_request: aiohttp.client._RequestContextManager,
        expected_response_type: ResponseType,
        raise_if_request_fails: bool,
    ) -> Dict[str, Any]:
        async with aenter_to_send_request as response:
            status = response.status

            # Straightforward retryable errors, no error info needed
            if status == 401:
                raise InvalidAccessTokenError()
            if status == 429:
                retry_after = int(response.headers["Retry-After"])
                raise RateLimitedError(retry_after=retry_after)
            if status >= 500:
                raise ServerError(status=status)

            # Sometimes Spotify just returns empty data
            data = None
            if expected_response_type == ResponseType.JSON:
                data = await response.json()
                if not data:
                    raise UnexpectedEmptyResponseError()

            # Handle unretryable client errors
            if status >= 400:
                error = (data or {}).get("error") or {}
                error_message = error.get("message")
                error_info = f"{error_message} ({status})"
                if status == 404:
                    raise ResourceNotFoundError(error_info)
                if raise_if_request_fails:
                    raise RequestFailedError(error_info)

            # Return data from "successful" request
            if expected_response_type == ResponseType.JSON:
                return data
            assert expected_response_type == ResponseType.EMPTY
            return {}

    async def shutdown(self) -> None:
        await self._session.close()
        # Sleep to allow underlying connections to close
        # https://docs.aiohttp.org/en/stable/client_advanced.html#graceful-shutdown
        await self._sleep(0)

    async def get_spotify_user_playlist_ids(self) -> Set[PlaylistID]:
        logger.info("Fetching @spotify playlist IDs")
        playlist_ids: Set[PlaylistID] = set()
        href = self.BASE_URL + "users/spotify/playlists?limit=50"
        while href:
            data = await self._get_with_retry(href)
            playlist_ids |= {PlaylistID(x) for x in self._extract_ids(data)}
            href = data.get("next")
        return playlist_ids

    async def get_featured_playlist_ids(self) -> Set[PlaylistID]:
        logger.info("Fetching featured playlist IDs")
        playlist_ids: Set[PlaylistID] = set()
        href = self.BASE_URL + "browse/featured-playlists?limit=50"
        while href:
            data = await self._get_with_retry(href)
            playlists = self._extract(data, "playlists", dict, IfNull.COALESCE)
            if not playlists:
                href = None
                continue
            playlist_ids |= {PlaylistID(x) for x in self._extract_ids(playlists)}
            href = playlists.get("next")
        return playlist_ids

    async def get_category_playlist_ids(self) -> Set[PlaylistID]:
        logger.info("Fetching category playlist IDs")
        playlist_ids: Set[PlaylistID] = set()
        category_ids: Set[str] = set()
        href = self.BASE_URL + "browse/categories?limit=50"
        while href:
            data = await self._get_with_retry(href, max_spend_seconds=3)
            categories = self._extract(data, "categories", dict, IfNull.COALESCE)
            if not categories:
                href = None
                continue
            category_ids |= self._extract_ids(categories)
            href = categories.get("next")
        for category in sorted(category_ids):
            href = self.BASE_URL + f"browse/categories/{category}/playlists?limit=50"
            while href:
                try:
                    data = await self._get_with_retry(href, max_spend_seconds=3)
                except ResourceNotFoundError:
                    # Weirdly, some categories return 404
                    break
                playlists = self._extract(data, "playlists", dict, IfNull.COALESCE)
                if not playlists:
                    href = None
                    continue
                playlist_ids |= {PlaylistID(x) for x in self._extract_ids(playlists)}
                href = playlists.get("next")
        return playlist_ids

    @classmethod
    def _extract_ids(cls, data: Dict[str, Any]) -> Set[str]:
        ids: Set[str] = set()
        items = cls._extract(data, "items", list, IfNull.COALESCE)
        for item in items:
            if not isinstance(item, dict):
                continue
            id_ = cls._extract(item, "id", str, IfNull.COALESCE)
            if not id_:
                continue
            ids.add(id_)
        return ids

    async def get_playlist(
        self, playlist_id: PlaylistID, *, alias: Optional[Alias]
    ) -> Playlist:
        href = self._get_playlist_href(playlist_id)
        data = await self._get_with_retry(href)

        playlist_urls = self._extract(data, "external_urls", dict, IfNull.RAISE)
        playlist_url = self._extract(playlist_urls, "spotify", str, IfNull.COALESCE)

        if alias:
            name = alias
        else:
            name = self._extract(data, "name", str, IfNull.RAISE)
        if not name.strip():
            raise InvalidDataError(f"Empty playlist name: {repr(name)}")

        followers = self._extract(data, "followers", dict, IfNull.RAISE)
        followers_total = followers.get("total")
        if followers_total is None:
            logger.warning(f"Null followers total: {playlist_id}")
        if not isinstance(followers_total, int):
            raise InvalidDataError(f"Invalid followers total: {followers_total}")

        owner = self._extract(data, "owner", dict, IfNull.RAISE)
        owner_urls = self._extract(owner, "external_urls", dict, IfNull.RAISE)
        owner_url = self._extract(owner_urls, "spotify", str, IfNull.COALESCE)
        owner_name = self._extract(owner, "display_name", str, IfNull.COALESCE)
        if not owner_name:
            logger.warning(f"Empty owner name: {owner_url}")

        return Playlist(
            url=playlist_url,
            original_name=name,
            # When fetched, playlists are presumed to have unique names. Later
            # on, if duplicates are discovered, their unique names get updated
            # so they can be differentiated. It's bit hacky, but it's easier
            # than defining separate structs for playlists fetched from Spotify
            # and playlists read from JSON.
            unique_name=name,
            description=self._extract(data, "description", str, IfNull.RAISE),
            tracks=await self._get_tracks(playlist_id),
            snapshot_id=self._extract(data, "snapshot_id", str, IfNull.RAISE),
            num_followers=followers_total,
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
            # If tracks cannot be fetched, this playlist is broken and should
            # be treated the same as if all data was unfetchable
            try:
                items = self._extract(data, "items", list, IfNull.RAISE)
            except InvalidDataError:
                raise ResourceNotFoundError(
                    f"Failed to get tracks for playlist {playlist_id}"
                )
            for item in items:
                if not isinstance(item, dict):
                    raise InvalidDataError(f"Invalid item: {item}")

                track = self._extract(item, "track", dict, IfNull.COALESCE)
                if not track:
                    continue
                track_urls = self._extract(track, "external_urls", dict, IfNull.RAISE)
                track_url = self._extract(track_urls, "spotify", str, IfNull.COALESCE)
                if not track_url:
                    logger.warning("Skipping track with empty URL")
                    continue
                track_name = self._extract(track, "name", str, IfNull.COALESCE)
                if not track_name:
                    logger.warning(f"Empty track name: {track_url}")

                album = self._extract(track, "album", dict, IfNull.RAISE)
                album_urls = self._extract(album, "external_urls", dict, IfNull.RAISE)
                album_url = self._extract(album_urls, "spotify", str, IfNull.COALESCE)
                album_name = self._extract(album, "name", str, IfNull.COALESCE)
                if not album_name:
                    logger.warning(f"Empty album name: {album_url}")

                artists = self._extract(track, "artists", list, IfNull.RAISE)
                artist_objs = []
                for artist in artists:
                    if not isinstance(artist, dict):
                        raise InvalidDataError(f"Invalid artist: {artist}")
                    artist_urls = self._extract(
                        artist, "external_urls", dict, IfNull.RAISE
                    )
                    artist_url = self._extract(
                        artist_urls, "spotify", str, IfNull.COALESCE
                    )
                    artist_name = (
                        self._extract(artist, "name", str, IfNull.COALESCE)
                        or self._extract(artist, "type", str, IfNull.COALESCE)
                        or ""
                    )
                    if not artist_name:
                        logger.warning(f"Empty artist name: {artist_url}")
                    artist_objs.append(Artist(url=artist_url, name=artist_name))

                if not artist_objs:
                    logger.warning(f"Empty track artists: {track_url}")

                duration_ms = self._extract(track, "duration_ms", int, IfNull.RAISE)

                added_at_string = self._extract(item, "added_at", str, IfNull.COALESCE)
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
    def _extract(
        cls,
        dict_: Dict[str, Any],
        key: str,
        type_: Type[T],
        if_null: IfNull,
    ) -> T:
        value = dict_.get(key)
        if value is None:
            if if_null is IfNull.RAISE:
                raise InvalidDataError(f"Missing key: {key}")
            if if_null is IfNull.COALESCE:
                return type_()
            raise RuntimeError(f"Unrecognized IfNull value: {if_null}")
        if not isinstance(value, type_):
            raise InvalidDataError(
                f"Invalid value for {key}, expected {type_.__name__} but got "
                f"{type(value).__name__}: {value}"
            )
        return value

    @classmethod
    def _get_playlist_href(cls, playlist_id: PlaylistID) -> str:
        rest = (
            "{}?fields=external_urls,name,description,snapshot_id,"
            "owner(display_name,external_urls),followers.total"
        )
        template = cls.BASE_URL + "playlists/" + rest
        return template.format(playlist_id)

    @classmethod
    def _get_tracks_href(cls, playlist_id: PlaylistID) -> str:
        rest = (
            "{}/tracks?fields=items(added_at,track(id,external_urls,"
            "duration_ms,name,album(external_urls,name),artists)),next"
        )
        template = cls.BASE_URL + "playlists/" + rest
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
    def _get_session(cls) -> aiohttp.ClientSession:
        return aiohttp.ClientSession()

    @classmethod
    @external
    async def _sleep(cls, seconds: float) -> None:
        await asyncio.sleep(seconds)
