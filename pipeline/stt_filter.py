"""
Confidence-filtered STT processor.

Wraps any STT transcription output and drops frames that fall below the
language-specific confidence threshold or are too short to be meaningful.

SACRED thresholds (never change):
  Urdu    0.45
  English 0.70
  Punjabi 0.40
"""
from __future__ import annotations

import logging
from typing import List

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameProcessor

from providers.language_profile import LanguageProfile

logger = logging.getLogger(__name__)


class ConfidenceFilteredSTT(FrameProcessor):
    """
    Drop TranscriptionFrames whose confidence is below the per-language
    threshold, or whose text is too short (< 2 stripped characters).

    All other frame types are passed through unchanged.
    """

    # Minimum number of non-whitespace characters before we consider a
    # transcription worth forwarding downstream.
    _MIN_TEXT_LENGTH: int = 2

    def __init__(
        self,
        confidence_threshold: float,
        noise_words: List[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.confidence_threshold: float = confidence_threshold
        self.noise_words: List[str] = noise_words or []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_language_profile(self, profile: LanguageProfile) -> None:
        """Swap in a new language profile at runtime (e.g. after DTMF selection)."""
        self.confidence_threshold = profile.stt.confidence_threshold
        self.noise_words = list(profile.noise_words)
        logger.debug(
            "ConfidenceFilteredSTT updated: lang=%s threshold=%.2f noise_words=%d",
            profile.code,
            self.confidence_threshold,
            len(self.noise_words),
        )

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            await self._handle_transcription(frame, direction)
        else:
            await self.push_frame(frame, direction)

    async def _handle_transcription(
        self, frame: TranscriptionFrame, direction
    ) -> None:
        text = frame.text.strip() if frame.text else ""

        # DIAGNOSTIC: log every FINAL transcription the filter receives, before
        # any drop decision. Combined with the STTBroadcaster log (which sits
        # AFTER this filter) this shows the full chain: Deepgram → filter
        # (received/dropped) → broadcaster → LLM aggregator.
        logger.info("STT filter: received FINAL text=%r (threshold=%.2f)",
                    text[:120], self.confidence_threshold)

        # 1. Drop utterances that are too short to be real speech
        if len(text) < self._MIN_TEXT_LENGTH:
            logger.info(
                "STT filter: DROPPED short utterance (len=%d) text=%r", len(text), text
            )
            return

        # 2. Obtain confidence value. pipecat 0.0.85's TranscriptionFrame has
        #    NO confidence field, and batch STTs (OpenAI/Groq Whisper) don't
        #    provide one at all. When confidence is unavailable we MUST NOT
        #    drop the frame — otherwise every transcript is discarded and the
        #    conversation never advances. The threshold only applies when a
        #    real confidence value is present (e.g. Deepgram word confidence).
        raw_conf = getattr(frame, "confidence", None)
        if raw_conf is None:
            # Some STTs stash details on .result — try Deepgram's shape.
            try:
                result = getattr(frame, "result", None)
                if result is not None:
                    alt = result.channel.alternatives[0]
                    raw_conf = getattr(alt, "confidence", None)
            except (AttributeError, IndexError, TypeError):
                raw_conf = None

        # 3. Drop low-confidence frames only when confidence is actually known.
        if raw_conf is not None and float(raw_conf) < self.confidence_threshold:
            logger.info(
                "STT filter: DROPPED low-confidence frame (conf=%.3f < threshold=%.2f) text=%r",
                float(raw_conf),
                self.confidence_threshold,
                text,
            )
            return

        # 4. High enough confidence — pass downstream unchanged
        await self.push_frame(frame, direction)
