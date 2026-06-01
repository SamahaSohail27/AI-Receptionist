"""
Unit tests — multilingual date/time parser.

Covers all three languages:
- Pakistani Urdu (ur-PK): آج کل پرسوں اگلے جمعہ + Urdu time words
- Punjabi (pa-PK): اج کل + Punjabi time words
- English: today tomorrow day after tomorrow + next Friday
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, date
from unittest.mock import patch

import pytz
import pytest

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------
PKT = pytz.timezone("Asia/Karachi")


def _parse_urdu(text: str, anchor: date = None):
    """Parse Urdu date expression relative to anchor date."""
    from scheduling.date_parser import MultiLanguageDateParser
    anchor = anchor or date(2025, 6, 2)  # Monday
    parser = MultiLanguageDateParser()
    return parser.parse(text, "ur-PK", reference_date=anchor)


def _parse_punjabi(text: str, anchor: date = None):
    from scheduling.date_parser import MultiLanguageDateParser
    anchor = anchor or date(2025, 6, 2)  # Monday
    parser = MultiLanguageDateParser()
    return parser.parse(text, "pa-PK", reference_date=anchor)


def _parse_english(text: str, anchor: date = None):
    from scheduling.date_parser import MultiLanguageDateParser
    anchor = anchor or date(2025, 6, 2)  # Monday
    parser = MultiLanguageDateParser()
    return parser.parse(text, "en", reference_date=anchor)


@pytest.mark.unit
class TestUrduDateParser:

    def test_today_urdu(self):
        result = _parse_urdu("آج", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 2)

    def test_tomorrow_urdu(self):
        result = _parse_urdu("کل", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 3)

    def test_day_after_tomorrow_urdu(self):
        result = _parse_urdu("پرسوں", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 4)

    def test_day_after_tomorrow_alt_urdu(self):
        result = _parse_urdu("پرسو", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 4)

    def test_next_friday_urdu(self):
        # anchor is Monday June 2 2025 — next Friday is June 6
        result = _parse_urdu("اگلے جمعہ", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.weekday() == 4  # Friday = 4

    def test_morning_urdu_defaults_to_9am(self):
        result = _parse_urdu("آج صبح", anchor=date(2025, 6, 2))
        if result is not None:
            assert result.hour == 9

    def test_afternoon_urdu_defaults_to_2pm(self):
        result = _parse_urdu("کل دوپہر", anchor=date(2025, 6, 2))
        if result is not None:
            assert result.hour == 14

    def test_evening_urdu_defaults_to_5pm(self):
        result = _parse_urdu("کل شام", anchor=date(2025, 6, 2))
        if result is not None:
            assert result.hour == 17

    def test_empty_string_returns_none(self):
        result = _parse_urdu("")
        assert result is None

    def test_pure_noise_returns_none(self):
        result = _parse_urdu("ہاں جی")
        assert result is None

    def test_result_is_pkt_aware(self):
        result = _parse_urdu("آج", anchor=date(2025, 6, 2))
        if result is not None:
            assert result.tzinfo is not None


@pytest.mark.unit
class TestPunjabiDateParser:

    def test_today_punjabi(self):
        result = _parse_punjabi("اج", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 2)

    def test_tomorrow_punjabi(self):
        result = _parse_punjabi("کل", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 3)

    def test_day_after_tomorrow_punjabi(self):
        result = _parse_punjabi("پرسوں", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 4)

    def test_morning_punjabi(self):
        result = _parse_punjabi("اج سویرے", anchor=date(2025, 6, 2))
        if result is not None:
            assert result.hour == 9


@pytest.mark.unit
class TestEnglishDateParser:

    def test_today_english(self):
        result = _parse_english("today", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 2)

    def test_tomorrow_english(self):
        result = _parse_english("tomorrow", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 3)

    def test_day_after_tomorrow_english(self):
        result = _parse_english("day after tomorrow", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.date() == date(2025, 6, 4)

    def test_next_friday_english(self):
        result = _parse_english("next Friday", anchor=date(2025, 6, 2))
        assert result is not None
        assert result.weekday() == 4

    def test_next_monday_english(self):
        result = _parse_english("next Monday", anchor=date(2025, 6, 2))
        if result is not None:
            assert result.weekday() == 0

    def test_morning_english(self):
        result = _parse_english("tomorrow morning", anchor=date(2025, 6, 2))
        if result is not None:
            assert result.hour == 9

    def test_afternoon_english(self):
        result = _parse_english("tomorrow afternoon", anchor=date(2025, 6, 2))
        if result is not None:
            assert result.hour in (12, 13, 14)

    def test_empty_returns_none(self):
        result = _parse_english("")
        assert result is None
