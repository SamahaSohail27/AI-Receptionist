from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from scheduling.pkt_calendar import PKT, PKTCalendar

# ---------------------------------------------------------------------------
# Urdu (ur-PK) mappings
# ---------------------------------------------------------------------------

URDU_RELATIVE: dict[str, int | None] = {
    "آج": 0,
    "کل": 1,
    "پرسوں": 2,
    "پرسو": 2,
    "اگلے": None,   # must be followed by a day name
}

URDU_DAYS: dict[str, int] = {
    "پیر": 0,       # Monday
    "منگل": 1,      # Tuesday
    "بدھ": 2,       # Wednesday
    "جمعرات": 3,    # Thursday
    "جمعہ": 4,      # Friday
    "ہفتہ": 5,      # Saturday
    "اتوار": 6,     # Sunday
}

# (label, default_hour, default_minute)
URDU_TIME_PARTS: dict[str, tuple[str, int, int]] = {
    "صبح":    ("morning",   9,  0),
    "دوپہر": ("afternoon", 14,  0),
    "شام":   ("evening",   17,  0),
    "رات":   ("night",     20,  0),
}

# Urdu number words → integer (used for "بارہ بجے" etc.)
URDU_NUMBERS: dict[str, int] = {
    "ایک": 1, "دو": 2, "تین": 3, "چار": 4, "پانچ": 5,
    "چھ": 6, "سات": 7, "آٹھ": 8, "نو": 9, "دس": 10,
    "گیارہ": 11, "بارہ": 12,
}

# ---------------------------------------------------------------------------
# Punjabi (pa-PK) mappings
# ---------------------------------------------------------------------------

PUNJABI_RELATIVE: dict[str, int | None] = {
    "اج": 0,
    "کل": 1,
    "پرسوں": 2,
    "اگلے": None,   # must be followed by a day name
}

PUNJABI_DAYS: dict[str, int] = {
    "سوموار": 0,    # Monday
    "منگل": 1,      # Tuesday
    "بدھ": 2,       # Wednesday
    "وریام": 3,     # Thursday
    "جمعہ": 4,      # Friday
    "ہفتہ": 5,      # Saturday
    "اتوار": 6,     # Sunday
}

PUNJABI_TIME_PARTS: dict[str, tuple[str, int, int]] = {
    "سویرے":  ("morning",   9,  0),
    "دپہر":   ("afternoon", 14,  0),
    "شام":    ("evening",   17,  0),
    "رات":    ("night",     20,  0),
}

# ---------------------------------------------------------------------------
# English mappings
# ---------------------------------------------------------------------------

ENGLISH_RELATIVE: dict[str, int | None] = {
    "today": 0,
    "tomorrow": 1,
    "day after tomorrow": 2,
    "next": None,   # must be followed by a day name
}

ENGLISH_DAYS: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

ENGLISH_TIME_PARTS: dict[str, tuple[int, int]] = {
    "morning":   (9,  0),
    "afternoon": (14, 0),
    "evening":   (17, 0),
    "night":     (20, 0),
    "noon":      (12, 0),
    "midnight":  (0,  0),
}

# English digit / word hour patterns
_ENGLISH_HOUR_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}


