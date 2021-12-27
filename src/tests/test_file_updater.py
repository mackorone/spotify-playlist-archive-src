#!/usr/bin/env python3

import datetime
import pathlib
import tempfile
import textwrap
from typing import Mapping, Type, TypeVar
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, call, patch, sentinel

from file_updater import (
    DuplicatePlaylistNamesError,
    FileUpdater,
    MalformedAliasError,
    UnexpectedFilesError,
)
from playlist_id import PlaylistID
from playlist_types import Owner, Playlist

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

        self.mock_spotify_class = self._patch(
            "file_updater.Spotify",
            new_callable=Mock,
        )
        self.mock_spotify_class.get_access_token = AsyncMock()
        self.mock_spotify_class.return_value.shutdown = AsyncMock()

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
        self.mock_spotify_class.return_value.shutdown.assert_called_once_with()
        self.mock_spotify_class.return_value.shutdown.assert_awaited_once()

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
        self.mock_spotify_class.get_access_token.assert_called_once_with(
            client_id="client_id",
            client_secret="client_secret",
        )
        self.mock_spotify_class.get_access_token.assert_awaited_once()
        self.mock_spotify_class.assert_called_once_with(
            self.mock_spotify_class.get_access_token.return_value
        )
        self.mock_update_files_impl.assert_called_once_with(
            now=sentinel.now,
            playlists_dir=sentinel.playlists_dir,
            auto_register=sentinel.auto_register,
            update_readme=sentinel.update_readme,
            spotify=self.mock_spotify_class.return_value,
        )
        self.mock_spotify_class.return_value.shutdown.assert_called_once_with()
        self.mock_spotify_class.return_value.shutdown.assert_awaited_once()


