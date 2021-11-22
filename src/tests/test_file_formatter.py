#!/usr/bin/env python3

from unittest import TestCase

from file_formatter import Formatter


class TestFormatDuration(TestCase):
    def test_success(self) -> None:
        self.assertEqual(Formatter._format_duration(0), "0:00")
        self.assertEqual(Formatter._format_duration(999), "0:00")
        self.assertEqual(Formatter._format_duration(1000), "0:01")
        self.assertEqual(Formatter._format_duration(59999), "0:59")
        self.assertEqual(Formatter._format_duration(60000), "1:00")
        self.assertEqual(Formatter._format_duration(3599999), "59:59")
        self.assertEqual(Formatter._format_duration(3600000), "1:00:00")
