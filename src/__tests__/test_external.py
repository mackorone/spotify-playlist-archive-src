#!/usr/bin/env python3

from unittest import TestCase

from external import InvalidExternalCallError, external


class TestExternal(TestCase):
    @external
    def foo(self) -> None:
        pass

    @classmethod
    @external
    def bar(cls) -> None:
        pass

    def test_basic(self) -> None:
        with self.assertRaises(InvalidExternalCallError):
            self.foo()

    def test_classmethod(self) -> None:
        with self.assertRaises(InvalidExternalCallError):
            self.bar()

    def test_base_exception(self) -> None:
        with self.assertRaises(InvalidExternalCallError):
            try:
                self.bar()
            except Exception:
                pass