class TestUpdateFilesImpl(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = datetime.datetime(2021, 12, 15)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = pathlib.Path(self.temp_dir.name)
        self.playlists_dir = self.repo_dir / "playlists"

        # Mock the spotify class
        patcher = patch("file_updater.Spotify")
        self.mock_spotify_class = patcher.start()
        self.addCleanup(patcher.stop)

        # Use AsyncMocks for async methods
        self.mock_spotify = self.mock_spotify_class.return_value
        self.mock_spotify.get_spotify_user_playlist_ids = AsyncMock()
        self.mock_spotify.get_featured_playlist_ids = AsyncMock()
        self.mock_spotify.get_playlist = AsyncMock()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def _update_files_impl(
        self, auto_register: bool = False, update_readme: bool = False
    ) -> None:
        await FileUpdater._update_files_impl(
            now=self.now,
            playlists_dir=self.playlists_dir,
            auto_register=auto_register,
            update_readme=update_readme,
            spotify=self.mock_spotify,
        )

    @classmethod
    def _get_playlist(
        cls, playlist_id: PlaylistID, aliases: Mapping[PlaylistID, str]
    ) -> Playlist:
        return Playlist(
            url=f"playlist_{playlist_id}_url",
            name=aliases.get(playlist_id) or f"playlist_{playlist_id}_name",
            description="description",
            tracks=[],
            snapshot_id="snapshot_id",
            num_followers=0,
            owner=Owner(
                url="owner_url",
                name="owner_name",
            ),
        )

    async def test_empty(self) -> None:
        names = ["registry", "plain", "pretty", "cumulative"]
        for name in names:
            self.assertFalse((self.playlists_dir / name).exists())
        await self._update_files_impl()
        for name in names:
            self.assertTrue((self.playlists_dir / name).exists())
        # Double check exist_ok = True
        await self._update_files_impl()

    async def test_auto_register(self) -> None:
        self.mock_spotify.get_spotify_user_playlist_ids.return_value = {"one", "three"}
        self.mock_spotify.get_featured_playlist_ids.return_value = {"two", "three"}
        self.mock_spotify.get_playlist.side_effect = self._get_playlist
        for name in ["one", "two", "three"]:
            self.assertFalse((self.playlists_dir / "registry" / name).exists())
        await self._update_files_impl(auto_register=True)
        for name in ["one", "two", "three"]:
            self.assertTrue((self.playlists_dir / "registry" / name).exists())

    async def test_fixup_aliases(self) -> None:
        self.mock_spotify.get_playlist.side_effect = self._get_playlist
        registry_dir = self.playlists_dir / "registry"
        registry_dir.mkdir(parents=True)
        alias_file = registry_dir / "foo"
        with open(alias_file, "w") as f:
            f.write("\n")
        with open(alias_file, "r") as f:
            self.assertTrue(f.read())
        await self._update_files_impl()
        with open(alias_file, "r") as f:
            self.assertFalse(f.read())

    async def test_invalid_aliases(self) -> None:
        registry_dir = self.playlists_dir / "registry"
        registry_dir.mkdir(parents=True)
        alias_file = registry_dir / "foo"
        for malformed_alias in ["\n\n", "a\nc", " \n"]:
            with open(alias_file, "w") as f:
                f.write(malformed_alias)
            with self.assertRaises(MalformedAliasError):
                await self._update_files_impl()

    async def test_good_alias(self) -> None:
        self.mock_spotify.get_playlist.side_effect = self._get_playlist
        registry_dir = self.playlists_dir / "registry"
        registry_dir.mkdir(parents=True)
        with open(registry_dir / "foo", "w") as f:
            f.write("alias")
        await self._update_files_impl()
        self.mock_spotify.get_playlist.assert_called_once_with("foo", {"foo": "alias"})
        with open(self.playlists_dir / "plain" / "foo", "r") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "alias")

    async def test_duplicate_playlist_names(self) -> None:
        self.mock_spotify.get_playlist.side_effect = self._get_playlist
        registry_dir = self.playlists_dir / "registry"
        registry_dir.mkdir(parents=True)
        with open(registry_dir / "foo", "w") as f:
            f.write("alias")
        with open(registry_dir / "bar", "w") as f:
            f.write("alias")
        with self.assertRaises(DuplicatePlaylistNamesError):
            await self._update_files_impl()

    async def test_unexpected_files(self) -> None:
        self.mock_spotify.get_playlist.side_effect = self._get_playlist
        for directory in ["registry", "plain", "pretty", "cumulative"]:
            (self.playlists_dir / directory).mkdir(parents=True)
        (self.playlists_dir / "registry" / "foo").touch()
        for directory, filename in [
            ("plain", "bar"),
            ("plain", "foo.md"),
            ("plain", "foo.json"),
            ("pretty", "foo"),
            ("pretty", "bar.md"),
            ("pretty", "bar.json"),
            ("cumulative", "foo"),
            ("cumulative", "bar.md"),
            ("cumulative", "bar.json"),
        ]:
            path = self.playlists_dir / directory / filename
            path.touch()
            with self.assertRaises(UnexpectedFilesError):
                await self._update_files_impl()
            path.unlink()

    async def test_readme(self) -> None:
        self.mock_spotify.get_playlist.side_effect = self._get_playlist
        for directory in ["registry", "plain", "pretty", "cumulative"]:
            (self.playlists_dir / directory).mkdir(parents=True)
        for name in ["one", "two", "three"]:
            (self.playlists_dir / "registry" / name).touch()
        with open(self.repo_dir / "README.md", "w") as f:
            f.write(
                textwrap.dedent(
                    """\
                    prev content

                    ## Playlists

                    - [fizz](buzz)
                    """
                )
            )
        await self._update_files_impl(update_readme=True)
        with open(self.repo_dir / "README.md", "r") as f:
            content = f.read()
        self.assertEqual(
            content,
            textwrap.dedent(
                """\
                prev content

                ## Playlists

                - [playlist\\_one\\_name](/playlists/pretty/one.md)
                - [playlist\\_three\\_name](/playlists/pretty/three.md)
                - [playlist\\_two\\_name](/playlists/pretty/two.md)
                """
            ),
        )

    async def test_success(self) -> None:
        # TODO
        pass
