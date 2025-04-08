#!/usr/bin/env python3


import collections
import datetime
import difflib
import logging
import pathlib
from typing import Any, Dict, NamedTuple, Optional, Set, TypeVar

import brotli

from file_formatter import Formatter
from file_manager import FileManager
from git_utils import GitUtils
from plants.cache import Cache
from plants.environment import Environment
from plants.logging import Color
from playlist_id import PlaylistID
from playlist_types import CumulativePlaylist, PlayHistoryForDate, Playlist
from spotify import (
    InvalidDataError,
    RequestRetryBudgetExceededError,
    ResourceNotFoundError,
    RetryBudget,
    Spotify,
)

logger: logging.Logger = logging.getLogger(__name__)


T = TypeVar("T", str, bytes)


class FileChanges(NamedTuple):
    num_lines_in_old_version: int
    num_lines_in_new_version: int
    num_lines_kept: int
    num_lines_added: int
    num_lines_removed: int
    # num_lines_in_new_version / num_lines_in_old_version
    growth_fraction: float
    # num_lines_kept / num_lines_in_old_version
    fraction_of_lines_kept: float
    # num_lines_removed / num_lines_in_old_version
    fraction_of_lines_removed: float


class FileUpdater:
    @classmethod
    async def update_files(
        cls,
        *,
        now: datetime.datetime,
        file_manager: Optional[FileManager] = None,
        spotify_cache: Optional[Cache[str, Dict[str, Any]]] = None,
        auto_register: bool,
        skip_cumulative_playlists: bool,
        history_dir: Optional[pathlib.Path] = None,
    ) -> None:
        async with Spotify(
            client_id=Environment.get_env("SPOTIFY_CLIENT_ID") or "",
            client_secret=Environment.get_env("SPOTIFY_CLIENT_SECRET") or "",
            # If specified, attempt to fetch playlists as a particular user
            refresh_token=Environment.get_env("SPOTIFY_REFRESH_TOKEN") or "",
            cache=spotify_cache,
        ) as spotify:
            if file_manager:
                await cls._update_files_impl(
                    now=now,
                    file_manager=file_manager,
                    spotify=spotify,
                    auto_register=auto_register,
                    skip_cumulative_playlists=skip_cumulative_playlists,
                )
            if history_dir:
                await cls._update_play_history(history_dir=history_dir, spotify=spotify)

    @classmethod
    async def _update_files_impl(
        cls,
        now: datetime.datetime,
        file_manager: FileManager,
        spotify: Spotify,
        *,
        auto_register: bool,
        skip_cumulative_playlists: bool,
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
            progress_percent = f"{progress_fraction:.2%}".rjust(5)
            mins, secs = divmod((datetime.datetime.now() - now).total_seconds(), 60)
            logger.info(
                f"({numerator} / {denominator} - {progress_percent} "
                f"- {int(mins)}m {int(secs):02}s) {playlist_id}"
            )
            try:
                playlists[playlist_id] = await spotify.get_playlist(
                    playlist_id,
                    alias=registered_playlists[playlist_id],
                    retry_budget=RetryBudget(seconds=5),
                )
            # Skip deleted playlists and playlists with invalid data
            except (
                InvalidDataError,
                ResourceNotFoundError,
                RequestRetryBudgetExceededError,
            ) as e:
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

        # Keep track of changes to plain files
        plain_file_changes: Dict[PlaylistID, FileChanges] = {}

        # Process the playlists
        logger.info(f"Updating {len(playlists_to_update)} playlist(s)...")
        for playlist_id, playlist in sorted(playlists_to_update.items()):
            assert isinstance(playlist, Playlist)
            logger.info(f"{Color.LIGHT_YELLOW(playlist_id)}")
            logger.info(f"  Unique name: {Color.LIGHT_PURPLE(playlist.unique_name)}")

            # Update plain playlist
            file_changes = cls._maybe_update_file(
                path=file_manager.get_plain_path(playlist_id),
                content=Formatter.plain(playlist_id, playlist),
                compute_file_changes=True,
            )
            assert file_changes  # for pyre
            plain_file_changes[playlist_id] = file_changes

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

            today = now.date()
            if not skip_cumulative_playlists:
                # Update cumulative JSON
                cumulative_json_path = file_manager.get_cumulative_json_path(
                    playlist_id
                )
                prev_cumulative_json_content = cls._get_file_content_or_empty_string(
                    cumulative_json_path
                )
                if prev_cumulative_json_content:
                    prev_struct = CumulativePlaylist.from_json(
                        prev_cumulative_json_content
                    )
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
            followers_json_path = file_manager.get_followers_json_path(playlist_id)
            prev_followers_json_content = cls._get_file_content_or_empty_string(
                followers_json_path
            )
            cls._maybe_update_file(
                path=followers_json_path,
                content=Formatter.followers_json(
                    prev_content=prev_followers_json_content,
                    today=today,
                    num_followers=playlist.num_followers,
                ),
            )

            # Print out plain file changes
            old_num_lines = f"{file_changes.num_lines_in_old_version}"
            new_num_lines = f"{file_changes.num_lines_in_new_version}"
            growth_fraction = f"{file_changes.growth_fraction:.3f}"
            logger.info(
                f"  Changes: {Color.LIGHT_GRAY(new_num_lines)} / "
                f"{Color.LIGHT_GRAY(old_num_lines)} = "
                f"{Color.TURQUOISE(growth_fraction)} "
                f"(~{Color.LIGHT_BLUE(str(file_changes.num_lines_kept))}, "
                f"+{Color.LIGHT_GREEN(str(file_changes.num_lines_added))}, "
                f"-{Color.LIGHT_RED(str(file_changes.num_lines_removed))})"
            )

        # Check for unexpected files in playlist directories
        file_manager.ensure_no_unexpected_files()

        # Print out playlists with changes in size
        logger.info("Playlists that changed size")
        flag = False
        for playlist_id, file_changes in sorted(
            plain_file_changes.items(),
            key=lambda pair: (-1 * pair[1].growth_fraction, pair[0]),
        ):
            if file_changes.growth_fraction != 1:
                logger.info(f"  {playlist_id}: {file_changes.growth_fraction:.3f}")
                flag = True
        if not flag:
            logger.info("  None")

        # Print out empty playlists, hopefully none
        empty_playlists = sorted(
            [
                playlist_id
                for playlist_id, file_changes in plain_file_changes.items()
                if file_changes.num_lines_in_new_version == 0
            ]
        )
        logger.info("Empty playlists")
        if empty_playlists:
            for playlist_id in empty_playlists:
                logger.info(f"  {playlist_id}")
        else:
            logger.info("  None")

        # Update all metadata files
        logger.info("Updating metadata")
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
            logger.debug(f"  No changes to file: {path}")
            return
        logger.debug(f"  Writing updates to file: {path}")
        if isinstance(content, bytes):
            with open(path, "wb") as f:
                f.write(content)
        elif isinstance(content, str):
            with open(path, "w") as f:
                f.write(content)
        else:
            raise RuntimeError(f"Invalid content type: {type(content)}")

    @classmethod
    def _maybe_update_file(
        cls, path: pathlib.Path, content: T, *, compute_file_changes: bool = False
    ) -> Optional[FileChanges]:
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
        if compute_file_changes:
            return cls._get_file_changes(prev_content, content)

    @classmethod
    def _get_file_changes(cls, old_content: str, new_content: str) -> FileChanges:
        old_lines = old_content.splitlines()
        new_lines = new_content.splitlines()

        differ = difflib.Differ()
        diff = differ.compare(old_lines, new_lines)

        num_lines_added = 0
        num_lines_removed = 0
        num_lines_in_both = 0
        for line in diff:
            if line[0] == "+":
                num_lines_added += 1
            elif line[0] == "-":
                num_lines_removed += 1
            elif line[0] == " ":
                num_lines_in_both += 1

        if old_lines:
            growth_fraction = len(new_lines) / len(old_lines)
            fraction_of_lines_kept = num_lines_in_both / len(old_lines)
            fraction_of_lines_removed = num_lines_removed / len(old_lines)
        else:
            growth_fraction = 1
            fraction_of_lines_kept = 1
            fraction_of_lines_removed = 0
        return FileChanges(
            num_lines_in_old_version=len(old_lines),
            num_lines_in_new_version=len(new_lines),
            num_lines_kept=num_lines_in_both,
            num_lines_added=num_lines_added,
            num_lines_removed=num_lines_removed,
            growth_fraction=growth_fraction,
            fraction_of_lines_kept=fraction_of_lines_kept,
            fraction_of_lines_removed=fraction_of_lines_removed,
        )

    @classmethod
    def _get_history_json_path(
        cls,
        history_dir: pathlib.Path,
        date: datetime.date,
    ) -> pathlib.Path:
        return history_dir / f"{date}.json"

    @classmethod
    async def _update_play_history(
        cls,
        history_dir: pathlib.Path,
        spotify: Spotify,
    ) -> None:
        # Ensure the output directory exists
        history_dir.mkdir(parents=True, exist_ok=True)

        # Get recently played tracks
        logger.info("Getting recently played tracks")
        recently_played_tracks = await spotify.get_recently_played_tracks()
        recently_played_tracks_by_date = collections.defaultdict(list)
        for track in sorted(recently_played_tracks, key=lambda track: track.played_at):
            recently_played_tracks_by_date[track.played_at.date()].append(track)

        logger.info(f"Got {len(recently_played_tracks)} recenty played tracks")
        for date, tracks in sorted(recently_played_tracks_by_date.items()):
            logger.debug(f"  {date}: {len(tracks)}")

        # Read those dates' history into memory
        date_to_play_history = {}
        for date in sorted(recently_played_tracks_by_date.keys()):
            path = cls._get_history_json_path(history_dir, date)
            prev_content = cls._get_file_content_or_empty_string(path)
            if prev_content:
                prev_struct = PlayHistoryForDate.from_json(prev_content)
            else:
                prev_struct = PlayHistoryForDate(
                    date=date,
                    tracks=[],
                )
            if prev_struct.date != date:
                raise RuntimeError(f"Date field doesn't match filename: {path}")
            date_to_play_history[date] = prev_struct

        # Update existing structs with new data
        for date, prev_struct in sorted(date_to_play_history.items()):
            date_to_play_history[date] = prev_struct.update(
                recently_played_tracks_by_date[date]
            )

        # Write the new structs to the files
        for date, new_struct in date_to_play_history.items():
            cls._maybe_update_file(
                path=cls._get_history_json_path(history_dir, date),
                content=new_struct.to_json() + "\n",
            )
