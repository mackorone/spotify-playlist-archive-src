#!/usr/bin/env python3

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, call, patch

from playlist_id import PlaylistID
from spotify import (
    Album,
    Artist,
    FailedToGetAccessTokenError,
    FailedToGetPlaylistError,
    FailedToGetTracksError,
    Playlist,
    RetryBudgetExceededError,
    Spotify,
    Track,
)


class MockSession(AsyncMock):
    @classmethod
    async def create(cls) -> MockSession:
        mock_session = MockSession()
        await mock_session._init()
        return mock_session

    async def _init(self) -> None:
        # AsyncMock objects beget other AsyncMock objects, but these methods
        # are synchronous so we need initialize them explicitly
        self.get = Mock(return_value=AsyncMock())
        self.post = Mock(return_value=AsyncMock())
        # Allow MockSession objects to be used as async context managers
        async with self as session:
            session.get = self.get
            session.post = self.post


class SpotifyTestCase(IsolatedAsyncioTestCase):
    def _patch(
        self,
        target: str,
        new_callable=None,  # pyre-fixme[2]
        return_value=None,  # pyre-fixme[2]
    ) -> Mock:
        patcher = patch(target, new_callable=new_callable, return_value=return_value)
        mock_object = patcher.start()
        self.addCleanup(patcher.stop)
        return mock_object

    async def asyncSetUp(self) -> None:
        self.mock_session = await MockSession.create()
        self.mock_get_session = self._patch(
            "spotify.Spotify._get_session",
            return_value=self.mock_session,
        )
        self.mock_sleep = self._patch(
            "spotify.Spotify._sleep",
            new_callable=AsyncMock,
        )


class TestShutdown(SpotifyTestCase):
    async def test_success(self) -> None:
        spotify = Spotify("token")
        await spotify.shutdown()
        self.mock_session.close.assert_called_once()
        self.mock_sleep.assert_called_once_with(0)


