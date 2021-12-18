#!/usr/bin/env python3


import dataclasses
import datetime
import json
from typing import Optional, Sequence


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


@dataclasses.dataclass(frozen=True)
class Playlist:
    url: str
    name: str
    description: str
    tracks: Sequence[Track]

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
