#!/usr/bin/env python3

import os
import pathlib
from typing import NewType, Optional

from plants.external import external
from plants.environment import Environment as PlantsEnvironment

SimplifiedPath = NewType("SimplifiedPath", pathlib.Path)


class Environment:
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
        repo_dir = PlantsEnvironment.get_repo_root()
        assert repo_dir.name == "spotify-playlist-archive"
        return cls._simplify(repo_dir)

    @classmethod
    def _simplify(cls, path: pathlib.Path) -> SimplifiedPath:
        return SimplifiedPath(pathlib.Path(os.path.relpath(path)))