class TestGetPlaylist(SpotifyTestCase):
    async def test_invalid_data(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = ""
        spotify = Spotify("token")
        with self.assertRaises(FailedToGetPlaylistError):
            await spotify.get_playlist(PlaylistID("abc123"), aliases={})

    async def test_empty_data(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {}
        spotify = Spotify("token")
        with self.assertRaises(FailedToGetPlaylistError):
            await spotify.get_playlist(PlaylistID("abc123"), aliases={})

    async def test_error(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {"error": "whoops"}
        spotify = Spotify("token")
        with self.assertRaises(FailedToGetPlaylistError):
            await spotify.get_playlist(PlaylistID("abc123"), aliases={})

    async def test_empty_name(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {"name": ""}
        spotify = Spotify("token")
        with self.assertRaises(FailedToGetPlaylistError):
            await spotify.get_playlist(PlaylistID("abc123"), aliases={})

    async def test_empty_alias(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {"name": "foo"}
        spotify = Spotify("token")
        with self.assertRaises(FailedToGetPlaylistError):
            await spotify.get_playlist(
                PlaylistID("abc123"), aliases={PlaylistID("abc123"): ""}
            )

    @patch("spotify.Spotify._get_tracks", new_callable=AsyncMock)
    async def test_success(self, mock_get_tracks: AsyncMock) -> None:
        track = Track(
            url="track_url",
            name="track_name",
            album=Album(
                url="album_url",
                name="album_name",
            ),
            artists=[
                Artist(
                    url="artist_url",
                    name="artist_name",
                )
            ],
            duration_ms=100,
        )
        mock_get_tracks.return_value = [track]
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {
                "name": "playlist_name",
                "description": "playlist_description",
                "external_urls": {
                    "spotify": "playlist_url",
                },
            }
        spotify = Spotify("token")
        playlist = await spotify.get_playlist(PlaylistID("abc123"), aliases={})
        self.assertEqual(
            playlist,
            Playlist(
                url="playlist_url",
                name="playlist_name",
                description="playlist_description",
                tracks=[track],
            ),
        )


class TestGetTracks(SpotifyTestCase):
    async def test_invalid_data(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = ""
        spotify = Spotify("token")
        with self.assertRaises(FailedToGetTracksError):
            await spotify._get_tracks(PlaylistID("abc123"))

    async def test_empty_data(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {}
        spotify = Spotify("token")
        with self.assertRaises(FailedToGetTracksError):
            await spotify._get_tracks(PlaylistID("abc123"))

    async def test_error(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {"error": "whoops"}
        spotify = Spotify("token")
        with self.assertRaises(FailedToGetTracksError):
            await spotify._get_tracks(PlaylistID("abc123"))

    async def test_empty_playlist(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {
                "items": [],
                "next": "",
            }
        spotify = Spotify("token")
        tracks = await spotify._get_tracks(PlaylistID("abc123"))
        self.assertEqual(tracks, [])

    async def test_empty_track(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {
                "items": [{"track": {}}],
                "next": "",
            }
        spotify = Spotify("token")
        tracks = await spotify._get_tracks(PlaylistID("abc123"))
        self.assertEqual(tracks, [])

    # Patch the logger to suppress log spew
    @patch("spotify.logger")
    async def test_missing_info(self, logger: Mock) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {
                "items": [
                    {
                        "track": {
                            "duration_ms": 123,
                            "name": "",
                            "album": {
                                "name": "",
                                "external_urls": {},
                            },
                            "artists": [],
                            "external_urls": {},
                        }
                    },
                ],
                "next": "",
            }
        spotify = Spotify("token")
        tracks = await spotify._get_tracks(PlaylistID("abc123"))
        self.assertEqual(
            tracks,
            [
                Track(
                    url="",
                    name="<MISSING>",
                    album=Album(
                        url="",
                        name="<MISSING>",
                    ),
                    artists=[],
                    duration_ms=123,
                )
            ],
        )

    async def test_success(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {
                "items": [
                    {
                        "track": {
                            "duration_ms": 456,
                            "name": "track_name",
                            "album": {
                                "name": "album_name",
                                "external_urls": {
                                    "spotify": "album_url",
                                },
                            },
                            "artists": [
                                {
                                    "name": "artist_name_1",
                                    "external_urls": {
                                        "spotify": "artist_url_1",
                                    },
                                },
                                {
                                    "name": "artist_name_2",
                                    "external_urls": {
                                        "spotify": "artist_url_2",
                                    },
                                },
                            ],
                            "external_urls": {
                                "spotify": "track_url",
                            },
                        }
                    },
                ],
                "next": "",
            }
        spotify = Spotify("token")
        tracks = await spotify._get_tracks(PlaylistID("abc123"))
        self.assertEqual(
            tracks,
            [
                Track(
                    url="track_url",
                    name="track_name",
                    album=Album(
                        url="album_url",
                        name="album_name",
                    ),
                    artists=[
                        Artist(
                            name="artist_name_1",
                            url="artist_url_1",
                        ),
                        Artist(
                            name="artist_name_2",
                            url="artist_url_2",
                        ),
                    ],
                    duration_ms=456,
                )
            ],
        )

    @patch("spotify.Spotify._get_tracks_href")
    async def test_pagination(self, mock_get_tracks_href: Mock) -> None:
        mock_get_tracks_href.side_effect = lambda x: x
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.side_effect = [
                {"items": [], "next": "b"},
                {"items": [], "next": "c"},
                {"items": [], "next": ""},
            ]
        spotify = Spotify("token")
        tracks = await spotify._get_tracks(PlaylistID("a"))
        self.assertEqual(tracks, [])
        self.mock_session.get.assert_has_calls(
            [
                call("a"),
                call("b"),
                call("c"),
            ]
        )

    # Patch the logger to suppress log spew
    @patch("spotify.logger")
    async def test_server_unavailable(self, mock_logger: Mock) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.status = 500
        spotify = Spotify("token")
        with self.assertRaises(RetryBudgetExceededError):
            await spotify._get_tracks(PlaylistID("abc123"))

    # Patch the logger to suppress log spew
    @patch("spotify.logger")
    async def test_transient_server_error(self, mock_logger: Mock) -> None:
        mock_responses = [AsyncMock(), AsyncMock()]
        async with mock_responses[0] as mock_response:
            mock_response.status = 500
        async with mock_responses[1] as mock_response:
            mock_response.json.return_value = {"items": [], "next": ""}
        self.mock_session.get.side_effect = mock_responses
        spotify = Spotify("token")
        await spotify._get_tracks(PlaylistID("abc123"))
        self.assertEqual(self.mock_session.get.call_count, 2)
        self.mock_sleep.assert_called_once_with(1)

    # Patch the logger to suppress log spew
    @patch("spotify.logger")
    async def test_rate_limited(self, mock_logger: Mock) -> None:
        mock_responses = [AsyncMock(), AsyncMock()]
        async with mock_responses[0] as mock_response:
            mock_response.status = 429
            mock_response.headers = {"Retry-After": 4.2}
        async with mock_responses[1] as mock_response:
            mock_response.json.return_value = {"items": [], "next": ""}
        self.mock_session.get.side_effect = mock_responses
        spotify = Spotify("token")
        await spotify._get_tracks(PlaylistID("abc123"))
        self.assertEqual(self.mock_session.get.call_count, 2)
        self.mock_sleep.assert_called_once_with(5)


class TestGetAccessToken(SpotifyTestCase):
    async def test_invalid_json(self) -> None:
        async with self.mock_session.post.return_value as mock_response:
            mock_response.json.side_effect = Exception
        with self.assertRaises(FailedToGetAccessTokenError):
            await Spotify.get_access_token("client_id", "client_secret")

    async def test_error(self) -> None:
        async with self.mock_session.post.return_value as mock_response:
            mock_response.json.return_value = {
                "error": "something went wrong",
                "access_token": "token",
                "token_type": "Bearer",
            }
        with self.assertRaises(FailedToGetAccessTokenError):
            await Spotify.get_access_token("client_id", "client_secret")

    async def test_invalid_access_token(self) -> None:
        async with self.mock_session.post.return_value as mock_response:
            mock_response.json.return_value = {
                "access_token": "",
                "token_type": "Bearer",
            }
        with self.assertRaises(FailedToGetAccessTokenError):
            await Spotify.get_access_token("client_id", "client_secret")

    async def test_invalid_token_type(self) -> None:
        async with self.mock_session.post.return_value as mock_response:
            mock_response.json.return_value = {
                "access_token": "token",
                "token_type": "invalid",
            }
        with self.assertRaises(FailedToGetAccessTokenError):
            await Spotify.get_access_token("client_id", "client_secret")

    async def test_success(self) -> None:
        async with self.mock_session.post.return_value as mock_response:
            mock_response.json.return_value = {
                "access_token": "token",
                "token_type": "Bearer",
            }
        token = await Spotify.get_access_token("client_id", "client_secret")
        self.assertEqual(token, "token")
        self.mock_session.post.assert_called_once_with(
            url="https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": "Basic Y2xpZW50X2lkOmNsaWVudF9zZWNyZXQ="},
        )
