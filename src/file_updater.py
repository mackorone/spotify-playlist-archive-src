#!/usr/bin/env python3

import collections
import datetime
import logging
import pathlib
from typing import Dict, Mapping, Optional, Set

from environment import Environment
from file_formatter import Formatter
from git_utils import GitUtils
from github import GitHub
from playlist_id import PlaylistID
from playlist_types import CumulativePlaylist, Playlist
from spotify import FailedRequestError, Spotify

logger: logging.Logger = logging.getLogger(__name__)


class MalformedAliasError(Exception):
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

        # Optimization: if the last commit only touched the registry, only the
        # touched playlists will generate downstream changes, so only fetch
        # those playlists. This makes adding new playlists cheap.
        logger.info("Checking if last commit was registry-only")
        only_fetch_these_playlists: Optional[Set[PlaylistID]] = None
        uncommitted_changes = GitUtils.any_uncommitted_changes()
        logger.info(f"- Uncommitted changes: {uncommitted_changes}")
        last_commit_content = GitUtils.get_last_commit_content()
        logger.info(f"- Last commit content: {last_commit_content}")
        # To prevent suprising behavior when testing locally, don't perform the
        # optimization if there are local changes
        if (not uncommitted_changes) and all(
            path.startswith("playlists/registry") for path in last_commit_content
        ):
            only_fetch_these_playlists = {
                PlaylistID(pathlib.Path(path).name) for path in last_commit_content
            }
            logger.info(f"Only fetch these playlists: {only_fetch_these_playlists}")

        # Automatically register select playlists
        if auto_register and only_fetch_these_playlists is not None:
            await cls._auto_register(registry_dir, spotify)

        # Determine which playlists to scrape from the files in
        # playlists/registry. This makes it easy to add new a playlist: just
        # touch an empty file like playlists/registry/<playlist_id> and this
        # script will handle the rest.
        playlist_id_to_path: Mapping[PlaylistID, pathlib.Path] = {
            PlaylistID(path.name): path for path in registry_dir.iterdir()
        }

        # Aliases are alternative playlists names. They're useful for avoiding
        # naming collisions when archiving personalized playlists, which have the
        # same name for every user. To add an alias, add a single line
        # containing the desired name to playlists/registry/<playlist_id>
        cls._fixup_aliases(playlist_id_to_path)
        aliases = cls._get_aliases(playlist_id_to_path)

        # Get data from GitHub
        published_cumulative_playlists = (
            await GitHub.get_published_cumulative_playlists()
        )

        # Read existing playlist data, useful if Spotify fetch fails
        playlists: Dict[PlaylistID, Playlist] = {}
        for playlist_id in sorted(playlist_id_to_path):
            path = pretty_dir / f"{playlist_id}.json"
            prev_content = cls._get_file_content_or_empty_string(path)
            if prev_content:
                playlists[playlist_id] = Playlist.from_json(prev_content)

        # Update playlist data from Spotify
        playlists_to_fetch = sorted(only_fetch_these_playlists or playlist_id_to_path)
        logger.info(f"Fetching {len(playlists_to_fetch)} playlist(s)...")
        for i, playlist_id in enumerate(sorted(playlists_to_fetch)):
            denominator = str(len(playlists_to_fetch))
            numerator = str(i).rjust(len(denominator))
            progress_fraction = i / len(playlists_to_fetch)
            progress_percent = f"{progress_fraction:.1%}".rjust(5)
            logger.info(
                f"({numerator} / {denominator} - {progress_percent}) {playlist_id}"
            )
            try:
                playlists[playlist_id] = await spotify.get_playlist(
                    playlist_id, aliases
                )
            # When playlists are deleted, the Spotify API returns 404; skip
            # those playlists (no updates) but retain them in the archive
            except FailedRequestError:
                logger.warning(f"Failed to fetch playlist: {playlist_id}")
        logger.info("Done fetching playlists")

        # Gracefully handle playlists with the same name
        original_playlist_names_to_ids = collections.defaultdict(set)
        for playlist_id, playlist in playlists.items():
            original_playlist_names_to_ids[playlist.original_name].add(playlist_id)
        duplicate_names = {
            name: playlist_ids
            for name, playlist_ids in original_playlist_names_to_ids.items()
            if len(playlist_ids) > 1
        }
        if duplicate_names:
            logger.info("Handling duplicate names")
        for original_name, playlist_ids in sorted(duplicate_names.items()):
            sorted_by_num_followers = sorted(
                playlist_ids,
                # Sort by num_followers desc, playlist_id asc
                key=lambda playlist_id: (
                    -1 * (playlists[playlist_id].num_followers or 0),
                    playlist_id,
                ),
            )
            for i, playlist_id in enumerate(sorted_by_num_followers):
                if i == 0:
                    logger.info(f"  {playlist_id}: {original_name}")
                    continue
                suffix = 2
                unique_name = f"{original_name} ({suffix})"
                while any(p.unique_name == unique_name for p in playlists.values()):
                    suffix += 1
                    unique_name = f"{original_name} ({suffix})"
                logger.info(f"  {playlist_id}: {unique_name}")
                playlist = playlists[playlist_id]
                playlists[playlist_id] = Playlist(
                    url=playlist.url,
                    original_name=original_name,
                    unique_name=unique_name,
                    description=playlist.description,
                    tracks=playlist.tracks,
                    snapshot_id=playlist.snapshot_id,
                    num_followers=playlist.num_followers,
                    owner=playlist.owner,
                )

        # If we only fetched certain playlists, we only need to update those
        # playlists along with any playlists that share the same name (their
        # unique names may have changed)
        if only_fetch_these_playlists:
            possibly_affected_playlists = only_fetch_these_playlists
            for original_name, playlist_ids in duplicate_names.items():
                # If any intersection, include all playlists
                if only_fetch_these_playlists & playlist_ids:
                    possibly_affected_playlists |= playlist_ids
            playlists_to_update = {
                playlist_id: playlist
                for playlist_id, playlist in playlists.items()
                if playlist_id in possibly_affected_playlists
            }
        else:
            playlists_to_update = playlists

        # Process the playlists
        logger.info(f"Updating {len(playlists_to_update)} playlists...")
        for playlist_id, playlist in sorted(playlists_to_update.items()):
            logger.info(f"Playlist ID: {playlist_id}")
            logger.info(f"Playlist name: {playlist.unique_name}")

            plain_path = plain_dir / playlist_id
            pretty_md_path = pretty_dir / f"{playlist_id}.md"
            pretty_json_path = pretty_dir / f"{playlist_id}.json"
            cumulative_md_path = cumulative_dir / f"{playlist_id}.md"
            cumulative_json_path = cumulative_dir / f"{playlist_id}.json"

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
                    published_playlist_ids=[],
                )
            published_ids = published_cumulative_playlists.get(playlist_id) or []
            new_struct = prev_struct.update(today, playlist, published_ids)
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
                    and cls._remove_suffix(path.name, suffix) in playlist_id_to_path
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
            content = Formatter.readme(prev_content, playlists)
            cls._write_to_file_if_content_changed(prev_content, content, readme_path)

    @classmethod
    async def _auto_register(cls, registry_dir: pathlib.Path, spotify: Spotify) -> None:
        playlist_ids = sorted(
            await spotify.get_spotify_user_playlist_ids()
            | await spotify.get_featured_playlist_ids()
            | await spotify.get_category_playlist_ids()
        )
        for playlist_id in playlist_ids:
            path = registry_dir / playlist_id
            if not path.exists():
                logger.info(f"Registering playlist: {playlist_id}")
                path.touch()

    @classmethod
    def _fixup_aliases(
        cls, playlist_id_to_path: Mapping[PlaylistID, pathlib.Path]
    ) -> None:
        # GitHub makes it easy to create files that look empty but actually
        # contain a single newline. Normalize them to simplify other logic.
        for playlist_id, path in sorted(playlist_id_to_path.items()):
            with open(path, "r") as f:
                content = f.read()
            if content == "\n":
                logger.info(f"Truncating empty alias: {playlist_id}")
                with open(path, "w"):
                    pass

    @classmethod
    def _get_aliases(
        cls, playlist_id_to_path: Mapping[PlaylistID, pathlib.Path]
    ) -> Dict[PlaylistID, str]:
        aliases: Dict[PlaylistID, str] = {}
        for playlist_id, path in sorted(playlist_id_to_path.items()):
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
            logger.info(f"  No changes to file: {path}")
            return
        logger.info(f"  Writing updates to file: {path}")
        with open(path, "w") as f:
            f.write(content)
