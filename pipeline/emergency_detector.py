"""
Pre-LLM emergency keyword detector.

Checks caller transcripts against a curated keyword list before the text
reaches the LLM.  A positive match must trigger an immediate call transfer
to the triage nurse — target: < 10 seconds end-to-end.

Design decisions:
- English keywords are ALWAYS checked regardless of the active language
  (bilingual patients may switch to English mid-call in an emergency).
- Detection is case-insensitive and uses substring matching so partial
  phrases (e.g. "مر رہا ہوں" inside a longer sentence) are caught.
- The matched keywords are intentionally NOT forwarded to the UI; they are
  only used for internal logging / audit.
"""
from __future__ import annotations

import logging

from providers.language_profile import LanguageProfile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword registry
# ---------------------------------------------------------------------------

# High-severity (urgent but not life-threatening) — caller still gets booked
# but on the earliest available slot. The LLM uses the `triage_severity` tool
# for nuance; this lexicon is the pre-LLM fast path so we mark calls urgent
# even if the LLM is slow to react.
HIGH_SEVERITY_KEYWORDS: dict[str, list[str]] = {
    "ur-PK": [
        "شدید", "بہت زیادہ", "ناقابل برداشت", "ناقابلِ برداشت",
        "بہت تکلیف", "بہت درد", "اچانک",
    ],
    "pa-PK": [
        "بہت تکلیف", "ناقابل برداشت", "بہت زیادہ", "بڑا درد",
    ],
    "en": [
        "severe", "very bad", "unbearable", "excruciating",
        "extreme pain", "agony", "won't stop", "wont stop",
        "hours of pain", "getting worse",
    ],
}


EMERGENCY_KEYWORDS: dict[str, list[str]] = {
    "ur-PK": [
        "سینے میں درد",
        "سانس نہیں آ رہا",
        "سانس نہیں آرہا",
        "بے ہوشی",
        "بے ہوش",
        "ہارٹ اٹیک",
        "دل کا دورہ",
        "حادثہ",
        "بہت خون",
        "خون نہیں رک رہا",
        "فالج",
        "دورہ پڑا",
        "مر رہا ہوں",
        "مر رہی ہوں",
        "ایمرجنسی",
    ],
    "pa-PK": [
        "سینے اچ درد",
        "ساہ نئیں آؤندا",
        "ہوش نئیں",
        "حادثہ ہو گیا",
        "بہت خون",
        "دل دا دورہ",
        "مر رہا ہاں",
        "ایمرجنسی",
    ],
    "en": [
        "chest pain",
        "can't breathe",
        "cannot breathe",
        "unconscious",
        "heart attack",
        "accident",
        "heavy bleeding",
        "won't stop bleeding",
        "stroke",
        "seizure",
        "dying",
        "emergency",
        "call ambulance",
    ],
}


class EmergencyDetector:
    """
    Stateless keyword detector for pre-LLM emergency triage.

    Usage:
        detector = EmergencyDetector()
        if detector.detect(transcript, language):
            # transfer immediately
    """

    def __init__(self) -> None:
        # Build lower-case keyword lists at construction time for fast lookup
        self._keywords_lower: dict[str, list[str]] = {
            lang: [kw.lower() for kw in keywords]
            for lang, keywords in EMERGENCY_KEYWORDS.items()
        }
        self._high_lower: dict[str, list[str]] = {
            lang: [kw.lower() for kw in keywords]
            for lang, keywords in HIGH_SEVERITY_KEYWORDS.items()
        }

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def detect(self, text: str, language: str) -> bool:
        """
        Return True if *text* contains any emergency keyword.

        Rules:
        1. Always check English keywords (bilingual patients may switch mid-call).
        2. Also check the keywords for the active *language*.
        3. Matching is case-insensitive substring search.

        Args:
            text:     The caller's transcribed utterance.
            language: BCP-47 language code of the active session language.

        Returns:
            True if one or more keywords are found; False otherwise.
        """
        if not text:
            return False

        text_lower = text.lower()

        # Always check English
        for kw in self._keywords_lower.get("en", []):
            if kw in text_lower:
                logger.warning(
                    "EmergencyDetector: English keyword %r matched in text (lang=%s)",
                    kw,
                    language,
                )
                return True

        # Check active language (skip if it is already "en" — already checked above)
        if language != "en":
            for kw in self._keywords_lower.get(language, []):
                if kw in text_lower:
                    logger.warning(
                        "EmergencyDetector: %s keyword %r matched in text",
                        language,
                        kw,
                    )
                    return True

        return False

    def severity(self, text: str, language: str) -> str:
        """Classify a transcript as 'emergency', 'high', or 'low'.

        Used by the pipecat pipeline to mark a call urgent without waiting on
        an LLM tool call. Emergency wins over high; first match wins within a
        tier. Caller-side mapping:
            emergency → transfer to triage nurse, do NOT book.
            high      → book earliest slot today/tomorrow, mark urgent.
            low       → normal booking flow.
        """
        if self.detect(text, language):
            return "emergency"
        if not text:
            return "low"
        text_lower = text.lower()
        for kw in self._high_lower.get("en", []):
            if kw in text_lower:
                return "high"
        if language != "en":
            for kw in self._high_lower.get(language, []):
                if kw in text_lower:
                    return "high"
        return "low"

    def get_matched_keywords(self, text: str, language: str) -> list[str]:
        """
        Return a list of all matched emergency keywords for logging / audit.

        WARNING: Never expose this list in the UI or in TTS responses.
        Intended for internal structured logging only.

        Args:
            text:     The caller's transcribed utterance.
            language: BCP-47 language code of the active session language.

        Returns:
            List of matched keyword strings (original case from EMERGENCY_KEYWORDS).
        """
        if not text:
            return []

        text_lower = text.lower()
        matched: list[str] = []

        # Collect all matches across English and active language
        languages_to_check = {"en"}
        if language != "en":
            languages_to_check.add(language)

        for lang in languages_to_check:
            original_keywords = EMERGENCY_KEYWORDS.get(lang, [])
            lower_keywords = self._keywords_lower.get(lang, [])
            for original, lower in zip(original_keywords, lower_keywords):
                if lower in text_lower:
                    matched.append(original)

        return matched
