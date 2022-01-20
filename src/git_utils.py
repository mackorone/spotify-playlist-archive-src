#!/usr/bin/env python3

from subprocess_utils import SubprocessUtils


class GitUtils:
    @classmethod
    def any_uncommitted_changes(cls) -> bool:
        result = SubprocessUtils.run(["git", "status", "-s"])
        return bool(result.stdout)
