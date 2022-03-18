#!/usr/bin/env python3

import logging
import subprocess
from typing import List

from plants.external.external import external

logger: logging.Logger = logging.getLogger(__name__)


class SubprocessUtils:
    @classmethod
    @external
    def run(cls, args: List[str]) -> subprocess.CompletedProcess:  # pyre-fixme[24]
        logger.info(f"- Running: {args}")
        result = subprocess.run(
            args=args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        logger.info(f"- Exited with: {result.returncode}")
        return result
