#!/usr/bin/env python3

import string
from unittest import TestCase

from alias import Alias, InvalidAliasError


class TestPlaylistID(TestCase):
    def test_empty(self) -> None:
        with self.assertRaises(InvalidAliasError):
            Alias("")

    def test_invalid_whitespace(self) -> None:
        for c in string.whitespace:
            if c in " \t":
                continue
            with self.assertRaises(InvalidAliasError):
                Alias(f"foo{c}bar")

    def test_enclosing_whitespace(self) -> None:
        for c in string.whitespace:
            for candidate in [f"{c}foo", f"foo{c}", f"{c}foo{c}"]:
                with self.assertRaises(InvalidAliasError):
                    Alias(candidate)
