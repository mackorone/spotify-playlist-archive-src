#!/usr/bin/env python3

from __future__ import annotations

import dataclasses
import datetime
import json
from typing import List, Optional, Sequence


@dataclasses.dataclass(frozen=True)
class Owner:
    url: str
    name: str


@dataclasses.dataclass(frozen=True)
class Album:
    url: str
    name: str


@dataclasses.dataclass(frozen=True)
class Artist:
    url: str
    name: str


@dataclasses.dataclass(frozen=True)
class Track:
    url: str
    name: str
    album: Album
    artists: Sequence[Artist]
    duration_ms: int
    added_at: Optional[datetime.datetime]

    def get_id(self) -> str:
        track_id = self.url.split("/")[-1]
        assert track_id.isalnum(), repr(track_id)
        return track_id


@dataclasses.dataclass(frozen=True)
class Playlist:
    url: str
    name: str
    description: str
    tracks: Sequence[Track]
    snapshot_id: str
    num_followers: int
    owner: Owner

    def to_json(self) -> str:
        return json.dumps(
            dataclasses.asdict(self),
            indent=2,
            sort_keys=True,
            default=self.serialize_datetime,
        )

    @classmethod
    def serialize_datetime(cls, obj: object) -> str:
        assert isinstance(obj, datetime.datetime)
        return str(obj)


@dataclasses.dataclass(frozen=True)
class CumulativeTrack:
    url: str
    name: str
    album: Album
    artists: Sequence[Artist]
    duration_ms: int
    date_added: datetime.date
    date_added_asterisk: bool
    date_removed: Optional[datetime.date]

    def get_id(self) -> str:
        track_id = self.url.split("/")[-1]
        assert track_id.isalnum(), repr(track_id)
        return track_id


@dataclasses.dataclass(frozen=True)
class CumulativePlaylist:
    url: str
    name: str
    description: str
    tracks: Sequence[CumulativeTrack]
    date_first_scraped: datetime.date

    def update(self, today: datetime.date, playlist: Playlist) -> CumulativePlaylist:
        updated_tracks: List[CumulativeTrack] = []
        current_tracks = {track.get_id(): track for track in playlist.tracks}
        previous_tracks = {track.get_id(): track for track in self.tracks}
        for track_id in set(current_tracks) | set(previous_tracks):
            old_data = previous_tracks.get(track_id)
            new_data = current_tracks.get(track_id)
            assert old_data or new_data

            if old_data:
                url = old_data.url
                name = old_data.name
                album = old_data.album
                artists = old_data.artists
                duration_ms = old_data.duration_ms
                date_added = old_data.date_added
                date_added_asterisk = old_data.date_added_asterisk
                date_removed = old_data.date_removed or today

            if new_data:
                url = new_data.url
                name = new_data.name
                album = new_data.album
                artists = new_data.artists
                duration_ms = new_data.duration_ms
                date_added = new_data.added_at.date() if new_data.added_at else today
                date_added_asterisk = False
                date_removed = None
                # If the old date_added is earlier than what Spotify returned, use it
                if old_data and old_data.date_added <= date_added:
                    date_added = old_data.date_added
                    date_added_asterisk = old_data.date_added_asterisk

            updated_tracks.append(
                CumulativeTrack(
                    url=url,
                    name=name,
                    album=album,
                    artists=artists,
                    duration_ms=duration_ms,
                    date_added=date_added,
                    date_added_asterisk=date_added_asterisk,
                    date_removed=date_removed,
                )
            )

        return CumulativePlaylist(
            url=playlist.url,
            name=playlist.name,
            description=playlist.description,
            tracks=sorted(
                updated_tracks,
                key=lambda track: (
                    track.name.lower(),
                    tuple(artist.name.lower() for artist in track.artists),
                    track.duration_ms,
                    track.get_id(),
                ),
            ),
            date_first_scraped=self.date_first_scraped,
        )

    @classmethod
    def from_json(cls, content: str) -> CumulativePlaylist:
        playlist = json.loads(content)
        assert isinstance(playlist, dict)

        playlist_url = playlist["url"]
        assert isinstance(playlist_url, str)

        playlist_name = playlist["name"]
        assert isinstance(playlist_name, str)

        description = playlist["description"]
        assert isinstance(description, str)

        date_first_scraped_string = playlist["date_first_scraped"]
        assert isinstance(date_first_scraped_string, str)
        date_first_scraped = datetime.datetime.strptime(
            date_first_scraped_string, "%Y-%m-%d"
        ).date()
        assert isinstance(date_first_scraped, datetime.date)

        cumulative_tracks: List[CumulativeTrack] = []
        assert isinstance(playlist["tracks"], list)
        for track in playlist["tracks"]:
            assert isinstance(track, dict)

            track_url = track["url"]
            assert isinstance(track_url, str)

            track_name = track["name"]
            assert isinstance(track_name, str)

            assert isinstance(track["album"], dict)
            album_url = track["album"]["url"]
            assert isinstance(album_url, str)
            album_name = track["album"]["name"]
            assert isinstance(album_name, str)

            artists = []
            assert isinstance(track["artists"], list)
            for artist in track["artists"]:
                assert isinstance(artist, dict)
                artist_url = artist["url"]
                assert isinstance(artist_url, str)
                artist_name = artist["name"]
                assert isinstance(artist_name, str)
                artists.append(Artist(url=artist_url, name=artist_name))

            duration_ms = track["duration_ms"]
            assert isinstance(duration_ms, int)

            date_added_string = track["date_added"]
            assert isinstance(date_added_string, str)
            date_added = datetime.datetime.strptime(
                date_added_string, "%Y-%m-%d"
            ).date()
            assert isinstance(date_added, datetime.date)

            date_added_asterisk = track["date_added_asterisk"]
            assert isinstance(date_added_asterisk, bool)

            date_removed = None
            date_removed_string = track["date_removed"]
            if date_removed_string is not None:
                assert isinstance(date_removed_string, str)
                date_removed = datetime.datetime.strptime(
                    date_removed_string, "%Y-%m-%d"
                ).date()
                assert isinstance(date_removed, datetime.date)

            cumulative_tracks.append(
                CumulativeTrack(
                    url=track_url,
                    name=track_name,
                    album=Album(
                        url=album_url,
                        name=album_name,
                    ),
                    artists=artists,
                    duration_ms=duration_ms,
                    date_added=date_added,
                    date_added_asterisk=date_added_asterisk,
                    date_removed=date_removed,
                )
            )

        return CumulativePlaylist(
            url=playlist_url,
            name=playlist_name,
            description=description,
            tracks=cumulative_tracks,
            date_first_scraped=date_first_scraped,
        )

    def to_json(self) -> str:
        return json.dumps(
            dataclasses.asdict(self),
            indent=2,
            sort_keys=True,
            default=self.serialize_date,
        )

    @classmethod
    def serialize_date(cls, obj: object) -> str:
        assert isinstance(obj, datetime.date)
        return str(obj)
