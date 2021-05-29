#!/usr/bin/env python3

import argparse
import asyncio
import datetime
import logging
import os
import re
import subprocess
from typing import Dict, List, Optional, Sequence

from external import allow_external_calls
from spotify import InvalidPlaylistError, Playlist, PrivatePlaylistError, Spotify, Track
from url import URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger: logging.Logger = logging.getLogger(__name__)


class Formatter:

    TRACK_NO = "No."
    TITLE = "Title"
    ARTISTS = "Artist(s)"
    ALBUM = "Album"
    LENGTH = "Length"
    ADDED = "Added"
    REMOVED = "Removed"

    ARTIST_SEPARATOR = ", "
    LINK_REGEX = r"\[(.+?)\]\(.+?\)"

    @classmethod
    def plain(cls, playlist_id: str, playlist: Playlist) -> str:
        lines = [cls._plain_line_from_track(track) for track in playlist.tracks]
        # Sort alphabetically to minimize changes when tracks are reordered
        sorted_lines = sorted(lines, key=lambda line: line.lower())
        header = [playlist.name, playlist.description, ""]
        return "\n".join(header + sorted_lines)

    @classmethod
    def pretty(cls, playlist_id: str, playlist: Playlist) -> str:
        columns = [
            cls.TRACK_NO,
            cls.TITLE,
            cls.ARTISTS,
            cls.ALBUM,
            cls.LENGTH,
        ]

        vertical_separators = ["|"] * (len(columns) + 1)
        line_template = " {} ".join(vertical_separators)
        divider_line = "---".join(vertical_separators)
        lines = cls._markdown_header_lines(
            playlist_name=playlist.name,
            playlist_url=playlist.url,
            playlist_id=playlist_id,
            playlist_description=playlist.description,
            is_cumulative=False,
        )
        lines += [
            line_template.format(*columns),
            divider_line,
        ]

        for i, track in enumerate(playlist.tracks):
            lines.append(
                line_template.format(
                    i + 1,
                    cls._link(track.name, track.url),
                    cls.ARTIST_SEPARATOR.join(
                        [cls._link(artist.name, artist.url) for artist in track.artists]
                    ),
                    cls._link(track.album.name, track.album.url),
                    cls._format_duration(track.duration_ms),
                )
            )

        return "\n".join(lines)

    @classmethod
    def cumulative(
        cls,
        now: datetime.datetime,
        prev_content: str,
        playlist_id: str,
        playlist: Playlist,
    ) -> str:
        today = now.strftime("%Y-%m-%d")
        columns = [
            cls.TITLE,
            cls.ARTISTS,
            cls.ALBUM,
            cls.LENGTH,
            cls.ADDED,
            cls.REMOVED,
        ]

        vertical_separators = ["|"] * (len(columns) + 1)
        line_template = " {} ".join(vertical_separators)
        divider_line = "---".join(vertical_separators)
        header = cls._markdown_header_lines(
            playlist_name=playlist.name,
            playlist_url=playlist.url,
            playlist_id=playlist_id,
            playlist_description=playlist.description,
            is_cumulative=True,
        )
        header += [
            line_template.format(*columns),
            divider_line,
        ]

        # Retrieve existing rows, then add new rows
        rows = cls._rows_from_prev_content(today, prev_content, divider_line)
        for track in playlist.tracks:
            # Get the row for the given track
            key = cls._plain_line_from_track(track).lower()
            row = rows.setdefault(key, {column: None for column in columns})
            # Update row values
            row[cls.TITLE] = cls._link(track.name, track.url)
            row[cls.ARTISTS] = cls.ARTIST_SEPARATOR.join(
                [cls._link(artist.name, artist.url) for artist in track.artists]
            )
            row[cls.ALBUM] = cls._link(track.album.name, track.album.url)
            row[cls.LENGTH] = cls._format_duration(track.duration_ms)

            if not row[cls.ADDED]:
                row[cls.ADDED] = today

            row[cls.REMOVED] = ""

        lines = []
        for key, row in sorted(rows.items()):
            lines.append(line_template.format(*[row[column] for column in columns]))

        return "\n".join(header + lines)

    @classmethod
    def _markdown_header_lines(
        cls,
        playlist_name: str,
        playlist_url: str,
        playlist_id: str,
        playlist_description: str,
        is_cumulative: bool,
    ) -> List[str]:
        if is_cumulative:
            pretty = cls._link("pretty", URL.pretty(playlist_name))
            cumulative = "cumulative"
        else:
            pretty = "pretty"
            cumulative = cls._link("cumulative", URL.cumulative(playlist_name))

        return [
            "{} - {} - {} ({})".format(
                pretty,
                cumulative,
                cls._link("plain", URL.plain(playlist_id)),
                cls._link("githistory", URL.plain_history(playlist_id)),
            ),
            "",
            "### {}".format(cls._link(playlist_name, playlist_url)),
            "",
            "> {}".format(playlist_description),
            "",
        ]

    @classmethod
    def _rows_from_prev_content(
        cls, today: str, prev_content: str, divider_line: str
    ) -> Dict[str, Dict[str, Optional[str]]]:
        rows = {}
        if not prev_content:
            return rows

        prev_lines = prev_content.splitlines()
        try:
            index = prev_lines.index(divider_line)
        except ValueError:
            return rows

        for i in range(index + 1, len(prev_lines)):
            prev_line = prev_lines[i]

            try:
                title, artists, album, length, added, removed = (
                    # Slice [2:-2] to trim off "| " and " |"
                    prev_line[2:-2].split(" | ")
                )
            except Exception:
                continue

            key = cls._plain_line_from_names(
                track_name=cls._unlink(title),
                artist_names=[artist for artist in re.findall(cls.LINK_REGEX, artists)],
                album_name=cls._unlink(album),
            ).lower()

            row = {
                cls.TITLE: title,
                cls.ARTISTS: artists,
                cls.ALBUM: album,
                cls.LENGTH: length,
                cls.ADDED: added,
                cls.REMOVED: removed,
            }
            rows[key] = row

            if not row[cls.REMOVED]:
                row[cls.REMOVED] = today

        return rows

    @classmethod
    def _plain_line_from_track(cls, track: Track) -> str:
        return cls._plain_line_from_names(
            track_name=track.name,
            artist_names=[artist.name for artist in track.artists],
            album_name=track.album.name,
        )

    @classmethod
    def _plain_line_from_names(
        cls, track_name: str, artist_names: Sequence[str], album_name: str
    ) -> str:
        return "{} -- {} -- {}".format(
            track_name,
            cls.ARTIST_SEPARATOR.join(artist_names),
            album_name,
        )

    @classmethod
    def _link(cls, text: str, url: str) -> str:
        if not url:
            return text
        return "[{}]({})".format(text, url)

    @classmethod
    def _unlink(cls, link: str) -> str:
        match = re.match(cls.LINK_REGEX, link)
        return match and match.group(1) or ""

    @classmethod
    def _format_duration(cls, duration_ms: int) -> str:
        seconds = int(duration_ms // 1000)
        timedelta = str(datetime.timedelta(seconds=seconds))

        index = 0
        while timedelta[index] in [":", "0"]:
            index += 1

        return timedelta[index:]


async def update_files(now: datetime.datetime) -> None:
    # Check nonempty to fail fast
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    assert client_id and client_secret

    # Initialize the Spotify client
    access_token = await Spotify.get_access_token(client_id, client_secret)
    spotify = Spotify(access_token)
    try:
        await update_files_impl(now, spotify)
    finally:
        await spotify.shutdown()


async def update_files_impl(now: datetime.datetime, spotify: Spotify) -> None:
    aliases_dir = "playlists/aliases"
    plain_dir = "playlists/plain"
    pretty_dir = "playlists/pretty"
    cumulative_dir = "playlists/cumulative"

    # Determine which playlists to scrape from the files in playlists/plain.
    # This makes it easy to add new a playlist: just touch an empty file like
    # playlists/plain/<playlist_id> and this script will handle the rest.
    playlist_ids = os.listdir(plain_dir)

    # Aliases are alternative playlists names. They're useful for avoiding
    # naming collisions when archiving personalized playlists, which have the
    # same name for every user. To add an alias, simply create a file like
    # playlists/aliases/<playlist_id> that contains the alternative name.
    aliases = {}
    for playlist_id in os.listdir(aliases_dir):
        alias_path = "{}/{}".format(aliases_dir, playlist_id)
        if playlist_id not in playlist_ids:
            logger.warning("Removing unused alias: {}".format(playlist_id))
            os.remove(alias_path)
            continue
        contents = open(alias_path).read().splitlines()
        if len(contents) != 1:
            logger.warning("Removing malformed alias: {}".format(playlist_id))
            os.remove(alias_path)
            continue
        aliases[playlist_id] = contents[0]

    readme_lines = []
    for playlist_id in playlist_ids:
        plain_path = "{}/{}".format(plain_dir, playlist_id)

        try:
            playlist = await spotify.get_playlist(playlist_id, aliases)
        except PrivatePlaylistError:
            logger.warning("Removing private playlist: {}".format(playlist_id))
            os.remove(plain_path)
        except InvalidPlaylistError:
            logger.warning("Removing invalid playlist: {}".format(playlist_id))
            os.remove(plain_path)
        else:
            readme_lines.append(
                "- [{}]({})".format(
                    playlist.name,
                    URL.pretty(playlist.name),
                )
            )

            pretty_path = "{}/{}.md".format(pretty_dir, playlist.name)
            cumulative_path = "{}/{}.md".format(cumulative_dir, playlist.name)

            for path in [plain_path, pretty_path, cumulative_path]:
                try:
                    prev_content = "".join(open(path).readlines())
                except Exception:
                    prev_content = ""

                if path == plain_path:
                    content = Formatter.plain(playlist_id, playlist)
                elif path == pretty_path:
                    content = Formatter.pretty(playlist_id, playlist)
                else:
                    content = Formatter.cumulative(
                        now, prev_content, playlist_id, playlist
                    )

                if content == prev_content:
                    logger.info("No changes to file: {}".format(path))
                else:
                    logger.info("Writing updates to file: {}".format(path))
                    with open(path, "w") as f:
                        f.write(content)

    # Sanity check: ensure same number of files in playlists/plain and
    # playlists/pretty - if not, some playlists have the same name and
    # overwrote each other in playlists/pretty OR a playlist ID was changed
    # and the file in playlists/plain was removed and needs to be re-added
    plain_playlists = set()
    for filename in os.listdir(plain_dir):
        with open(os.path.join(plain_dir, filename)) as f:
            plain_playlists.add(f.readline().strip())

    pretty_playlists = set()
    for filename in os.listdir(pretty_dir):
        pretty_playlists.add(filename[:-3])  # strip .md suffix

    missing_from_plain = pretty_playlists - plain_playlists
    missing_from_pretty = plain_playlists - pretty_playlists

    if missing_from_plain:
        raise Exception("Missing plain playlists: {}".format(missing_from_plain))

    if missing_from_pretty:
        raise Exception("Missing pretty playlists: {}".format(missing_from_pretty))

    # Lastly, update README.md
    readme = open("README.md").read().splitlines()
    index = readme.index("## Playlists")
    lines = (
        readme[: index + 1] + [""] + sorted(readme_lines, key=lambda line: line.lower())
    )
    with open("README.md", "w") as f:
        f.write("\n".join(lines) + "\n")


def run(args: Sequence[str]) -> subprocess.CompletedProcess:  # pyre-fixme[24]
    logger.info("- Running: {}".format(args))
    result = subprocess.run(
        args=args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    logger.info("- Exited with: {}".format(result.returncode))
    return result


def push_updates(now: datetime.datetime) -> None:
    diff = run(["git", "status", "-s"])
    has_changes = bool(diff.stdout)

    if not has_changes:
        logger.info("No changes, not pushing")
        return

    logger.info("Configuring git")

    config = ["git", "config", "--global"]
    config_name = run(config + ["user.name", "Mack Ward (Bot Account)"])
    config_email = run(config + ["user.email", "mackorone.bot@gmail.com"])

    if config_name.returncode != 0:
        raise Exception("Failed to configure name")
    if config_email.returncode != 0:
        raise Exception("Failed to configure email")

    logger.info("Staging changes")

    add = run(["git", "add", "-A"])
    if add.returncode != 0:
        raise Exception("Failed to stage changes")

    logger.info("Committing changes")

    run_number = os.getenv("GITHUB_RUN_NUMBER")
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    message = "[skip ci] Run: {} ({})".format(run_number, now_str)
    commit = run(["git", "commit", "-m", message])
    if commit.returncode != 0:
        raise Exception("Failed to commit changes")

    logger.info("Rebasing onto main")
    rebase = run(["git", "rebase", "HEAD", "main"])
    if rebase.returncode != 0:
        raise Exception("Failed to rebase onto main")

    logger.info("Removing origin")
    remote_rm = run(["git", "remote", "rm", "origin"])
    if remote_rm.returncode != 0:
        raise Exception("Failed to remove origin")

    logger.info("Adding new origin")
    # It's ok to print the token, GitHub Actions will hide it
    token = os.getenv("BOT_GITHUB_ACCESS_TOKEN")
    url = (
        "https://mackorone-bot:{}@github.com/mackorone/"
        "spotify-playlist-archive.git".format(token)
    )
    remote_add = run(["git", "remote", "add", "origin", url])
    if remote_add.returncode != 0:
        raise Exception("Failed to add new origin")

    logger.info("Pushing changes")
    push = run(["git", "push", "origin", "main"])
    if push.returncode != 0:
        raise Exception("Failed to push changes")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot Spotify playlists")
    parser.add_argument(
        "--push",
        help="Commit and push updated playlists",
        action="store_true",
    )
    args = parser.parse_args()
    now = datetime.datetime.now()
    await update_files(now)

    if args.push:
        push_updates(now)

    logger.info("Done")


if __name__ == "__main__":
    allow_external_calls()
    asyncio.run(main())
