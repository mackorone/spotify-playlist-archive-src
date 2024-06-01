#!/usr/bin/env python3

import datetime
import pathlib
import tempfile
import textwrap
from typing import Optional, TypeVar
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, call, patch, sentinel

from alias import Alias
from file_manager import FileManager, MalformedAliasError, UnexpectedFilesError
from file_updater import FileUpdater
from plants.unittest_utils import UnittestUtils
from playlist_id import PlaylistID
from playlist_types import Album, Artist, Owner, Playlist, Track
from spotify import (
    RequestFailedError,
    RequestRetryBudgetExceededError,
    ResourceNotFoundError,
    RetryBudget,
)

T = TypeVar("T")


class TestUpdateFiles(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mock_get_env = UnittestUtils.patch(
            self,
            "plants.environment.Environment.get_env",
            new_callable=Mock,
        )
        self.mock_get_env.side_effect = lambda name: {
            "SPOTIFY_CLIENT_ID": "client_id",
            "SPOTIFY_CLIENT_SECRET": "client_secret",
        }[name]

        self.mock_spotify = AsyncMock()
        self.mock_spotify_class = UnittestUtils.patch(
            self,
            "file_updater.Spotify",
            new_callable=lambda: Mock(return_value=self.mock_spotify),
        )

        self.mock_update_files_impl = UnittestUtils.patch(
            self, "file_updater.FileUpdater._update_files_impl", new_callable=AsyncMock
        )

    async def test_error(self) -> None:
        self.mock_update_files_impl.side_effect = Exception
        with self.assertRaises(Exception):
            await FileUpdater.update_files(
                now=sentinel.now,
                file_manager=sentinel.file_manager,
                auto_register=sentinel.auto_register,
            )
        self.mock_spotify.__aexit__.assert_called_once()
        self.mock_spotify.__aexit__.assert_awaited_once()

    async def test_success(self) -> None:
        await FileUpdater.update_files(
            now=sentinel.now,
            file_manager=sentinel.file_manager,
            auto_register=sentinel.auto_register,
        )
        self.mock_get_env.assert_has_calls(
            [
                call("SPOTIFY_CLIENT_ID"),
                call("SPOTIFY_CLIENT_SECRET"),
            ]
        )
        self.mock_spotify_class.assert_called_once_with(
            client_id="client_id", client_secret="client_secret", cache=None
        )
        self.mock_update_files_impl.assert_called_once_with(
            now=sentinel.now,
            file_manager=sentinel.file_manager,
            auto_register=sentinel.auto_register,
            spotify=self.mock_spotify.__aenter__.return_value,
        )
        self.mock_spotify.__aexit__.assert_called_once_with(None, None, None)
        self.mock_spotify.__aexit__.assert_awaited_once()


class TestUpdateFilesImpl(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = datetime.datetime(2021, 12, 15)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = pathlib.Path(self.temp_dir.name)
        self.playlists_dir = self.repo_dir / "playlists"
        self.file_manager = FileManager(self.playlists_dir)

        # Mock some external methods
        UnittestUtils.patch(
            self,
            "plants.environment.Environment.is_push_github_action",
            new_callable=lambda: Mock(return_value=False),
        )
        UnittestUtils.patch(
            self,
            "git_utils.GitUtils.get_last_commit_content",
            new_callable=lambda: Mock(return_value=[]),
        )

        # Mock the spotify class
        self.mock_spotify_class = UnittestUtils.patch(
            self,
            "file_updater.Spotify",
            new_callable=Mock,
        )

        # Use AsyncMocks for async methods
        self.mock_spotify = self.mock_spotify_class.return_value
        self.mock_spotify.get_spotify_user_playlist_ids = AsyncMock()
        self.mock_spotify.get_featured_playlist_ids = AsyncMock()
        self.mock_spotify.get_category_playlist_ids = AsyncMock()
        self.mock_spotify.get_playlist = AsyncMock()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def _update_files_impl(
        self,
        auto_register: bool = False,
    ) -> None:
        await FileUpdater._update_files_impl(
            now=self.now,
            file_manager=self.file_manager,
            auto_register=auto_register,
            spotify=self.mock_spotify,
        )

    @classmethod
    def _helper(
        cls,
        playlist_id: PlaylistID,
        original_name: str,
        num_followers: int,
    ) -> Playlist:
        return Playlist(
            url=f"url_{playlist_id}",
            original_name=original_name,
            unique_name=original_name,
            description="description",
            tracks=[],
            snapshot_id="snapshot_id",
            num_followers=num_followers,
            owner=Owner(
                url="owner_url",
                name="owner_name",
            ),
        )

    @classmethod
    def _fake_get_playlist(
        cls,
        playlist_id: PlaylistID,
        *,
        alias: Optional[Alias],
        retry_budget: Optional[RetryBudget] = None,
    ) -> Playlist:
        return cls._helper(
            playlist_id=playlist_id,
            original_name=alias or f"name_{playlist_id}",
            num_followers=0,
        )

    async def test_empty(self) -> None:
        names = ["registry", "plain", "pretty", "cumulative", "followers"]
        for name in names:
            self.assertFalse((self.playlists_dir / name).exists())
        await self._update_files_impl()
        for name in names:
            self.assertTrue((self.playlists_dir / name).exists())
        # Double check exist_ok = True
        await self._update_files_impl()

    async def test_request_failed(self) -> None:
        registry_dir = self.playlists_dir / "registry"
        registry_dir.mkdir(parents=True)
        (registry_dir / "foo").touch()
        self.mock_spotify.get_playlist.side_effect = RequestFailedError
        # Uncategorized request failures should terminate the program
        with self.assertRaises(RequestFailedError):
            await self._update_files_impl()

    async def test_auto_register(self) -> None:
        self.mock_spotify.get_spotify_user_playlist_ids.return_value = {"a", "d"}
        self.mock_spotify.get_featured_playlist_ids.return_value = {"b", "d"}
        self.mock_spotify.get_category_playlist_ids.return_value = {"c", "d"}
        self.mock_spotify.get_playlist.side_effect = self._fake_get_playlist
        for name in "abcd":
            self.assertFalse((self.playlists_dir / "registry" / name).exists())
        await self._update_files_impl(auto_register=True)
        for name in "abcd":
            self.assertTrue((self.playlists_dir / "registry" / name).exists())

    async def test_fixup_aliases(self) -> None:
        self.mock_spotify.get_playlist.side_effect = self._fake_get_playlist
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
        self.mock_spotify.get_playlist.side_effect = self._fake_get_playlist
        registry_dir = self.playlists_dir / "registry"
        registry_dir.mkdir(parents=True)
        with open(registry_dir / "foo", "w") as f:
            f.write("alias")
        await self._update_files_impl()
        args, kwargs = self.mock_spotify.get_playlist.call_args
        self.assertEqual(args, ("foo",))
        self.assertEqual(len(kwargs), 2)
        self.assertEqual(kwargs["alias"], "alias")
        self.assertEqual(kwargs["retry_budget"].get_initial_seconds(), 5)
        with open(self.playlists_dir / "plain" / "foo", "r") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "alias")

    async def test_duplicate_playlist_names(self) -> None:
        self.mock_spotify.get_playlist.side_effect = [
            self._helper(
                playlist_id=PlaylistID("a"), original_name="name", num_followers=1
            ),
            self._helper(
                playlist_id=PlaylistID("b"), original_name="name", num_followers=2
            ),
            self._helper(
                playlist_id=PlaylistID("c"), original_name="name", num_followers=2
            ),
            self._helper(
                playlist_id=PlaylistID("d"), original_name="name (3)", num_followers=0
            ),
            self._helper(
                playlist_id=PlaylistID("e"), original_name="name (3)", num_followers=0
            ),
            self._helper(
                playlist_id=PlaylistID("f"),
                original_name="name (3) (2)",
                num_followers=1,
            ),
        ]
        registry_dir = self.playlists_dir / "registry"
        registry_dir.mkdir(parents=True)
        for playlist_id in "abcdef":
            (registry_dir / playlist_id).touch()
        await self._update_files_impl()
        for playlist_id, name in [
            ("b", "name"),
            ("c", "name (2)"),
            ("d", "name (3)"),
            ("f", "name (3) (2)"),
            ("e", "name (3) (3)"),
            ("a", "name (4)"),
        ]:
            with open(self.playlists_dir / "plain" / playlist_id, "r") as f:
                lines = f.read().splitlines()
            self.assertEqual(lines[0], name)

    async def test_unexpected_files(self) -> None:
        self.mock_spotify.get_playlist.side_effect = self._fake_get_playlist
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

    # Patch the logger to suppress log spew
    @patch("file_updater.logger")
    async def test_index_and_metadata_json(self, mock_logger: Mock) -> None:
        # +-------------------+---+---+---+---+
        # |     Criteria      | a | b | c | d |
        # +-------------------+---+---+---+---+
        # | Fetch succeeds    | 1 | 1 | 0 | 0 |
        # | Has existing data | 1 | 0 | 1 | 0 |
        # +-------------------+---+---+---+---+

        self.mock_spotify.get_playlist.side_effect = UnittestUtils.side_effect(
            [
                self._fake_get_playlist(PlaylistID("a"), alias=None),
                self._fake_get_playlist(PlaylistID("b"), alias=None),
                ResourceNotFoundError(),
                RequestRetryBudgetExceededError(),
            ]
        )

        registry_dir = self.playlists_dir / "registry"
        registry_dir.mkdir(parents=True)
        for playlist_id in "abcd":
            (registry_dir / playlist_id).touch()

        metadata_dir = self.playlists_dir / "metadata"
        metadata_dir.mkdir(parents=True)

        pretty_dir = self.playlists_dir / "pretty"
        pretty_dir.mkdir(parents=True)
        for playlist_id in "ac":
            path = pretty_dir / f"{playlist_id}.json"
            playlist = self._helper(
                playlist_id=PlaylistID(playlist_id),
                original_name=f" name_{playlist_id} ",  # ensure whitespace is stripped
                num_followers=0,
            )
            playlist_json = playlist.to_json()
            with open(path, "w") as f:
                f.write(playlist_json)

        with open(self.playlists_dir / "index.md", "w") as f:
            f.write(
                textwrap.dedent(
                    """\
                    ## Playlists \\(1\\)

                    - [fizz](buzz)
                    """
                )
            )
        await self._update_files_impl()
        with open(self.playlists_dir / "index.md", "r") as f:
            content = f.read()
        self.assertEqual(
            content,
            textwrap.dedent(
                """\
                ## Playlists \\(3\\)

                - [name\\_a](/playlists/pretty/a.md)
                - [name\\_b](/playlists/pretty/b.md)
                - [name\\_c](/playlists/pretty/c.md)
                """
            ),
        )

        with open(metadata_dir / "metadata-full.json", "r") as f:
            content = f.read()
        self.assertEqual(
            content,
            textwrap.dedent(
                """\
                {
                  "a": {
                    "description": "description",
                    "num_followers": 0,
                    "original_name": "name_a",
                    "owner": {
                      "name": "owner_name",
                      "url": "owner_url"
                    },
                    "snapshot_id": "snapshot_id",
                    "unique_name": "name_a",
                    "url": "url_a"
                  },
                  "b": {
                    "description": "description",
                    "num_followers": 0,
                    "original_name": "name_b",
                    "owner": {
                      "name": "owner_name",
                      "url": "owner_url"
                    },
                    "snapshot_id": "snapshot_id",
                    "unique_name": "name_b",
                    "url": "url_b"
                  },
                  "c": {
                    "description": "description",
                    "num_followers": 0,
                    "original_name": " name_c ",
                    "owner": {
                      "name": "owner_name",
                      "url": "owner_url"
                    },
                    "snapshot_id": "snapshot_id",
                    "unique_name": " name_c ",
                    "url": "url_c"
                  }
                }
                """
            ),
        )

        with open(metadata_dir / "metadata-compact.json", "r") as f:
            content = f.read()
        self.assertEqual(content, '{"a":"name_a","b":"name_b","c":" name_c "}\n')

        with open(metadata_dir / "metadata-full.json.br", "rb") as f:
            content = f.read()
        self.assertEqual(
            content,
            (
                b"\x1b\x11\x03\x00\x1c\x07v,cz\xbe\xb1u'\xa6nK\xf5,$c\x1b\xdb-\x82"
                b"\x1eQL&\x88H_\xea\xb0(L\xdd\xbc\xe7gYjc76\x8e\r\x9e>X\xf4\xc0\tQ"
                b"\x17\x95n\x8b\x04\xf3W\x04\xe2\x8d;\xffH\xe0\xc6\x94z\x01\x9c\x1cu"
                b"\xd4[Da\x03\xcd\xa76\xc9q\x04\x0ezG\xa5r\xd4u\xaf\x9eB\xb9S$f\xff"
                b"\xe8\x9dy\x98\x81sz\xb9\xf9\x966D7\x1c\x1dL2Jl&4\x8dn\xc0\xd5\x8dB"
                b"\xa5?\x9a\xf7\xf0\x0e&\x9a\x11?/\xc9\x87\xfc>C\xf4<\x81\x07\xb3j"
            ),
        )

        with open(metadata_dir / "metadata-compact.json.br", "rb") as f:
            content = f.read()
        self.assertEqual(
            content,
            (
                b"\x1b)\x00\xf8\x1d\tv\xac\x89\xbb\xf348a\x08tc\xa9>7\xd9\x8fQC"
                b"\x11C\xa4Xt:\x81EDqH\x15\xd0\xc0\x1e\x97\xe9\x82c\xa2\x14="
            ),
        )

    async def test_success(self) -> None:
        # Assert that the playlists directory starts out as empty
        self.assertEqual(sorted(self.playlists_dir.rglob("*")), [])

        # First run, with no playlists registered
        await self._update_files_impl()

        # Make sure the expected dirs and files exist
        self.assertEqual(
            sorted([x for x in self.playlists_dir.rglob("*") if x.is_dir()]),
            [
                self.playlists_dir / "cumulative",
                self.playlists_dir / "followers",
                self.playlists_dir / "metadata",
                self.playlists_dir / "plain",
                self.playlists_dir / "pretty",
                self.playlists_dir / "registry",
            ],
        )
        self.assertEqual(
            sorted([x for x in self.playlists_dir.rglob("*") if x.is_file()]),
            [
                self.playlists_dir / "index.md",
                self.playlists_dir / "metadata/metadata-compact.json",
                self.playlists_dir / "metadata/metadata-compact.json.br",
                self.playlists_dir / "metadata/metadata-full.json",
                self.playlists_dir / "metadata/metadata-full.json.br",
            ],
        )

        # Next, lets register a playlist
        with open(self.playlists_dir / "registry" / "abc", "w") as f:
            pass

        # Create a fake playlist
        playlist = Playlist(
            url="playlist_url",
            original_name="playlist_original_name",
            unique_name="playlist_unique_name",
            description="playlist_description",
            tracks=[
                Track(
                    url="trackurl",
                    name="track_name",
                    album=Album(
                        url="album_url",
                        name="album_name",
                    ),
                    artists=[
                        Artist(
                            url="artist_one_url",
                            name="artist_one_name",
                        ),
                        Artist(
                            url="artist_two_url",
                            name="artist_two_name",
                        ),
                    ],
                    duration_ms=12345,
                    added_at=self.now,
                )
            ],
            snapshot_id="playlist_snapshot_id",
            num_followers=999,
            owner=Owner(
                url="owner_url",
                name="owner_name",
            ),
        )
        self.mock_spotify.get_playlist.side_effect = [playlist]

        # Now let's run it again
        await self._update_files_impl()

        # Make sure the expected dirs and files exist
        self.assertEqual(
            sorted([x for x in self.playlists_dir.rglob("*") if x.is_dir()]),
            [
                self.playlists_dir / "cumulative",
                self.playlists_dir / "followers",
                self.playlists_dir / "metadata",
                self.playlists_dir / "plain",
                self.playlists_dir / "pretty",
                self.playlists_dir / "registry",
            ],
        )
        self.assertEqual(
            sorted([x for x in self.playlists_dir.rglob("*") if x.is_file()]),
            [
                self.playlists_dir / "cumulative/abc.json",
                self.playlists_dir / "cumulative/abc.md",
                self.playlists_dir / "followers/abc.json",
                # Note: can skip validating the contents of index.md and the
                # files in metadata because they're covered by previous tests
                self.playlists_dir / "index.md",
                self.playlists_dir / "metadata/metadata-compact.json",
                self.playlists_dir / "metadata/metadata-compact.json.br",
                self.playlists_dir / "metadata/metadata-full.json",
                self.playlists_dir / "metadata/metadata-full.json.br",
                self.playlists_dir / "plain/abc",
                self.playlists_dir / "pretty/abc.json",
                self.playlists_dir / "pretty/abc.md",
                self.playlists_dir / "registry/abc",
            ],
        )

        # Now check the contents of each file
        with open(self.playlists_dir / "cumulative/abc.json") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    {
                      "date_first_scraped": "2021-12-15",
                      "description": "playlist_description",
                      "name": "playlist_unique_name",
                      "tracks": [
                        {
                          "album": {
                            "name": "album_name",
                            "url": "album_url"
                          },
                          "artists": [
                            {
                              "name": "artist_one_name",
                              "url": "artist_one_url"
                            },
                            {
                              "name": "artist_two_name",
                              "url": "artist_two_url"
                            }
                          ],
                          "date_added": "2021-12-15",
                          "date_added_asterisk": false,
                          "date_removed": null,
                          "duration_ms": 12345,
                          "name": "track_name",
                          "url": "trackurl"
                        }
                      ],
                      "url": "playlist_url"
                    }
                    """
                ),
            )

        with open(self.playlists_dir / "cumulative/abc.md") as f:
            self.assertEqual(
                f.read(),
                # Weird formatting here because my editor doesn't like trailing
                # whitespace, but textwrap dedent requires leading whitespace
                # on every line, even the empty ones
                r"""[pretty](/playlists/pretty/abc.md) - cumulative - [plain](/playlists/plain/abc) - [githistory](base/plain/abc)

### [playlist\_unique\_name](playlist_url)

> playlist\_description

1 song - 12 sec

| Title | Artist(s) | Album | Length | Added | Removed |
|---|---|---|---|---|---|
| [track\_name](trackurl) | [artist\_one\_name](artist_one_url), [artist\_two\_name](artist_two_url) | [album\_name](album_url) | 0:12 | 2021-12-15 |  |

\*This playlist was first scraped on 2021-12-15. Prior content cannot be recovered.
""",
            )

        with open(self.playlists_dir / "followers/abc.json") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    {
                      "2021-12-15": 999
                    }
                    """
                ),
            )

        with open(self.playlists_dir / "plain/abc") as f:
            self.assertEqual(
                f.read(),
                # Weird formatting here because my editor doesn't like trailing
                # whitespace, but textwrap dedent requires leading whitespace
                # on every line, even the empty ones
                r"""playlist_unique_name
playlist_description

track_name -- artist_one_name, artist_two_name -- album_name
""",
            )

        with open(self.playlists_dir / "pretty/abc.json") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    {
                      "description": "playlist_description",
                      "num_followers": 999,
                      "original_name": "playlist_original_name",
                      "owner": {
                        "name": "owner_name",
                        "url": "owner_url"
                      },
                      "snapshot_id": "playlist_snapshot_id",
                      "tracks": [
                        {
                          "added_at": "2021-12-15 00:00:00",
                          "album": {
                            "name": "album_name",
                            "url": "album_url"
                          },
                          "artists": [
                            {
                              "name": "artist_one_name",
                              "url": "artist_one_url"
                            },
                            {
                              "name": "artist_two_name",
                              "url": "artist_two_url"
                            }
                          ],
                          "duration_ms": 12345,
                          "name": "track_name",
                          "url": "trackurl"
                        }
                      ],
                      "unique_name": "playlist_unique_name",
                      "url": "playlist_url"
                    }
                    """
                ),
            )

        with open(self.playlists_dir / "pretty/abc.md") as f:
            self.assertEqual(
                f.read(),
                # Weird formatting here because my editor doesn't like trailing
                # whitespace, but textwrap dedent requires leading whitespace
                # on every line, even the empty ones
                r"""pretty - [cumulative](/playlists/cumulative/abc.md) - [plain](/playlists/plain/abc) - [githistory](base/plain/abc)

### [playlist\_unique\_name](playlist_url)

> playlist\_description

[owner\_name](owner_url) - 999 likes - 1 song - 12 sec

| No. | Title | Artist(s) | Album | Length |
|---|---|---|---|---|
| 1 | [track\_name](trackurl) | [artist\_one\_name](artist_one_url), [artist\_two\_name](artist_two_url) | [album\_name](album_url) | 0:12 |

Snapshot ID: `playlist_snapshot_id`
""",
            )

        with open(self.playlists_dir / "registry/abc") as f:
            self.assertEqual(f.read(), "")
