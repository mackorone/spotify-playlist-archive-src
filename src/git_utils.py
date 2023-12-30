#!/usr/bin/env python3

import logging
import subprocess
from typing import List, Tuple

from plants.subprocess_utils import SubprocessUtils

logger: logging.Logger = logging.getLogger(__name__)


class GitUtils:
    @classmethod
    def get_last_commit_content(cls) -> List[str]:
        """Get files affected by the most recent commit"""
        result = cls._run(("git", "log", "--name-only", "--pretty=format:", "-1"))
        return result.stdout.splitlines()

    @classmethod
    def _run(cls, args: Tuple[str, ...]) -> "subprocess.CompletedProcess[str]":
        logger.info(f"- Running: {args}")
        result = SubprocessUtils.run(args=args)
        logger.info(f"- Exited with: {result.returncode}")
        return result
