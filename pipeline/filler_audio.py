"""
Filler audio player — hides 500ms-2s dead air during tool call gaps.

Pre-recorded .wav files are stored in language-specific sub-directories under
the configured audio_dir.  A random file is selected each time to avoid the
"stuck record" effect.

Directory layout:
    static/audio/filler_ur/   ← ur-PK utterances
    static/audio/filler_pa/   ← pa-PK utterances
    static/audio/filler_en/   ← en utterances

FILLER_TEXTS is retained so that the first-run Azure TTS generation script
(separate utility) knows what phrases to synthesise and save into the
directories above.
"""
from __future__ import annotations

import logging
import os
import random
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filler phrase texts — used by the TTS generation utility on first run.
# These are NOT used at runtime; only the pre-recorded .wav files are played.
# ---------------------------------------------------------------------------

FILLER_TEXTS: dict[str, list[str]] = {
    "ur-PK": [
        "ایک لمحہ، میں چیک کر رہا ہوں",
        "ابھی دیکھتا ہوں",
        "بالکل، ایک سیکنڈ",
    ],
    "pa-PK": [
        "اک پل، میں ویکھدا ہاں",
        "ہن دسدا ہاں",
    ],
    "en": [
        "One moment, let me check that",
        "Just a second",
        "Let me look that up",
    ],
}

# Mapping from language code to filler sub-directory name
_LANG_TO_DIR: dict[str, str] = {
    "ur-PK": "filler_ur",
    "pa-PK": "filler_pa",
    "en":    "filler_en",
}


class FillerAudioPlayer:
    """
    Returns the path to a random filler .wav file for the given language.

    Usage (caller is responsible for actually playing the audio):

        player = FillerAudioPlayer()
        path = player.get_filler_path("ur-PK")
        if path:
            # push AudioRawFrame loaded from path into the pipeline
            ...
    """

    def __init__(self, audio_dir: str = "static/audio") -> None:
        self.audio_dir = audio_dir

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_filler_path(self, language: str) -> Optional[str]:
        """
        Return a random .wav filler file path for the given language code.

        Args:
            language: BCP-47 language code ("ur-PK", "pa-PK", or "en").

        Returns:
            Absolute-or-relative path to a .wav file, or None if the
            directory does not exist or contains no .wav files.
        """
        subdir = _LANG_TO_DIR.get(language)
        if subdir is None:
            logger.warning(
                "FillerAudioPlayer: unknown language code %r — no filler will play",
                language,
            )
            return None

        filler_dir = os.path.join(self.audio_dir, subdir)

        if not os.path.isdir(filler_dir):
            logger.debug(
                "FillerAudioPlayer: directory %r does not exist — skipping filler",
                filler_dir,
            )
            return None

        wav_files = [
            f for f in os.listdir(filler_dir) if f.lower().endswith(".wav")
        ]

        if not wav_files:
            logger.debug(
                "FillerAudioPlayer: no .wav files in %r — skipping filler",
                filler_dir,
            )
            return None

        chosen = random.choice(wav_files)
        path = os.path.join(filler_dir, chosen)
        logger.debug("FillerAudioPlayer: selected filler %r for lang=%r", path, language)
        return path

    def available_languages(self) -> list[str]:
        """Return language codes that have at least one filler .wav available."""
        available: list[str] = []
        for lang, subdir in _LANG_TO_DIR.items():
            filler_dir = os.path.join(self.audio_dir, subdir)
            if os.path.isdir(filler_dir):
                wav_files = [
                    f for f in os.listdir(filler_dir) if f.lower().endswith(".wav")
                ]
                if wav_files:
                    available.append(lang)
        return available
