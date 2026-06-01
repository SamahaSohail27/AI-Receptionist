"""
Number-to-spoken-text converters for TTS output.

Three converters are provided:
  - UrduNumberConverter    — Pakistani Urdu (ur-PK)
  - EnglishNumberConverter — English (en)
  - PunjabiNumberConverter — delegates to Urdu (Azure ur-PK voice used for pa-PK TTS)

Public helpers:
  NumberConverterRegistry.get_converter(name)
  replace_numbers_in_text(text, converter_name)
"""
from __future__ import annotations

import re
from typing import Union


# ---------------------------------------------------------------------------
# Urdu Number Converter
# ---------------------------------------------------------------------------

# 0-20 in Urdu
URDU_ONES = [
    "صفر",   # 0
    "ایک",   # 1
    "دو",    # 2
    "تین",   # 3
    "چار",   # 4
    "پانچ",  # 5
    "چھ",    # 6
    "سات",   # 7
    "آٹھ",   # 8
    "نو",    # 9
    "دس",    # 10
    "گیارہ", # 11
    "بارہ",  # 12
    "تیرہ",  # 13
    "چودہ",  # 14
    "پندرہ", # 15
    "سولہ",  # 16
    "سترہ",  # 17
    "اٹھارہ",# 18
    "انیس",  # 19
    "بیس",   # 20
]

URDU_TENS = [
    "",       # 0
    "",       # 10 — handled via URDU_ONES
    "بیس",    # 20
    "تیس",    # 30
    "چالیس",  # 40
    "پچاس",   # 50
    "ساٹھ",   # 60
    "ستر",    # 70
    "اسی",    # 80
    "نوے",    # 90
]


class UrduNumberConverter:
    """Convert integers 0-9999 to Pakistani Urdu spoken words."""

    def convert(self, n: int) -> str:
        if n < 0 or n > 9999:
            return str(n)

        if n == 0:
            return URDU_ONES[0]

        # Special case: 1500 → "ڈیڑھ ہزار"
        if n == 1500:
            return "ڈیڑھ ہزار"

        parts: list[str] = []

        # Thousands
        if n >= 1000:
            thousands = n // 1000
            remainder = n % 1000
            if thousands == 1:
                parts.append("ہزار")
            else:
                parts.append(self._below_thousand(thousands) + " ہزار")
            if remainder > 0:
                parts.append(self._below_thousand(remainder))
            return " ".join(parts)

        return self._below_thousand(n)

    def _below_thousand(self, n: int) -> str:
        """Convert a number in range 1-999 to Urdu words."""
        if n == 0:
            return ""

        parts: list[str] = []

        # Hundreds
        if n >= 100:
            hundreds = n // 100
            remainder = n % 100
            if hundreds == 1:
                parts.append("سو")
            elif hundreds == 2:
                parts.append("دو سو")
            else:
                parts.append(URDU_ONES[hundreds] + " سو")
            if remainder > 0:
                parts.append(self._below_hundred(remainder))
            return " ".join(parts)

        return self._below_hundred(n)

    def _below_hundred(self, n: int) -> str:
        """Convert a number in range 1-99 to Urdu words."""
        if n == 0:
            return ""

        if n <= 20:
            return URDU_ONES[n]

        tens = n // 10
        ones = n % 10

        if ones == 0:
            return URDU_TENS[tens]

        # Urdu: ones come first for compound numbers above 20
        # e.g., 21 → "اکیس" but 22+ uses pattern "ones اور tens" — natural spoken Urdu
        # Standard spoken form: ones + tens connector
        return URDU_ONES[ones] + " " + URDU_TENS[tens]


# ---------------------------------------------------------------------------
# English Number Converter
# ---------------------------------------------------------------------------

_EN_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]

_EN_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety",
]


class EnglishNumberConverter:
    """Convert integers 0-9999 to English spoken words."""

    def convert(self, n: int) -> str:
        if n < 0 or n > 9999:
            return str(n)

        if n == 0:
            return "zero"

        return self._convert_positive(n)

    def _convert_positive(self, n: int) -> str:
        parts: list[str] = []

        if n >= 1000:
            thousands = n // 1000
            remainder = n % 1000
            parts.append(self._below_thousand(thousands) + " thousand")
            if remainder > 0:
                parts.append(self._below_thousand(remainder))
            return " ".join(parts)

        return self._below_thousand(n)

    def _below_thousand(self, n: int) -> str:
        if n == 0:
            return ""

        parts: list[str] = []

        if n >= 100:
            hundreds = n // 100
            remainder = n % 100
            parts.append(_EN_ONES[hundreds] + " hundred")
            if remainder > 0:
                parts.append("and " + self._below_hundred(remainder))
            return " ".join(parts)

        return self._below_hundred(n)

    def _below_hundred(self, n: int) -> str:
        if n == 0:
            return ""

        if n < 20:
            return _EN_ONES[n]

        tens = n // 10
        ones = n % 10

        if ones == 0:
            return _EN_TENS[tens]

        return _EN_TENS[tens] + "-" + _EN_ONES[ones]


# ---------------------------------------------------------------------------
# Punjabi Number Converter (delegates to Urdu)
# ---------------------------------------------------------------------------

class PunjabiNumberConverter:
    """
    Punjabi number converter.

    Pakistani Punjabi TTS uses Azure ur-PK-UzmaNeural — Urdu number words
    are fully intelligible to Punjabi speakers, so we delegate to the Urdu
    converter rather than maintaining a separate word list.
    """

    _delegate = UrduNumberConverter()

    def convert(self, n: int) -> str:
        return self._delegate.convert(n)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class NumberConverterRegistry:
    _registry: dict[str, UrduNumberConverter | EnglishNumberConverter | PunjabiNumberConverter] = {
        "urdu": UrduNumberConverter(),
        "english": EnglishNumberConverter(),
        "punjabi": PunjabiNumberConverter(),
    }

    @classmethod
    def get_converter(
        cls, name: str
    ) -> Union[UrduNumberConverter, EnglishNumberConverter, PunjabiNumberConverter]:
        """
        Return the converter for the given name.
        Falls back to EnglishNumberConverter for unknown names.
        """
        return cls._registry.get(name.lower(), cls._registry["english"])


def get_converter(
    name: str,
) -> Union[UrduNumberConverter, EnglishNumberConverter, PunjabiNumberConverter]:
    """Module-level convenience wrapper for NumberConverterRegistry.get_converter."""
    return NumberConverterRegistry.get_converter(name)


def replace_numbers_in_text(text: str, converter_name: str) -> str:
    """
    Find all standalone integers in text and replace each with its spoken form
    using the named converter.

    Example:
        replace_numbers_in_text("Room 302 at 9 am", "english")
        → "Room three hundred and two at nine am"
    """

    def _replace(match: re.Match) -> str:
        n = int(match.group())
        return get_converter(converter_name).convert(n)

    return re.sub(r"\b\d+\b", _replace, text)
