#!/usr/bin/env python3

import os
import pathlib
from typing import NewType, Optional

from plants.external.external import external

SimplifiedPath = NewType("SimplifiedPath", pathlib.Path)


class Environment:
    @classmethod
    @external
    def get_env(cls, name: str) -> Optional[str]:
        return os.getenv(name)

    @classmethod
    def get_prod_playlists_dir(cls) -> SimplifiedPath:
        return cls._simplify(cls._get_repo_dir() / "playlists")

    @classmethod
    def get_test_playlists_dir(cls) -> SimplifiedPath:
        name = "_playlists/"
        repo_dir = cls._get_repo_dir()
        with open(repo_dir / ".gitignore") as f:
            assert name in f.read().splitlines()
        return cls._simplify(repo_dir / name)

    @classmethod
    @external
    def _get_repo_dir(cls) -> SimplifiedPath:
        repo_dir = pathlib.Path(__file__).resolve().parent.parent
        assert repo_dir.name == "spotify-playlist-archive"
        return cls._simplify(repo_dir)

    @classmethod
    def _simplify(cls, path: pathlib.Path) -> SimplifiedPath:
        return SimplifiedPath(pathlib.Path(os.path.relpath(path)))
