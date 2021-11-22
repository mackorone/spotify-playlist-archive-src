#!/usr/bin/env python3

import datetime
import re
from typing import Dict, List, Optional, Sequence

from playlist_id import PlaylistID
from playlist_types import Playlist, Track
from url import URL


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
    def plain(cls, playlist_id: PlaylistID, playlist: Playlist) -> str:
        lines = [cls._plain_line_from_track(track) for track in playlist.tracks]
        # Sort alphabetically to minimize changes when tracks are reordered
        sorted_lines = sorted(lines, key=lambda line: line.lower())
        header = [playlist.name, playlist.description, ""]
        return "\n".join(header + sorted_lines)

    @classmethod
    def pretty(cls, playlist_id: PlaylistID, playlist: Playlist) -> str:
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
        playlist_id: PlaylistID,
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
        playlist_id: PlaylistID,
        playlist_description: str,
        is_cumulative: bool,
    ) -> List[str]:
        if is_cumulative:
            pretty = cls._link("pretty", URL.pretty(playlist_id))
            cumulative = "cumulative"
        else:
            pretty = "pretty"
            cumulative = cls._link("cumulative", URL.cumulative(playlist_id))

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
