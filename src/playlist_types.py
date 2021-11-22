#!/usr/bin/env python3


import dataclasses
from typing import Sequence


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


@dataclasses.dataclass(frozen=True)
class Playlist:
    url: str
    name: str
    description: str
    tracks: Sequence[Track]
