#!/usr/bin/env python3

import datetime
import logging
import os
import subprocess
from typing import Sequence

logger: logging.Logger = logging.getLogger(__name__)


class Committer:
    @classmethod
    def push_updates(cls, now: datetime.datetime) -> None:
        diff = cls._run(["git", "status", "-s"])
        has_changes = bool(diff.stdout)

        if not has_changes:
            logger.info("No changes, not pushing")
            return

        logger.info("Configuring git")

        config = ["git", "config", "--global"]
        config_name = cls._run(config + ["user.name", "Mack Ward (Bot Account)"])
        config_email = cls._run(config + ["user.email", "mackorone.bot@gmail.com"])

        if config_name.returncode != 0:
            raise Exception("Failed to configure name")
        if config_email.returncode != 0:
            raise Exception("Failed to configure email")

        logger.info("Staging changes")

        add = cls._run(["git", "add", "-A"])
        if add.returncode != 0:
            raise Exception("Failed to stage changes")

        logger.info("Committing changes")

        run_number = os.getenv("GITHUB_RUN_NUMBER")
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        message = "[skip ci] Run: {} ({})".format(run_number, now_str)
        commit = cls._run(["git", "commit", "-m", message])
        if commit.returncode != 0:
            raise Exception("Failed to commit changes")

        logger.info("Rebasing onto main")
        rebase = cls._run(["git", "rebase", "HEAD", "main"])
        if rebase.returncode != 0:
            raise Exception("Failed to rebase onto main")

        logger.info("Removing origin")
        remote_rm = cls._run(["git", "remote", "rm", "origin"])
        if remote_rm.returncode != 0:
            raise Exception("Failed to remove origin")

        logger.info("Adding new origin")
        # It's ok to print the token, GitHub Actions will hide it
        token = os.getenv("BOT_GITHUB_ACCESS_TOKEN")
        url = (
            "https://mackorone-bot:{}@github.com/mackorone/"
            "spotify-playlist-archive.git".format(token)
        )
        remote_add = cls._run(["git", "remote", "add", "origin", url])
        if remote_add.returncode != 0:
            raise Exception("Failed to add new origin")

        logger.info("Pushing changes")
        push = cls._run(["git", "push", "origin", "main"])
        if push.returncode != 0:
            raise Exception("Failed to push changes")

    @classmethod
    def _run(cls, args: Sequence[str]) -> subprocess.CompletedProcess:  # pyre-fixme[24]
        logger.info("- Running: {}".format(args))
        result = subprocess.run(
            args=args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logger.info("- Exited with: {}".format(result.returncode))
        return result
