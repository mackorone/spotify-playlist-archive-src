#!/usr/bin/env python3

from typing import Any, Callable, Sequence, TypeVar, Union

T = TypeVar("T")


class UnittestUtils:
    @classmethod
    def side_effect(cls, values: Sequence[Union[T, Exception]]) -> Callable[..., T]:
        """Like side_effect but supports returning values *and* raising exceptions"""
        call_count: int = 0

        def wrapper(*args: Any, **kwargs: Any) -> T:
            nonlocal call_count
            assert call_count < len(values)
            value = values[call_count]
            call_count += 1
            if isinstance(value, Exception):
                raise value
            return value

        return wrapper
