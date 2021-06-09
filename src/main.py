#!/usr/bin/env python3

import argparse
import asyncio
import datetime
import logging

from committer import Committer
from external import allow_external_calls
from file_updater import FileUpdater

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger: logging.Logger = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot Spotify playlists")
    parser.add_argument(
        "--prod",
        help="Commit and push updated playlists",
        action="store_true",
    )
    args = parser.parse_args()
    now = datetime.datetime.now()

    prod = bool(args.prod)
    await FileUpdater.update_files(now, prod)
    if prod:
        Committer.push_updates(now)

    logger.info("Done")


if __name__ == "__main__":
    allow_external_calls()
    asyncio.run(main())
