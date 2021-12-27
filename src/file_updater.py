#!/usr/bin/env python3

import collections
import datetime
import logging
import pathlib
from typing import Dict, Mapping, Set

from environment import Environment
from file_formatter import Formatter
from playlist_id import PlaylistID
from playlist_types import CumulativePlaylist
from spotify import Spotify

logger: logging.Logger = logging.getLogger(__name__)


class MalformedAliasError(Exception):
    pass


class DuplicatePlaylistNamesError(Exception):
    pass


class UnexpectedFilesError(Exception):
    pass


class FileUpdater:
    @classmethod
    async def update_files(
        cls,
        now: datetime.datetime,
        playlists_dir: pathlib.Path,
        auto_register: bool,
        update_readme: bool,
    ) -> None:
        # Check nonempty to fail fast
        client_id = Environment.get_env("SPOTIFY_CLIENT_ID")
        client_secret = Environment.get_env("SPOTIFY_CLIENT_SECRET")
        assert client_id and client_secret

        # Initialize the Spotify client
        access_token = await Spotify.get_access_token(
            client_id=client_id, client_secret=client_secret
        )
        spotify = Spotify(access_token)
        try:
            await cls._update_files_impl(
                now=now,
                playlists_dir=playlists_dir,
                auto_register=auto_register,
                update_readme=update_readme,
                spotify=spotify,
            )
        finally:
            await spotify.shutdown()

    @classmethod
    async def _update_files_impl(
        cls,
        now: datetime.datetime,
        playlists_dir: pathlib.Path,
        auto_register: bool,
        update_readme: bool,
        spotify: Spotify,
    ) -> None:
        # Ensure the directories exist
        registry_dir = playlists_dir / "registry"
        plain_dir = playlists_dir / "plain"
        pretty_dir = playlists_dir / "pretty"
        cumulative_dir = playlists_dir / "cumulative"
        for path in [registry_dir, plain_dir, pretty_dir, cumulative_dir]:
            path.mkdir(parents=True, exist_ok=True)

        # Automatically register select playlists
        if auto_register:
            await cls._auto_register(registry_dir, spotify)

        # Determine which playlists to scrape from the files in
        # playlists/registry. This makes it easy to add new a playlist: just
        # touch an empty file like playlists/registry/<playlist_id> and this
        # script will handle the rest.
        playlist_ids: Mapping[PlaylistID, pathlib.Path] = {
            PlaylistID(path.name): path for path in registry_dir.iterdir()
        }

        # Aliases are alternative playlists names. They're useful for avoiding
        # naming collisions when archiving personalized playlists, which have the
        # same name for every user. To add an alias, add a single line
        # containing the desired name to playlists/registry/<playlist_id>
        cls._fixup_aliases(playlist_ids)
        aliases = cls._get_aliases(playlist_ids)

        playlist_names_to_ids: Dict[str, Set[PlaylistID]] = collections.defaultdict(set)
        for playlist_id in sorted(playlist_ids):
            plain_path = plain_dir / playlist_id
            pretty_md_path = pretty_dir / f"{playlist_id}.md"
            pretty_json_path = pretty_dir / f"{playlist_id}.json"
            cumulative_md_path = cumulative_dir / f"{playlist_id}.md"
            cumulative_json_path = cumulative_dir / f"{playlist_id}.json"

            # Get the data from Spotify
            logger.info(f"Fetching playlist: {playlist_id}")
            playlist = await spotify.get_playlist(playlist_id, aliases)
            logger.info(f"Playlist name: {playlist.name}")
            playlist_names_to_ids[playlist.name].add(playlist_id)

            # Update plain playlist
            prev_content = cls._get_file_content_or_empty_string(plain_path)
            content = Formatter.plain(playlist_id, playlist)
            cls._write_to_file_if_content_changed(
                prev_content=prev_content,
                content=content,
                path=plain_path,
            )

            # Update pretty JSON
            prev_content = cls._get_file_content_or_empty_string(pretty_json_path)
            cls._write_to_file_if_content_changed(
                prev_content=prev_content,
                content=playlist.to_json(),
                path=pretty_json_path,
            )

            # Update pretty markdown
            prev_content = cls._get_file_content_or_empty_string(pretty_md_path)
            content = Formatter.pretty(playlist_id, playlist)
            cls._write_to_file_if_content_changed(
                prev_content=prev_content,
                content=content,
                path=pretty_md_path,
            )

            # Update cumulative JSON
            today = now.date()
            prev_content = cls._get_file_content_or_empty_string(cumulative_json_path)
            if prev_content:
                prev_struct = CumulativePlaylist.from_json(prev_content)
            else:
                prev_struct = CumulativePlaylist(
                    url="",
                    name="",
                    description="",
                    tracks=[],
                    date_first_scraped=today,
                )
            new_struct = prev_struct.update(today, playlist)
            cls._write_to_file_if_content_changed(
                prev_content=prev_content,
                content=new_struct.to_json(),
                path=cumulative_json_path,
            )

            # Update cumulative markdown
            prev_content = cls._get_file_content_or_empty_string(cumulative_md_path)
            content = Formatter.cumulative(playlist_id, new_struct)
            cls._write_to_file_if_content_changed(
                prev_content=prev_content,
                content=content,
                path=cumulative_md_path,
            )

        # Check for duplicate playlist names
        duplicate_names = {
            name: playlist_ids
            for name, playlist_ids in playlist_names_to_ids.items()
            if len(playlist_ids) > 1
        }
        if duplicate_names:
            raise DuplicatePlaylistNamesError(
                f"Duplicate playlist names: {duplicate_names}"
            )

        # Reverse the mapping for later use
        playlist_ids_to_names = {
            list(playlist_ids)[0]: name
            for name, playlist_ids in playlist_names_to_ids.items()
        }

        # Check for unexpected files in playlist directories
        unexpected_files: Set[pathlib.Path] = set()
        for directory, suffixes in [
            (plain_dir, [""]),
            (pretty_dir, [".md", ".json"]),
            (cumulative_dir, [".md", ".json"]),
        ]:
            for path in directory.iterdir():
                if not any(
                    path.name.endswith(suffix)
                    and cls._remove_suffix(path.name, suffix) in playlist_ids
                    for suffix in suffixes
                ):
                    unexpected_files.add(path)
        if unexpected_files:
            raise UnexpectedFilesError(f"Unexpected files: {unexpected_files}")

        # Lastly, update README.md
        if update_readme:
            readme_path = playlists_dir.parent / "README.md"
            with open(readme_path, "r") as f:
                prev_content = f.read()
            content = Formatter.readme(prev_content, playlist_ids_to_names)
            cls._write_to_file_if_content_changed(prev_content, content, readme_path)

    @classmethod
    async def _auto_register(cls, registry_dir: pathlib.Path, spotify: Spotify) -> None:
        playlist_ids = sorted(
            await spotify.get_spotify_user_playlist_ids()
            | await spotify.get_featured_playlist_ids()
        )
        for playlist_id in playlist_ids:
            path = registry_dir / playlist_id
            if not path.exists():
                logger.info(f"Registering playlist: {playlist_id}")
                path.touch()

    @classmethod
    def _fixup_aliases(cls, playlist_ids: Mapping[PlaylistID, pathlib.Path]) -> None:
        # GitHub makes it easy to create files that look empty but actually
        # contain a single newline. Normalize them to simplify other logic.
        for playlist_id, path in sorted(playlist_ids.items()):
            with open(path, "r") as f:
                content = f.read()
            if content == "\n":
                logger.info(f"Truncating empty alias: {playlist_id}")
                with open(path, "w"):
                    pass

    @classmethod
    def _get_aliases(
        cls, playlist_ids: Mapping[PlaylistID, pathlib.Path]
    ) -> Dict[PlaylistID, str]:
        aliases: Dict[PlaylistID, str] = {}
        for playlist_id, path in sorted(playlist_ids.items()):
            with open(path, "r") as f:
                lines = f.read().splitlines()
            if not lines:
                continue
            if len(lines) != 1:
                raise MalformedAliasError(f"Malformed alias: {playlist_id}")
            alias = lines[0]
            assert alias, alias
            if alias.isspace():
                raise MalformedAliasError(f"Malformed alias: {playlist_id}")
            aliases[playlist_id] = alias
        return aliases

    @classmethod
    def _remove_suffix(cls, string: str, suffix: str) -> str:
        if not suffix:
            return string
        assert string.endswith(suffix)
        return string[: -len(suffix)]

    @classmethod
    def _get_file_content_or_empty_string(cls, path: pathlib.Path) -> str:
        try:
            with open(path, "r") as f:
                return "".join(f.readlines())
        except FileNotFoundError:
            return ""

    @classmethod
    def _write_to_file_if_content_changed(
        cls, prev_content: str, content: str, path: pathlib.Path
    ) -> None:
        if content == prev_content:
            logger.info(f"No changes to file: {path}")
            return
        logger.info(f"Writing updates to file: {path}")
        with open(path, "w") as f:
            f.write(content)
