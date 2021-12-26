#!/usr/bin/env python3

import os
import pathlib
from typing import Optional

from external import external


class Environment:
    @classmethod
    @external
    def get_env(cls, name: str) -> Optional[str]:
        return os.getenv(name)

    @classmethod
    def get_prod_playlists_dir(cls) -> pathlib.Path:
        return cls._get_repo_dir() / "playlists"

    @classmethod
    def get_test_playlists_dir(cls) -> pathlib.Path:
        name = "_playlists/"
        repo_dir = cls._get_repo_dir()
        with open(repo_dir / ".gitignore") as f:
            assert name in f.read().splitlines()
        return repo_dir / name

    @classmethod
    @external
    def _get_repo_dir(cls) -> pathlib.Path:
        repo_dir = pathlib.Path(__file__).resolve().parent.parent
        assert repo_dir.name == "spotify-playlist-archive"
        return pathlib.Path(os.path.relpath(repo_dir))
