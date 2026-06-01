"""
DTMF language selector — detects digit keypresses at call start.

The patient presses:
    1 → Urdu   (ur-PK)
    2 → Punjabi (pa-PK)
    3 → English (en)

If no keypress is received within `timeout_secs` the default language
(ur-PK) is used automatically.

A LanguageSelectedFrame is pushed downstream so that downstream processors
(ConfidenceFilteredSTT, LLM prompt loader, TTS provider selector, …) can
reconfigure themselves for the chosen language.

External callers (e.g. the Plivo webhook handler) call `handle_dtmf(digit)`
to deliver the keypress into this processor.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from pipecat.frames.frames import Frame, InputDTMFFrame, SystemFrame
from pipecat.processors.frame_processor import FrameProcessor

from providers.language_profile import LanguageProfile, get_profile_for_dtmf, get_profile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom frame
# ---------------------------------------------------------------------------

@dataclass
class LanguageSelectedFrame(SystemFrame):
    """
    Emitted once per call after the patient presses a DTMF digit (or after the
    selection timeout expires and the default language is applied).
    """
    profile: LanguageProfile
    dtmf_digit: str       # "1", "2", "3", or "" when default was applied


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

_VALID_DIGITS: frozenset[str] = frozenset({"1", "2", "3"})


class DTMFLanguageSelector(FrameProcessor):
    """
    Waits up to `timeout_secs` for a DTMF digit that selects the call language.

    Lifecycle
    ---------
    1. Pipeline starts → `process_frame` receives StartFrame → selection timer
       is armed via `_wait_for_selection`.
    2. Telephony webhook calls `handle_dtmf(digit)` when the patient presses a
       key → the waiting coroutine is woken and the selection is finalised.
    3. A `LanguageSelectedFrame` is pushed downstream exactly once, then the
       processor becomes transparent (all subsequent frames pass through).

    Thread-safety
    -------------
    `handle_dtmf` may be called from a different async task (HTTP webhook
    handler).  It sets an asyncio.Event that the waiting task observes.
    """

    def __init__(
        self,
        default_language: str = "ur-PK",
        timeout_secs: float = 5.0,
        preselected: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._default_language: str = default_language
        self._timeout_secs: float = timeout_secs
        # When the language is already known (browser dropdown / telephony with
        # a fixed line), skip the IVR wait entirely: emit LanguageSelectedFrame
        # immediately on StartFrame and never buffer audio. This removes ~5s of
        # dead latency on the first turn. Runtime DTMF switching still works.
        self._preselected: bool = preselected

        # Set by handle_dtmf(); observed by _wait_for_selection()
        self._digit_received: asyncio.Event = asyncio.Event()
        self._received_digit: str = ""

        # Prevents double-emission if handle_dtmf races with the timeout
        self._selection_done: bool = False

        # Queued non-StartFrames arrive before selection completes; hold them.
        self._pending_frames: list[tuple[Frame, object]] = []

        # Background task handle — kept so we can cancel on EndFrame
        self._selection_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # External interface (called by telephony webhook handler)
    # ------------------------------------------------------------------

    def handle_dtmf(self, digit: str) -> None:
        """
        Deliver a DTMF keypress.  Safe to call from any async task.

        Only the first valid digit in {1, 2, 3} is honoured; subsequent calls
        before selection completes are ignored.
        """
        if self._selection_done:
            return  # Already selected — ignore late keypresses

        if digit not in _VALID_DIGITS:
            logger.debug(
                "DTMFLanguageSelector: ignoring non-language digit %r", digit
            )
            return

        self._received_digit = digit
        self._digit_received.set()
        logger.debug("DTMFLanguageSelector: DTMF digit %r received", digit)

    # ------------------------------------------------------------------
    # FrameProcessor overrides
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction) -> None:
        from pipecat.frames.frames import StartFrame, EndFrame  # local import avoids circular issues

        # Base class registers StartFrame and flips the _started flag — required
        # in pipecat 0.0.85 or every downstream frame trips _check_started.
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            # Pass the start frame downstream immediately
            await self.push_frame(frame, direction)
            if self._preselected:
                # Language already known — emit selection now, no buffering.
                self._selection_done = True
                profile = get_profile(self._default_language)
                logger.info(
                    "DTMFLanguageSelector: preselected language=%s (no IVR wait)",
                    profile.code,
                )
                await self.push_frame(
                    LanguageSelectedFrame(profile=profile, dtmf_digit=""), direction
                )
            else:
                # Arm the IVR selection timer as a background task (telephony).
                self._selection_task = asyncio.create_task(
                    self._wait_for_selection(direction)
                )
            return

        if isinstance(frame, EndFrame):
            # Cancel any in-flight selection task
            if self._selection_task and not self._selection_task.done():
                self._selection_task.cancel()
            await self.push_frame(frame, direction)
            return

        # In-pipeline DTMF delivery — covers Plivo (InputDTMFFrame from its
        # serializer) and the browser test session (BrowserControlFrame from
        # BrowserPCMSerializer). Both routes funnel through handle_dtmf so
        # external HTTP webhook delivery still works identically.
        if isinstance(frame, InputDTMFFrame):
            digit = getattr(frame.button, "value", None) or str(frame.button)
            self.handle_dtmf(digit)
            return  # do not propagate — consumed

        # Browser control frame: imported lazily to avoid a circular import
        # between this module and pipeline.browser_serializer.
        try:
            from pipeline.browser_serializer import BrowserControlFrame
        except ImportError:
            BrowserControlFrame = None  # type: ignore
        if BrowserControlFrame is not None and isinstance(frame, BrowserControlFrame):
            payload = frame.payload or {}
            if payload.get("type") == "dtmf":
                digit = str(payload.get("digit", ""))
                self.handle_dtmf(digit)
            return  # do not propagate — consumed

        if not self._selection_done:
            # Buffer frames that arrive before language selection completes
            self._pending_frames.append((frame, direction))
        else:
            await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _wait_for_selection(self, direction) -> None:
        """
        Wait up to timeout_secs for a DTMF digit.  Emit LanguageSelectedFrame
        then flush buffered frames.
        """
        try:
            await asyncio.wait_for(
                self._digit_received.wait(),
                timeout=self._timeout_secs,
            )
            digit = self._received_digit
            profile = get_profile_for_dtmf(digit)
            logger.info(
                "DTMFLanguageSelector: digit=%r → language=%s", digit, profile.code
            )
        except asyncio.TimeoutError:
            digit = ""
            profile = get_profile(self._default_language)
            logger.info(
                "DTMFLanguageSelector: timeout — defaulting to %s", profile.code
            )

        self._selection_done = True

        # Emit selection frame
        selection_frame = LanguageSelectedFrame(profile=profile, dtmf_digit=digit)
        await self.push_frame(selection_frame, direction)

        # Flush buffered frames in order
        for buffered_frame, buffered_direction in self._pending_frames:
            await self.push_frame(buffered_frame, buffered_direction)
        self._pending_frames.clear()
