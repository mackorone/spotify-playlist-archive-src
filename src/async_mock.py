#!/usr/bin/env python3

import sys
from types import TracebackType
from typing import Any, Optional, Type
from unittest.mock import Mock


class AsyncMock(Mock):
    def __call__(self, *args: Any, **kwargs: Any):  # pyre-fixme[3]
        async def coro(parent: Mock) -> None:
            return parent.__call__(*args, **kwargs)

        sys.set_coroutine_origin_tracking_depth(3)  # pyre-fixme[16]
        return coro(super())

    def __await__(self):  # pyre-fixme[3]
        return self().__await__()

    async def __aenter__(self) -> "AsyncMock":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        pass