class MultiLanguageDateParser:
    """
    Parse date/time expressions from Pakistani Urdu (ur-PK), Punjabi (pa-PK),
    and English into PKT-aware datetime objects.

    All returned datetimes are timezone-aware and expressed in PKT
    (pytz.timezone("Asia/Karachi"), UTC+5, no DST).
    """

    def __init__(self, calendar: PKTCalendar | None = None) -> None:
        self._calendar = calendar or PKTCalendar()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def parse(
        self,
        text: str,
        language: str,
        reference_date: date | None = None,
    ) -> datetime | None:
        """
        Parse *text* into a PKT-aware datetime.

        Parameters
        ----------
        text:
            Raw voice-input string (may contain mixed script or digits).
        language:
            ``"ur-PK"`` | ``"pa-PK"`` | ``"en"``
        reference_date:
            The date to evaluate relative expressions against.
            Defaults to today in PKT when *None*.

        Returns
        -------
        datetime | None
            PKT-aware datetime if a recognisable pattern was found,
            *None* otherwise.
        """
        if reference_date is None:
            reference_date = PKTCalendar.today_pkt()

        # Normalise whitespace; keep original case for Nastaliq script,
        # lower-case ASCII portions for English matching.
        normalised = " ".join(text.strip().split())
        lower = normalised.lower()

        if language == "ur-PK":
            return self._parse_urdu(normalised, lower, reference_date)
        elif language == "pa-PK":
            return self._parse_punjabi(normalised, lower, reference_date)
        else:
            # Treat anything else as English
            return self._parse_english(lower, reference_date)

    # ------------------------------------------------------------------
    # Language-specific parsers
    # ------------------------------------------------------------------

    def _parse_urdu(
        self, text: str, lower: str, ref: date
    ) -> datetime | None:
        resolved_date: date | None = None
        resolved_hour: int | None = None
        resolved_minute: int | None = None
        is_afternoon_context: bool = False

        # --- Time of day keywords ---
        for keyword, (label, default_h, default_m) in URDU_TIME_PARTS.items():
            if keyword in text:
                resolved_hour = default_h
                resolved_minute = default_m
                if label == "afternoon":
                    is_afternoon_context = True
                break

        # --- Explicit Urdu number + بجے pattern (e.g. "بارہ بجے", "تین بجے") ---
        hour_from_words = self._extract_urdu_number_hour(text, is_afternoon_context)
        if hour_from_words is not None:
            resolved_hour = hour_from_words
            resolved_minute = 0

        # --- Explicit digit time: "3:30", "15:00" ---
        digit_time = _extract_digit_time(lower)
        if digit_time is not None:
            resolved_hour, resolved_minute = digit_time

        # --- "اگلے" + day name ---
        if "اگلے" in text:
            for day_name, weekday in URDU_DAYS.items():
                if day_name in text:
                    resolved_date = _next_weekday(ref, weekday)
                    break

        # --- Bare day name (without اگلے) ---
        if resolved_date is None:
            for day_name, weekday in URDU_DAYS.items():
                if day_name in text and "اگلے" not in text:
                    resolved_date = _nearest_weekday(ref, weekday)
                    break

        # --- Relative expressions (آج / کل / پرسوں) ---
        if resolved_date is None:
            for keyword, delta in URDU_RELATIVE.items():
                if delta is not None and keyword in text:
                    resolved_date = ref + timedelta(days=delta)
                    break

        # If only a time was resolved (no date keyword), default to today.
        if resolved_date is None and resolved_hour is not None:
            resolved_date = ref

        if resolved_date is None:
            return None

        return self._build_pkt_datetime(resolved_date, resolved_hour, resolved_minute)

    def _parse_punjabi(
        self, text: str, lower: str, ref: date
    ) -> datetime | None:
        resolved_date: date | None = None
        resolved_hour: int | None = None
        resolved_minute: int | None = None
        is_afternoon_context: bool = False

        # --- Time of day keywords ---
        for keyword, (label, default_h, default_m) in PUNJABI_TIME_PARTS.items():
            if keyword in text:
                resolved_hour = default_h
                resolved_minute = default_m
                if label == "afternoon":
                    is_afternoon_context = True
                break

        # --- Urdu/Punjabi number word hour (shared number vocab) ---
        hour_from_words = self._extract_urdu_number_hour(text, is_afternoon_context)
        if hour_from_words is not None:
            resolved_hour = hour_from_words
            resolved_minute = 0

        # --- Digit time ---
        digit_time = _extract_digit_time(lower)
        if digit_time is not None:
            resolved_hour, resolved_minute = digit_time

        # --- "اگلے" + day name ---
        if "اگلے" in text:
            for day_name, weekday in PUNJABI_DAYS.items():
                if day_name in text:
                    resolved_date = _next_weekday(ref, weekday)
                    break

        # --- Bare day name ---
        if resolved_date is None:
            for day_name, weekday in PUNJABI_DAYS.items():
                if day_name in text and "اگلے" not in text:
                    resolved_date = _nearest_weekday(ref, weekday)
                    break

        # --- Relative expressions ---
        if resolved_date is None:
            for keyword, delta in PUNJABI_RELATIVE.items():
                if delta is not None and keyword in text:
                    resolved_date = ref + timedelta(days=delta)
                    break

        # If only a time was resolved (no date keyword), default to today.
        if resolved_date is None and resolved_hour is not None:
            resolved_date = ref

        if resolved_date is None:
            return None

        return self._build_pkt_datetime(resolved_date, resolved_hour, resolved_minute)

    def _parse_english(self, lower: str, ref: date) -> datetime | None:
        resolved_date: date | None = None
        resolved_hour: int | None = None
        resolved_minute: int | None = None
        is_afternoon_context: bool = False
        is_evening_context: bool = False
        is_night_context: bool = False

        # --- Time-of-day keywords ---
        for keyword, (default_h, default_m) in ENGLISH_TIME_PARTS.items():
            if keyword in lower:
                resolved_hour = default_h
                resolved_minute = default_m
                if keyword == "afternoon":
                    is_afternoon_context = True
                elif keyword == "evening":
                    is_evening_context = True
                elif keyword == "night":
                    is_night_context = True
                break

        # --- "X am/pm" or "X:YY am/pm" patterns ---
        ampm_time = _extract_ampm_time(lower)
        if ampm_time is not None:
            resolved_hour, resolved_minute = ampm_time

        # --- Plain digit time "HH:MM" or "H:MM" ---
        if ampm_time is None:
            digit_time = _extract_digit_time(lower)
            if digit_time is not None:
                h, m = digit_time
                # Apply PM shift for afternoon/evening/night context
                if h < 12 and (is_afternoon_context or is_evening_context or is_night_context):
                    h += 12
                resolved_hour, resolved_minute = h, m

        # --- English word hours ("three o'clock", "three") ---
        if resolved_hour is None:
            word_hour = _extract_english_word_hour(
                lower,
                is_afternoon_context or is_evening_context or is_night_context,
            )
            if word_hour is not None:
                resolved_hour = word_hour
                resolved_minute = 0

        # --- "day after tomorrow" (must check before "tomorrow") ---
        if "day after tomorrow" in lower:
            resolved_date = ref + timedelta(days=2)
        elif "tomorrow" in lower:
            resolved_date = ref + timedelta(days=1)
        elif "today" in lower:
            resolved_date = ref

        # --- "next <day>" ---
        if resolved_date is None:
            next_match = re.search(r"\bnext\s+(\w+)", lower)
            if next_match:
                day_name = next_match.group(1)
                weekday = ENGLISH_DAYS.get(day_name)
                if weekday is not None:
                    resolved_date = _next_weekday(ref, weekday)

        # --- Bare day name ---
        if resolved_date is None:
            for day_name, weekday in ENGLISH_DAYS.items():
                # Avoid matching partial words (e.g. "tuesday" inside "last tuesday")
                pattern = r"\b" + re.escape(day_name) + r"\b"
                if re.search(pattern, lower):
                    resolved_date = _nearest_weekday(ref, weekday)
                    break

        # --- Explicit date "DD/MM" or "DD-MM" or "DD/MM/YYYY" ---
        if resolved_date is None:
            explicit = _extract_explicit_date(lower, ref)
            if explicit is not None:
                resolved_date = explicit

        # If only a time was resolved (no date keyword), default to today.
        if resolved_date is None and resolved_hour is not None:
            resolved_date = ref

        if resolved_date is None:
            return None

        return self._build_pkt_datetime(resolved_date, resolved_hour, resolved_minute)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _extract_urdu_number_hour(
        self, text: str, is_afternoon_context: bool
    ) -> int | None:
        """
        Detect patterns like "بارہ بجے" (twelve o'clock) or "تین بجے" (three).
        Applies PM shift when afternoon context is active.
        """
        if "بجے" not in text and "بجے" not in text:
            # fast-exit: no clock word present
            pass

        for word, value in URDU_NUMBERS.items():
            # Match number word followed by بجے (with optional space)
            if re.search(rf"{re.escape(word)}\s*بجے", text):
                if is_afternoon_context and value < 12:
                    return value + 12
                return value
        return None

    @staticmethod
    def _build_pkt_datetime(
        d: date,
        hour: int | None,
        minute: int | None,
    ) -> datetime:
        """
        Combine a date with an optional time into a PKT-aware datetime.
        Defaults to 09:00 PKT when no time component was resolved.
        """
        h = hour if hour is not None else 9
        m = minute if minute is not None else 0
        return PKT.localize(datetime.combine(d, time(h, m)))


