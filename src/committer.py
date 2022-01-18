#!/usr/bin/env python3

import datetime
import logging

from environment import Environment
from git_utils import GitUtils
from subprocess_utils import SubprocessUtils

logger: logging.Logger = logging.getLogger(__name__)


class Committer:
    @classmethod
    def push_updates(cls, now: datetime.datetime) -> None:
        has_changes = GitUtils.any_uncommitted_changes()
        if not has_changes:
            logger.info("No changes, not pushing")
            return

        logger.info("Configuring git")

        config = ["git", "config", "--global"]
        config_name = SubprocessUtils.run(
            config + ["user.name", "Mack Ward (Bot Account)"]
        )
        config_email = SubprocessUtils.run(
            config + ["user.email", "mackorone.bot@gmail.com"]
        )

        if config_name.returncode != 0:
            raise Exception("Failed to configure name")
        if config_email.returncode != 0:
            raise Exception("Failed to configure email")

        logger.info("Staging changes")

        add = SubprocessUtils.run(["git", "add", "-A"])
        if add.returncode != 0:
            raise Exception("Failed to stage changes")

        logger.info("Committing changes")

        run_number = Environment.get_env("GITHUB_RUN_NUMBER")
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        message = f"[skip ci] Run: {run_number} ({now_str})"
        commit = SubprocessUtils.run(["git", "commit", "-m", message])
        if commit.returncode != 0:
            raise Exception("Failed to commit changes")

        logger.info("Rebasing onto main")
        rebase = SubprocessUtils.run(["git", "rebase", "HEAD", "main"])
        if rebase.returncode != 0:
            raise Exception("Failed to rebase onto main")

        logger.info("Removing origin")
        remote_rm = SubprocessUtils.run(["git", "remote", "rm", "origin"])
        if remote_rm.returncode != 0:
            raise Exception("Failed to remove origin")

        logger.info("Adding new origin")
        # It's ok to print the token, GitHub Actions will hide it
        token = Environment.get_env("BOT_GITHUB_ACCESS_TOKEN")
        url = (
            f"https://mackorone-bot:{token}@github.com/mackorone/"
            "spotify-playlist-archive.git"
        )
        remote_add = SubprocessUtils.run(["git", "remote", "add", "origin", url])
        if remote_add.returncode != 0:
            raise Exception("Failed to add new origin")

        logger.info("Pushing changes")
        push = SubprocessUtils.run(["git", "push", "origin", "main"])
        if push.returncode != 0:
            raise Exception("Failed to push changes")
