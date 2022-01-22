#!/usr/bin/env python3

from typing import List

from subprocess_utils import SubprocessUtils


class GitUtils:
    @classmethod
    def any_uncommitted_changes(cls) -> bool:
        result = SubprocessUtils.run(["git", "status", "-s"])
        return bool(result.stdout)

    @classmethod
    def get_last_commit_content(cls) -> List[str]:
        """Get files affected by the most recent commit"""
        result = SubprocessUtils.run(
            ["git", "log", "--name-only", "--pretty=format:", "-1"]
        )
        return result.stdout.splitlines()
