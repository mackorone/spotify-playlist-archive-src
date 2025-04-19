#!/usr/bin/env python3

import datetime
import pathlib
import tempfile
import textwrap
from typing import Optional, TypeVar
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, call, patch, sentinel

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
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyOwner,
    SpotifyPlaylist,
    SpotifyRecentlyPlayedTrack,
    SpotifyTrack,
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
            "SPOTIFY_REFRESH_TOKEN": "refresh_token",
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

        self.mock_update_play_history = UnittestUtils.patch(
            self,
            "file_updater.FileUpdater._update_play_history",
            new_callable=AsyncMock,
        )

    async def test_error(self) -> None:
        self.mock_update_files_impl.side_effect = Exception
        with self.assertRaises(Exception):
            await FileUpdater.update_files(
                now=sentinel.now,
                file_manager=sentinel.file_manager,
                auto_register=sentinel.auto_register,
                skip_cumulative_playlists=sentinel.skip_cumulative_playlists,
                history_dir=sentinel.history_dir,
            )
        self.mock_spotify.__aexit__.assert_called_once()
        self.mock_spotify.__aexit__.assert_awaited_once()

    async def test_success(self) -> None:
        await FileUpdater.update_files(
            now=sentinel.now,
            file_manager=sentinel.file_manager,
            auto_register=sentinel.auto_register,
            skip_cumulative_playlists=sentinel.skip_cumulative_playlists,
            history_dir=sentinel.history_dir,
        )
        self.mock_get_env.assert_has_calls(
            [
                call("SPOTIFY_CLIENT_ID"),
                call("SPOTIFY_CLIENT_SECRET"),
                call("SPOTIFY_REFRESH_TOKEN"),
            ]
        )
        self.mock_spotify_class.assert_called_once_with(
            client_id="client_id",
            client_secret="client_secret",
            refresh_token="refresh_token",
            cache=None,
        )
        self.mock_update_files_impl.assert_called_once_with(
            now=sentinel.now,
            file_manager=sentinel.file_manager,
            spotify=self.mock_spotify.__aenter__.return_value,
            auto_register=sentinel.auto_register,
            skip_cumulative_playlists=sentinel.skip_cumulative_playlists,
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
        mock_spotify_class = UnittestUtils.patch(
            self,
            "file_updater.Spotify",
            new_callable=Mock,
        )

        # Use AsyncMocks for async methods
        self.mock_spotify = mock_spotify_class.return_value
        self.mock_spotify.get_spotify_user_playlist_ids = AsyncMock()
        self.mock_spotify.get_featured_playlist_ids = AsyncMock()
        self.mock_spotify.get_category_playlist_ids = AsyncMock()
        self.mock_spotify.get_playlist = AsyncMock()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def _update_files_impl(
        self,
        *,
        auto_register: bool = False,
        skip_cumulative_playlists: bool = False,
    ) -> None:
        await FileUpdater._update_files_impl(
            now=self.now,
            file_manager=self.file_manager,
            spotify=self.mock_spotify,
            auto_register=auto_register,
            skip_cumulative_playlists=skip_cumulative_playlists,
        )

    @classmethod
    def _helper(
        cls,
        playlist_id: PlaylistID,
        name: str,
        num_followers: int,
    ) -> SpotifyPlaylist:
        return SpotifyPlaylist(
            url=f"url_{playlist_id}",
            name=name,
            description="description",
            tracks=[],
            snapshot_id="snapshot_id",
            num_followers=num_followers,
            owner=SpotifyOwner(
                url="owner_url",
                name="owner_name",
            ),
        )

    @classmethod
    def _fake_get_playlist(
        cls,
        playlist_id: PlaylistID,
        *,
        retry_budget: Optional[RetryBudget] = None,
    ) -> SpotifyPlaylist:
        return cls._helper(
            playlist_id=playlist_id,
            name=f"name_{playlist_id}",
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
        self.assertEqual(len(kwargs), 1)
        self.assertEqual(kwargs["retry_budget"].get_initial_seconds(), 5)
        with open(self.playlists_dir / "plain" / "foo", "r") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "alias")

    async def test_duplicate_playlist_names(self) -> None:
        self.mock_spotify.get_playlist.side_effect = [
            self._helper(playlist_id=PlaylistID("a"), name="name", num_followers=1),
            self._helper(playlist_id=PlaylistID("b"), name="name", num_followers=2),
            self._helper(playlist_id=PlaylistID("c"), name="name", num_followers=2),
            self._helper(playlist_id=PlaylistID("d"), name="name (3)", num_followers=0),
            self._helper(playlist_id=PlaylistID("e"), name="name (3)", num_followers=0),
            self._helper(
                playlist_id=PlaylistID("f"),
                name="name (3) (2)",
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
                self._fake_get_playlist(PlaylistID("a")),
                self._fake_get_playlist(PlaylistID("b")),
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
            spotify_playlist = self._helper(
                playlist_id=PlaylistID(playlist_id),
                name=f" name_{playlist_id} ",  # ensure whitespace is stripped
                num_followers=0,
            )
            playlist = Playlist(
                url=spotify_playlist.url,
                original_name=spotify_playlist.name,
                unique_name=spotify_playlist.name,
                description=spotify_playlist.description,
                tracks=[
                    Track(
                        url=track.url,
                        name=track.name,
                        album=Album(
                            url=track.album.url,
                            name=track.album.name,
                        ),
                        artists=[
                            Artist(
                                url=artist.url,
                                name=artist.name,
                            )
                            for artist in track.artists
                        ],
                        duration_ms=track.duration_ms,
                        added_at=track.added_at,
                    )
                    for track in spotify_playlist.tracks
                ],
                snapshot_id=spotify_playlist.snapshot_id,
                num_followers=spotify_playlist.num_followers,
                owner=Owner(
                    url=spotify_playlist.owner.url,
                    name=spotify_playlist.owner.name,
                ),
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
        with open(self.playlists_dir / "registry" / "abc", "w"):
            pass

        # Create a fake playlist
        playlist = SpotifyPlaylist(
            url="playlist_url",
            name="playlist_name",
            description="playlist_description",
            tracks=[
                SpotifyTrack(
                    url="trackurl",
                    name="track_name",
                    album=SpotifyAlbum(
                        url="album_url",
                        name="album_name",
                    ),
                    artists=[
                        SpotifyArtist(
                            url="artist_one_url",
                            name="artist_one_name",
                        ),
                        SpotifyArtist(
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
            owner=SpotifyOwner(
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
                      "name": "playlist_name",
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

### [playlist\_name](playlist_url)

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
                r"""playlist_name
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
                      "original_name": "playlist_name",
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
                      "unique_name": "playlist_name",
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

### [playlist\_name](playlist_url)

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

    async def test_skip_cumulative_playlists(self) -> None:
        # Assert that the playlists directory starts out as empty
        self.assertEqual(sorted(self.playlists_dir.rglob("*")), [])

        # Create the registry directory
        self.playlists_dir.mkdir()
        registry_dir = self.playlists_dir / "registry"
        registry_dir.mkdir()

        # Register a playlist
        with open(registry_dir / "abc", "w"):
            pass

        # Create a fake playlist
        playlist = SpotifyPlaylist(
            url="playlist_url",
            name="playlist_original_name",
            description="playlist_description",
            tracks=[
                SpotifyTrack(
                    url="trackurl",
                    name="track_name",
                    album=SpotifyAlbum(
                        url="album_url",
                        name="album_name",
                    ),
                    artists=[
                        SpotifyArtist(
                            url="artist_one_url",
                            name="artist_one_name",
                        ),
                        SpotifyArtist(
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
            owner=SpotifyOwner(
                url="owner_url",
                name="owner_name",
            ),
        )
        self.mock_spotify.get_playlist.side_effect = [playlist]

        await self._update_files_impl(skip_cumulative_playlists=True)

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


class TestUpdatePlayHistory(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        repo_dir = pathlib.Path(self.temp_dir.name)
        playlists_dir = repo_dir / "playlists"
        self.history_dir = playlists_dir / "history"

        # Mock the spotify class
        mock_spotify_class = UnittestUtils.patch(
            self,
            "file_updater.Spotify",
            new_callable=Mock,
        )

        # Use AsyncMocks for async methods
        self.mock_spotify = mock_spotify_class.return_value
        self.mock_spotify.get_recently_played_tracks = AsyncMock()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def _update_play_history(self) -> None:
        await FileUpdater._update_play_history(
            history_dir=self.history_dir,
            spotify=self.mock_spotify,
        )

    # Just a helper function for getting a RecentlyPlayedTrack object to make
    # the test cases below more succinct
    def _get_recently_played_track(
        self,
        track_id: str,
        played_at: datetime.datetime,
    ) -> SpotifyRecentlyPlayedTrack:
        return SpotifyRecentlyPlayedTrack(
            url=f"track_url_{track_id}",
            name=f"track_name_{track_id}",
            album=SpotifyAlbum(
                url=f"album_url_{track_id}",
                name=f"album_name_{track_id}",
            ),
            artists=[
                SpotifyArtist(
                    url=f"artist_url_{track_id}",
                    name=f"artist_name_{track_id}",
                ),
            ],
            popularity=25,
            duration_ms=12345,
            context_type=f"context_type_{track_id}",
            context_url=f"context_url_{track_id}",
            played_at=played_at,
        )

    async def test_empty(self) -> None:
        self.assertFalse(self.history_dir.exists())
        await self._update_play_history()
        self.assertTrue(self.history_dir.exists())
        # Double check exist_ok = True
        await self._update_play_history()

    async def test_request_failed(self) -> None:
        self.mock_spotify.get_recently_played_tracks.side_effect = RequestFailedError
        # Uncategorized request failures should terminate the program
        with self.assertRaises(RequestFailedError):
            await self._update_play_history()

    async def test_existing_file_wrong_date(self) -> None:
        # Create the history directory
        await self._update_play_history()

        # Create a dummy file with bad data (mismatch date)
        with open(self.history_dir / "2025-04-01.json", "w") as f:
            f.write(
                textwrap.dedent(
                    """\
                    {
                      "date": "2025-03-31",
                      "tracks": [
                        {
                          "album": {
                            "name": "album_name",
                            "url": "album_url"
                          },
                          "artists": [
                            {
                              "name": "artist_name",
                              "url": "artist_url"
                            }
                          ],
                          "context_type": "context_type",
                          "context_url": "context_url",
                          "duration_ms": 12345,
                          "name": "track_name",
                          "played_at": "2025-03-31T00:00:00.000000Z",
                          "popularity": 25,
                          "url": "track_url"
                        }
                      ]
                    }
                    """
                ),
            )

        # Have Spotify return a track corresponding to the bad file
        self.mock_spotify.get_recently_played_tracks.return_value = [
            self._get_recently_played_track(
                track_id="a", played_at=datetime.datetime(year=2025, month=4, day=1)
            )
        ]

        # Make sure an error is raised
        with self.assertRaises(RuntimeError):
            await self._update_play_history()

    async def test_existing_file_unsorted_tracks(self) -> None:
        # Create the history directory
        await self._update_play_history()

        # Create a dummy file with bad data (unsorted tracks)
        with open(self.history_dir / "2025-04-01.json", "w") as f:
            f.write(
                textwrap.dedent(
                    """\
                    {
                      "date": "2025-03-31",
                      "tracks": [
                        {
                          "album": {
                            "name": "album_name_foo",
                            "url": "album_url_foo"
                          },
                          "artists": [
                            {
                              "name": "artist_name_foo",
                              "url": "artist_url_foo"
                            }
                          ],
                          "context_type": "context_type_foo",
                          "context_url": "context_url_foo",
                          "duration_ms": 12345,
                          "name": "track_name_foo",
                          "played_at": "2025-04-01T00:00:00.000001Z",
                          "popularity": 25,
                          "url": "track_url_foo"
                        },
                        {
                          "album": {
                            "name": "album_name_bar",
                            "url": "album_url_bar"
                          },
                          "artists": [
                            {
                              "name": "artist_name_bar",
                              "url": "artist_url_bar"
                            }
                          ],
                          "context_type": "context_type_bar",
                          "context_url": "context_url_bar",
                          "duration_ms": 12345,
                          "name": "track_name_bar",
                          "played_at": "2025-04-01T00:00:00.000000Z",
                          "popularity": 25,
                          "url": "track_url_bar"
                        }
                      ]
                    }
                    """
                ),
            )

        # Have Spotify return a track corresponding to the bad file
        self.mock_spotify.get_recently_played_tracks.return_value = [
            self._get_recently_played_track(
                track_id="a", played_at=datetime.datetime(year=2025, month=4, day=1)
            )
        ]

        # Make sure an error is raised
        with self.assertRaises(RuntimeError):
            await self._update_play_history()

    async def test_success(self) -> None:
        # Assert that the history directory starts out as empty
        self.assertEqual(sorted(self.history_dir.rglob("*")), [])

        # The first call to get_recently_played_tracks returns a single track
        self.mock_spotify.get_recently_played_tracks.return_value = [
            self._get_recently_played_track(
                track_id="a",
                played_at=datetime.datetime(year=2025, month=4, day=1, hour=12),
            )
        ]

        # Call the method being tested
        await self._update_play_history()

        # Make sure the expected file exists
        self.assertEqual(
            sorted(self.history_dir.rglob("*")),
            [
                self.history_dir / "2025-04-01.json",
                self.history_dir / "2025-04-01.md",
            ],
        )

        # Now check the contents of each file
        with open(self.history_dir / "2025-04-01.json") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    {
                      "date": "2025-04-01",
                      "tracks": [
                        {
                          "album": {
                            "name": "album_name_a",
                            "url": "album_url_a"
                          },
                          "artists": [
                            {
                              "name": "artist_name_a",
                              "url": "artist_url_a"
                            }
                          ],
                          "context_type": "context_type_a",
                          "context_url": "context_url_a",
                          "duration_ms": 12345,
                          "name": "track_name_a",
                          "played_at": "2025-04-01T12:00:00.000000Z",
                          "popularity": 25,
                          "url": "track_url_a"
                        }
                      ]
                    }
                    """
                ),
            )

        with open(self.history_dir / "2025-04-01.md") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    | Played At | Title | Artist(s) | Album | Length |
                    |---|---|---|---|---|
                    | 12:00 PM | [track\\_name\\_a](track_url_a) | [artist\\_name\\_a](artist_url_a) | [album\\_name\\_a](album_url_a) | 0:12 |
                    """
                ),
            )

        # The second call to get_recently_played_tracks returns multiple
        # tracks, intentionally out of order to test grouping and sorting
        self.mock_spotify.get_recently_played_tracks.return_value = [
            # A newer day than before
            self._get_recently_played_track(
                track_id="b", played_at=datetime.datetime(year=2025, month=4, day=2)
            ),
            # An older day than before
            self._get_recently_played_track(
                track_id="c",
                played_at=datetime.datetime(year=2025, month=3, day=31),
            ),
            # The same day as before, exact same time
            self._get_recently_played_track(
                track_id="d",
                played_at=datetime.datetime(year=2025, month=4, day=1, hour=12),
            ),
            # The same day as before, 1 hour earlier
            self._get_recently_played_track(
                track_id="e",
                played_at=datetime.datetime(year=2025, month=4, day=1, hour=11),
            ),
            # The same day as before, 1 hour later
            self._get_recently_played_track(
                track_id="f",
                played_at=datetime.datetime(year=2025, month=4, day=1, hour=13),
            ),
        ]

        # Call the method being tested
        await self._update_play_history()

        # Make sure the expected files exist
        self.assertEqual(
            sorted(self.history_dir.rglob("*")),
            [
                self.history_dir / "2025-03-31.json",
                self.history_dir / "2025-03-31.md",
                self.history_dir / "2025-04-01.json",
                self.history_dir / "2025-04-01.md",
                self.history_dir / "2025-04-02.json",
                self.history_dir / "2025-04-02.md",
            ],
        )

        # Now check the contents of each file
        with open(self.history_dir / "2025-03-31.json") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    {
                      "date": "2025-03-31",
                      "tracks": [
                        {
                          "album": {
                            "name": "album_name_c",
                            "url": "album_url_c"
                          },
                          "artists": [
                            {
                              "name": "artist_name_c",
                              "url": "artist_url_c"
                            }
                          ],
                          "context_type": "context_type_c",
                          "context_url": "context_url_c",
                          "duration_ms": 12345,
                          "name": "track_name_c",
                          "played_at": "2025-03-31T00:00:00.000000Z",
                          "popularity": 25,
                          "url": "track_url_c"
                        }
                      ]
                    }
                    """
                ),
            )

        with open(self.history_dir / "2025-03-31.md") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    | Played At | Title | Artist(s) | Album | Length |
                    |---|---|---|---|---|
                    | 12:00 AM | [track\\_name\\_c](track_url_c) | [artist\\_name\\_c](artist_url_c) | [album\\_name\\_c](album_url_c) | 0:12 |
                    """
                ),
            )

        with open(self.history_dir / "2025-04-01.json") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    {
                      "date": "2025-04-01",
                      "tracks": [
                        {
                          "album": {
                            "name": "album_name_a",
                            "url": "album_url_a"
                          },
                          "artists": [
                            {
                              "name": "artist_name_a",
                              "url": "artist_url_a"
                            }
                          ],
                          "context_type": "context_type_a",
                          "context_url": "context_url_a",
                          "duration_ms": 12345,
                          "name": "track_name_a",
                          "played_at": "2025-04-01T12:00:00.000000Z",
                          "popularity": 25,
                          "url": "track_url_a"
                        },
                        {
                          "album": {
                            "name": "album_name_f",
                            "url": "album_url_f"
                          },
                          "artists": [
                            {
                              "name": "artist_name_f",
                              "url": "artist_url_f"
                            }
                          ],
                          "context_type": "context_type_f",
                          "context_url": "context_url_f",
                          "duration_ms": 12345,
                          "name": "track_name_f",
                          "played_at": "2025-04-01T13:00:00.000000Z",
                          "popularity": 25,
                          "url": "track_url_f"
                        }
                      ]
                    }
                    """
                ),
            )

        with open(self.history_dir / "2025-04-01.md") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    | Played At | Title | Artist(s) | Album | Length |
                    |---|---|---|---|---|
                    | 12:00 PM | [track\\_name\\_a](track_url_a) | [artist\\_name\\_a](artist_url_a) | [album\\_name\\_a](album_url_a) | 0:12 |
                    | 1:00 PM | [track\\_name\\_f](track_url_f) | [artist\\_name\\_f](artist_url_f) | [album\\_name\\_f](album_url_f) | 0:12 |
                    """
                ),
            )

        with open(self.history_dir / "2025-04-02.json") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    {
                      "date": "2025-04-02",
                      "tracks": [
                        {
                          "album": {
                            "name": "album_name_b",
                            "url": "album_url_b"
                          },
                          "artists": [
                            {
                              "name": "artist_name_b",
                              "url": "artist_url_b"
                            }
                          ],
                          "context_type": "context_type_b",
                          "context_url": "context_url_b",
                          "duration_ms": 12345,
                          "name": "track_name_b",
                          "played_at": "2025-04-02T00:00:00.000000Z",
                          "popularity": 25,
                          "url": "track_url_b"
                        }
                      ]
                    }
                    """
                ),
            )

        with open(self.history_dir / "2025-04-02.md") as f:
            self.assertEqual(
                f.read(),
                textwrap.dedent(
                    """\
                    | Played At | Title | Artist(s) | Album | Length |
                    |---|---|---|---|---|
                    | 12:00 AM | [track\\_name\\_b](track_url_b) | [artist\\_name\\_b](artist_url_b) | [album\\_name\\_b](album_url_b) | 0:12 |
                    """
                ),
            )
