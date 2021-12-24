#!/usr/bin/env python3

import datetime
from typing import List, NewType

from playlist_id import PlaylistID
from playlist_types import CumulativePlaylist, Playlist, Track
from url import URL

EscapedText = NewType("EscapedText", str)


class Formatter:

    TRACK_NO = "No."
    TITLE = "Title"
    ARTISTS = "Artist(s)"
    ALBUM = "Album"
    LENGTH = "Length"
    ADDED = "Added"
    REMOVED = "Removed"

    ARTIST_SEPARATOR = ", "

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
                    cls._link(cls._escape_markdown(track.name), track.url),
                    cls.ARTIST_SEPARATOR.join(
                        [
                            cls._link(cls._escape_markdown(artist.name), artist.url)
                            for artist in track.artists
                        ]
                    ),
                    cls._link(cls._escape_markdown(track.album.name), track.album.url),
                    cls._format_duration(track.duration_ms),
                )
            )

        lines.append("")
        lines.append(f"Snapshot ID: `{playlist.snapshot_id}`")

        return "\n".join(lines)

    @classmethod
    def cumulative(
        cls,
        playlist_id: PlaylistID,
        playlist: CumulativePlaylist,
    ) -> str:
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
        lines = cls._markdown_header_lines(
            playlist_name=playlist.name,
            playlist_url=playlist.url,
            playlist_id=playlist_id,
            playlist_description=playlist.description,
            is_cumulative=True,
        )
        lines += [
            line_template.format(*columns),
            divider_line,
        ]

        for i, track in enumerate(playlist.tracks):
            date_added = str(track.date_added)
            if track.date_added_asterisk:
                date_added += "\\*"
            lines.append(
                line_template.format(
                    # Title
                    cls._link(cls._escape_markdown(track.name), track.url),
                    # Artists
                    cls.ARTIST_SEPARATOR.join(
                        [
                            cls._link(cls._escape_markdown(artist.name), artist.url)
                            for artist in track.artists
                        ]
                    ),
                    # Album
                    cls._link(cls._escape_markdown(track.album.name), track.album.url),
                    # Length
                    cls._format_duration(track.duration_ms),
                    # Added
                    date_added,
                    # Removed
                    track.date_removed or "",
                )
            )

        lines.append("")
        lines.append(
            f"\\*This playlist was first scraped on {playlist.date_first_scrape}. "
            "Prior content cannot be recovered."
        )

        return "\n".join(lines)

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
            pretty = cls._link(EscapedText("pretty"), URL.pretty(playlist_id))
            cumulative = "cumulative"
        else:
            pretty = "pretty"
            cumulative = cls._link(
                EscapedText("cumulative"), URL.cumulative(playlist_id)
            )

        return [
            "{} - {} - {} - {}".format(
                pretty,
                cumulative,
                cls._link(EscapedText("plain"), URL.plain(playlist_id)),
                cls._link(EscapedText("githistory"), URL.plain_history(playlist_id)),
            ),
            "",
            "### {}".format(
                cls._link(cls._escape_markdown(playlist_name), playlist_url)
            ),
            "",
            # Some descriptions end with newlines, strip to clean them up
            "> {}".format(cls._escape_markdown(playlist_description.strip())),
            "",
        ]

    @classmethod
    def _plain_line_from_track(cls, track: Track) -> str:
        return "{} -- {} -- {}".format(
            track.name,
            cls.ARTIST_SEPARATOR.join([artist.name for artist in track.artists]),
            track.album.name,
        )

    @classmethod
    def _link(cls, text: EscapedText, url: str) -> str:
        if not url:
            return text
        return "[{}]({})".format(text, url)

    @classmethod
    def _format_duration(cls, duration_ms: int) -> str:
        seconds = int(duration_ms // 1000)
        timedelta = str(datetime.timedelta(seconds=seconds))

        index = 0
        # Strip leading zeros but always include the minutes digit
        while index < len(timedelta) - 4 and timedelta[index] in "0:":
            index += 1

        return timedelta[index:]

    @classmethod
    def _escape_markdown(cls, text: str) -> EscapedText:
        text = text.replace("\n", "<br/>")
        for pattern in [
            "\\",  # Existing backslashes
            ". ",  # Numbered lists
            "#",  # Headers
            "(",  # Links
            ")",  # Links
            "[",  # Links
            "]",  # Links
            "-",  # Bulleted lists
            "*",  # Bulleted lists and emphasis
            "_",  # Emphasis
            "`",  # Code
            "|",  # Tables
            "~",  # Strikethrough
        ]:
            text = text.replace(pattern, f"\\{pattern}")

        return EscapedText(text)
