#!/usr/bin/env python3

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from spotify import FailedToGetAccessTokenError, FailedToGetTracksError, Spotify


class MockSession(AsyncMock):
    async def async_init(self) -> None:
        # AsyncMock objects beget other AsyncMock objects, but these methods
        # are synchronous, so we need initialize them explicitly
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
        self.mock_session = MockSession()
        await self.mock_session.async_init()
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