# ---------------------------------------------------------------------------
# Module-level helpers (no class state needed)
# ---------------------------------------------------------------------------

def _next_weekday(reference_date: date, target_weekday: int) -> date:
    """
    Return the next occurrence of *target_weekday* (0=Mon … 6=Sun) that is
    strictly after *reference_date*.
    """
    days_ahead = (target_weekday - reference_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return reference_date + timedelta(days=days_ahead)


def _nearest_weekday(reference_date: date, target_weekday: int) -> date:
    """
    Return the nearest occurrence of *target_weekday* on or after
    *reference_date*.  If today *is* target_weekday return today.
    """
    days_ahead = (target_weekday - reference_date.weekday()) % 7
    return reference_date + timedelta(days=days_ahead)


def _extract_digit_time(text: str) -> tuple[int, int] | None:
    """
    Find ``H:MM`` or ``HH:MM`` in *text* and return (hour, minute).
    Returns *None* if no pattern is found.
    """
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    return None


def _extract_ampm_time(text: str) -> tuple[int, int] | None:
    """
    Find patterns like ``3pm``, ``3:30pm``, ``3 pm``, ``03:30 am``.
    Returns (24-hour, minute) or *None*.
    """
    # "HH:MM am/pm" or "H am/pm"
    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        text,
        re.IGNORECASE,
    )
    if match:
        h = int(match.group(1))
        m = int(match.group(2)) if match.group(2) else 0
        meridiem = match.group(3).lower()
        if meridiem == "pm" and h != 12:
            h += 12
        elif meridiem == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    return None


def _extract_english_word_hour(
    text: str, afternoon_or_later: bool
) -> int | None:
    """
    Match English number words (one … twelve) optionally followed by
    "o'clock" or "oclock".  Applies PM shift when *afternoon_or_later*.
    """
    for word, value in _ENGLISH_HOUR_WORDS.items():
        pattern = r"\b" + re.escape(word) + r"(?:\s+o['']?clock)?\b"
        if re.search(pattern, text):
            if afternoon_or_later and value < 12:
                return value + 12
            return value
    return None


def _extract_explicit_date(text: str, ref: date) -> date | None:
    """
    Match ``DD/MM/YYYY``, ``DD-MM-YYYY``, ``DD/MM``, or ``DD-MM``.
    Uses *ref* year when year is absent.
    """
    # With year
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            pass

    # Without year
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        try:
            candidate = date(ref.year, month, day)
            # If the date has already passed this year, use next year
            if candidate < ref:
                candidate = date(ref.year + 1, month, day)
            return candidate
        except ValueError:
            pass

    return None
