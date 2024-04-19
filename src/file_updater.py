#!/usr/bin/env python3

import collections
import datetime
import logging
import pathlib
from typing import Dict, Optional, Set, TypeVar

import brotli

from file_formatter import Formatter
from file_manager import FileManager
from git_utils import GitUtils
from plants.environment import Environment
from playlist_id import PlaylistID
from playlist_types import CumulativePlaylist, Playlist
from spotify import ResourceNotFoundError, Spotify

logger: logging.Logger = logging.getLogger(__name__)


T = TypeVar("T", str, bytes)


class FileUpdater:
    @classmethod
    async def update_files(
        cls,
        now: datetime.datetime,
        file_manager: FileManager,
        auto_register: bool,
    ) -> None:
        # Check nonempty to fail fast
        client_id = Environment.get_env("SPOTIFY_CLIENT_ID")
        client_secret = Environment.get_env("SPOTIFY_CLIENT_SECRET")
        assert client_id and client_secret

        async with Spotify(
            client_id=client_id,
            client_secret=client_secret,
        ) as spotify:
            await cls._update_files_impl(
                now=now,
                file_manager=file_manager,
                auto_register=auto_register,
                spotify=spotify,
            )

    @classmethod
    async def _update_files_impl(
        cls,
        now: datetime.datetime,
        file_manager: FileManager,
        auto_register: bool,
        spotify: Spotify,
    ) -> None:
        # Ensure the output directories exist
        file_manager.ensure_subdirs_exist()

        # Optimization: if the last commit only touched the registry, only the
        # touched playlists will generate downstream changes, so only fetch
        # those playlists. This makes adding new playlists cheap.
        logger.info("Checking if last commit was registry-only")
        only_fetch_these_playlists: Optional[Set[PlaylistID]] = None
        last_commit_content = GitUtils.get_last_commit_content()
        # To prevent suprising behavior when testing locally, only perform the
        # optimization if the script was triggered by a GitHub push
        if Environment.is_push_github_action() and all(
            path.startswith("playlists/registry") for path in last_commit_content
        ):
            only_fetch_these_playlists = {
                PlaylistID(pathlib.Path(path).name) for path in last_commit_content
            }
            logger.info(f"Only fetch these playlists: {only_fetch_these_playlists}")

        # Automatically register select playlists
        if auto_register and not only_fetch_these_playlists:
            try:
                await cls._auto_register(spotify, file_manager)
            except Exception:
                logger.exception("Failed to auto-register playlists")

        file_manager.fixup_aliases()
        registered_playlists = file_manager.get_registered_playlists()

        # Read existing playlist data, useful if Spotify fetch fails
        playlists: Dict[PlaylistID, Playlist] = {}
        for playlist_id in sorted(registered_playlists):
            path = file_manager.get_pretty_json_path(playlist_id)
            prev_content = cls._get_file_content_or_empty_string(path)
            if prev_content:
                playlists[playlist_id] = Playlist.from_json(prev_content)

        # Update playlist data from Spotify
        playlists_to_fetch = sorted(only_fetch_these_playlists or registered_playlists)
        num_unfetchable = 0
        logger.info(f"Fetching {len(playlists_to_fetch)} playlist(s)...")
        for i, playlist_id in enumerate(sorted(playlists_to_fetch)):
            denominator = str(len(playlists_to_fetch))
            numerator = str(i).rjust(len(denominator))
            progress_fraction = i / len(playlists_to_fetch)
            progress_percent = f"{progress_fraction:.1%}".rjust(5)
            mins, secs = divmod((datetime.datetime.now() - now).total_seconds(), 60)
            logger.info(
                f"({numerator} / {denominator} - {progress_percent} "
                f"- {int(mins)}m {int(secs):02}s) {playlist_id}"
            )
            try:
                playlists[playlist_id] = await spotify.get_playlist(
                    playlist_id, alias=registered_playlists[playlist_id]
                )
            # When playlists are deleted, the Spotify API returns 404; skip
            # those playlists (no updates) but retain them in the archive
            except ResourceNotFoundError as e:
                num_unfetchable += 1
                logger.warning(f"Failed to fetch playlist {playlist_id}: {e}")
        logger.info("Done fetching playlists")

        # Gracefully handle playlists with the same name
        original_playlist_names_to_ids = collections.defaultdict(set)
        for playlist_id, playlist in playlists.items():
            original_playlist_names_to_ids[playlist.original_name].add(playlist_id)
        duplicate_names: Dict[str, Set[PlaylistID]] = {
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
                while any(
                    other_playlist_id != playlist_id
                    and other_playlist.unique_name == unique_name
                    for other_playlist_id, other_playlist in playlists.items()
                ):
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
        logger.info(f"Updating {len(playlists_to_update)} playlist(s)...")
        for playlist_id, playlist in sorted(playlists_to_update.items()):
            assert isinstance(playlist, Playlist)
            logger.info(f"Playlist ID: {playlist_id}")
            logger.info(f"Playlist name: {playlist.unique_name}")

            # Update plain playlist
            cls._maybe_update_file(
                path=file_manager.get_plain_path(playlist_id),
                content=Formatter.plain(playlist_id, playlist),
            )

            # Update pretty JSON
            cls._maybe_update_file(
                path=file_manager.get_pretty_json_path(playlist_id),
                content=playlist.to_json() + "\n",
            )

            # Update pretty markdown
            cls._maybe_update_file(
                path=file_manager.get_pretty_markdown_path(playlist_id),
                content=Formatter.pretty(playlist_id, playlist),
            )

            # Update cumulative JSON
            today = now.date()
            cumulative_json_path = file_manager.get_cumulative_json_path(playlist_id)
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
            cls._maybe_update_file(
                path=cumulative_json_path,
                content=new_struct.to_json() + "\n",
            )

            # Update cumulative markdown
            cls._maybe_update_file(
                path=file_manager.get_cumulative_markdown_path(playlist_id),
                content=Formatter.cumulative(playlist_id, new_struct),
            )

            # Update followers JSON
            cls._maybe_update_file(
                path=file_manager.get_followers_json_path(playlist_id),
                content=Formatter.followers_json(
                    prev_content, today, playlist.num_followers
                ),
            )

        # Check for unexpected files in playlist directories
        file_manager.ensure_no_unexpected_files()

        # Update all metadata files
        logger.info("Metadata")
        metadata_full_json = Formatter.metadata_full_json(playlists)
        metadata_compact_json = Formatter.metadata_compact_json(playlists)
        cls._maybe_update_file(
            path=file_manager.get_index_path(),
            content=Formatter.index(playlists),
        )
        cls._maybe_update_file(
            path=file_manager.get_metadata_full_json_path(),
            content=metadata_full_json + "\n",
        )
        cls._maybe_update_file(
            path=file_manager.get_metadata_compact_json_path(),
            content=metadata_compact_json + "\n",
        )
        cls._maybe_update_file(
            path=file_manager.get_metadata_full_json_br_path(),
            content=brotli.compress(metadata_full_json.encode()),
        )
        cls._maybe_update_file(
            path=file_manager.get_metadata_compact_json_br_path(),
            content=brotli.compress(metadata_compact_json.encode()),
        )

        logger.info("Summary")
        num_attempted = len(playlists_to_fetch)
        logger.info(f"  Attempted to fetch: {num_attempted}")
        logger.info(f"  Fetch succeeded: {num_attempted - num_unfetchable}")
        logger.info(f"  Fetch failed: {num_unfetchable}")

    @classmethod
    async def _auto_register(cls, spotify: Spotify, file_manager: FileManager) -> None:
        playlist_ids = (
            await spotify.get_spotify_user_playlist_ids()
            | await spotify.get_featured_playlist_ids()
            | await spotify.get_category_playlist_ids()
        )
        file_manager.ensure_registered(playlist_ids)

    @classmethod
    def _get_file_content_or_empty_string(cls, path: pathlib.Path) -> str:
        try:
            with open(path, "r") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    @classmethod
    def _get_file_content_or_empty_bytes(cls, path: pathlib.Path) -> bytes:
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return b""

    @classmethod
    def _write_to_file_if_content_changed(
        cls, prev_content: T, content: T, path: pathlib.Path
    ) -> None:
        if content == prev_content:
            logger.info(f"  No changes to file: {path}")
            return
        logger.info(f"  Writing updates to file: {path}")
        if isinstance(content, bytes):
            with open(path, "wb") as f:
                f.write(content)
        elif isinstance(content, str):
            with open(path, "w") as f:
                f.write(content)
        else:
            raise RuntimeError(f"Invalid content type: {type(content)}")

    @classmethod
    def _maybe_update_file(cls, path: pathlib.Path, content: T) -> None:
        if isinstance(content, bytes):
            prev_content = cls._get_file_content_or_empty_bytes(path)
        elif isinstance(content, str):
            prev_content = cls._get_file_content_or_empty_string(path)
        else:
            raise RuntimeError(f"Invalid content type: {type(content)}")
        cls._write_to_file_if_content_changed(
            prev_content=prev_content,
            content=content,
            path=path,
        )
