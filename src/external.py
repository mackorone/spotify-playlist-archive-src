#!/usr/bin/env python3

import functools
from typing import Callable, TypeVar

from pyre_extensions import ParameterSpecification

TParams = ParameterSpecification("TParams")
TReturn = TypeVar("TReturn")


ALLOW_EXTERNAL_CALLS: bool = False


class InvalidExternalCallError(BaseException):
    # Derive from BaseException to avoid being caught by `except Exception`
    pass


def allow_external_calls() -> None:
    # This method should never be called within unit tests
    global ALLOW_EXTERNAL_CALLS
    ALLOW_EXTERNAL_CALLS = True


def external(func: Callable[TParams, TReturn]) -> Callable[TParams, TReturn]:
    """The @external decorator prevents unit tests from executing methods with
    external dependencies. If a decorated method is called from within a test,
    an exception is raised, which causes the test to fail. For the test to
    succeed, the decorated method must be mocked."""

    @functools.wraps(func)
    def wrapper(*args: TParams.args, **kwargs: TParams.kwargs) -> TReturn:
        if not ALLOW_EXTERNAL_CALLS:
            raise InvalidExternalCallError(func.__name__)
        return func(*args, **kwargs)

    return wrapper
