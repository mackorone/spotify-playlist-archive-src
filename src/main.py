#!/usr/bin/env python3

import argparse
import asyncio
import datetime
import logging
import os
import pathlib

from committer import Committer
from external import allow_external_calls
from file_updater import FileUpdater

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger: logging.Logger = logging.getLogger(__name__)


async def main() -> None:
    now = datetime.datetime.now()
    parser = argparse.ArgumentParser(description="Snapshot Spotify playlists")
    parser.add_argument(
        "--auto-register",
        help="Automatically register select playlists",
        action="store_true",
    )
    parser.add_argument(
        "--commit-and-push",
        help="Commit and push updated playlists upstream",
        action="store_true",
    )
    args = parser.parse_args()
    auto_register = bool(args.auto_register)
    commit_and_push = bool(args.commit_and_push)

    # Get the absolute path to the playlists directory
    repo_root_dir = pathlib.Path(__file__).resolve().parent.parent
    assert repo_root_dir.name == "spotify-playlist-archive"
    repo_root_dir = pathlib.Path(os.path.relpath(repo_root_dir))
    prod_name = "playlists"
    test_name = "_playlists/"
    with open(repo_root_dir / ".gitignore") as f:
        assert test_name in f.read().splitlines()
    if commit_and_push:
        playlists_dir = repo_root_dir / prod_name
    else:
        playlists_dir = repo_root_dir / test_name

    await FileUpdater.update_files(
        now=now,
        playlists_dir=playlists_dir,
        auto_register=auto_register,
        update_readme=commit_and_push,
    )
    if commit_and_push:
        Committer.push_updates(now=now)

    logger.info("Done")


if __name__ == "__main__":
    allow_external_calls()
    asyncio.run(main())
