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


class TestFormatDurationEnglish(TestCase):
    def test_success(self) -> None:
        self.assertEqual(Formatter._format_duration_english(0), "0 sec")
        self.assertEqual(Formatter._format_duration_english(999), "0 sec")
        self.assertEqual(Formatter._format_duration_english(1000), "1 sec")
        self.assertEqual(Formatter._format_duration_english(59999), "59 sec")
        self.assertEqual(Formatter._format_duration_english(60000), "1 min 0 sec")
        self.assertEqual(Formatter._format_duration_english(3599999), "59 min 59 sec")
        self.assertEqual(Formatter._format_duration_english(3600000), "1 hr 0 min")
        self.assertEqual(Formatter._format_duration_english(86399999), "23 hr 59 min")
        self.assertEqual(
            Formatter._format_duration_english(86400000), "1 day 0 hr 0 min"
        )
        self.assertEqual(
            Formatter._format_duration_english(1001001001), "11 day 14 hr 3 min"
        )
        self.assertEqual(
            Formatter._format_duration_english(1001001001001), "11,585 day 15 hr 50 min"
        )
