#!/usr/bin/env python3

from types import TracebackType
from typing import Any, Optional, Type
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from async_mock import AsyncMock
from spotify import Spotify, FailedToGetAccessTokenError, FailedToGetTracksError


class FakeSession:
    def __init__(self) -> None:
        self.get = Mock(return_value=AsyncMock())
        self.post = Mock(return_value=AsyncMock())
        self.close = AsyncMock()
        self._session = Mock()
        self._session.get = self.get
        self._session.post = self.post

    async def __aenter__(self) -> Mock:
        return self._session

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()


class SpotifyTestCase(IsolatedAsyncioTestCase):
    def _patch(self, target: str, return_value: Any) -> Mock:  # pyre-fixme[2]
        patcher = patch(target, return_value=return_value)
        mock_object = patcher.start()
        self.addCleanup(patcher.stop)
        return mock_object

    def setUp(self) -> None:
        self.mock_session = FakeSession()
        self.mock_get_session = self._patch(
            target="spotify.Spotify._get_session",
            return_value=self.mock_session,
        )
        self.mock_sleep = self._patch(
            target="spotify.Spotify._sleep", return_value=AsyncMock()
        )


class TestShutdown(SpotifyTestCase):
    async def test_success(self) -> None:
        spotify = Spotify("token")
        await spotify.shutdown()
        self.mock_session.close.assert_called_once()
        self.mock_sleep.assert_called_once_with(0)


class TestGetTracks(SpotifyTestCase):
    async def test_error(self) -> None:
        async with self.mock_session.get.return_value as mock_response:
            mock_response.json.return_value = {"error": "something went wrong"}
        spotify = Spotify("token")
        with self.assertRaises(FailedToGetTracksError):
            await spotify._get_tracks("playlist_id")


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
        self.mock_session.close.assert_called_once()
