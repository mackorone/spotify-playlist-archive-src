#!/usr/bin/env python3

from typing import Type, TypeVar
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, call, patch, sentinel

from file_updater import FileUpdater

T = TypeVar("T")


class TestUpdateFiles(IsolatedAsyncioTestCase):
    def _patch(
        self,
        target: str,
        new_callable: Type[T],
    ) -> T:
        patcher = patch(target, new_callable=new_callable)
        mock_object = patcher.start()
        self.addCleanup(patcher.stop)
        return mock_object

    async def asyncSetUp(self) -> None:
        self.mock_get_env = self._patch(
            "environment.Environment.get_env",
            new_callable=Mock,
        )
        self.mock_get_env.side_effect = lambda name: {
            "SPOTIFY_CLIENT_ID": "client_id",
            "SPOTIFY_CLIENT_SECRET": "client_secret",
        }[name]

        self.mock_spotify = self._patch(
            "file_updater.Spotify",
            new_callable=Mock,
        )
        self.mock_spotify.get_access_token = AsyncMock()
        self.mock_spotify.return_value.shutdown = AsyncMock()

        self.mock_update_files_impl = self._patch(
            "file_updater.FileUpdater._update_files_impl", new_callable=AsyncMock
        )

    async def test_error(self) -> None:
        self.mock_update_files_impl.side_effect = Exception
        with self.assertRaises(Exception):
            await FileUpdater.update_files(
                now=sentinel.now,
                playlists_dir=sentinel.playlists_dir,
                auto_register=sentinel.auto_register,
                update_readme=sentinel.update_readme,
            )
        self.mock_spotify.return_value.shutdown.assert_called_once_with()
        self.mock_spotify.return_value.shutdown.assert_awaited_once()

    async def test_success(self) -> None:
        await FileUpdater.update_files(
            now=sentinel.now,
            playlists_dir=sentinel.playlists_dir,
            auto_register=sentinel.auto_register,
            update_readme=sentinel.update_readme,
        )
        self.mock_get_env.assert_has_calls(
            [
                call("SPOTIFY_CLIENT_ID"),
                call("SPOTIFY_CLIENT_SECRET"),
            ]
        )
        self.mock_spotify.get_access_token.assert_called_once_with(
            client_id="client_id",
            client_secret="client_secret",
        )
        self.mock_spotify.get_access_token.assert_awaited_once()
        self.mock_spotify.assert_called_once_with(
            self.mock_spotify.get_access_token.return_value
        )
        self.mock_update_files_impl.assert_called_once_with(
            now=sentinel.now,
            playlists_dir=sentinel.playlists_dir,
            auto_register=sentinel.auto_register,
            update_readme=sentinel.update_readme,
            spotify=self.mock_spotify.return_value,
        )
        self.mock_spotify.return_value.shutdown.assert_called_once_with()
        self.mock_spotify.return_value.shutdown.assert_awaited_once()
