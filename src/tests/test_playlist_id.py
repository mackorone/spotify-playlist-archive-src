#!/usr/bin/env python3

import string
from unittest import TestCase

from playlist_id import InvalidPlaylistIDError, PlaylistID


class TestPlaylistID(TestCase):
    def test_invalid(self) -> None:
        for c in string.punctuation + string.whitespace:
            with self.assertRaises(InvalidPlaylistIDError):
                PlaylistID(c)

    def test_valid(self) -> None:
        PlaylistID(string.ascii_letters + string.digits)

    def test_equal(self) -> None:
        self.assertEqual(PlaylistID("foo"), PlaylistID("foo"))

    def test_str(self) -> None:
        self.assertEqual(str(PlaylistID("foo")), "foo")
