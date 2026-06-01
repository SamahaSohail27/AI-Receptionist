"""
3-layer conversation context manager for the LLM.

Layer 1 — Structured state (StructuredState dataclass):
    ~80 tokens of key-value data.  Never grows with conversation length.
    Injected as a text block appended to the system prompt each turn.

Layer 2 — Rolling summary:
    When the raw turn buffer exceeds 12 turns the oldest 8 turns are
    compressed into a single paragraph summary.  The summary is injected
    as a "summary" assistant message before the verbatim turns.

Layer 3 — Last 4 raw turns:
    The most recent 4 (speaker, text) pairs are always kept verbatim
    so the LLM has precise short-term recall.

Message list returned by get_messages_for_llm():
    [
        {"role": "system",    "content": "<system_prompt>\n\n<structured_state>"},
        {"role": "assistant", "content": "<rolling_summary>"},  ← only if summary exists
        {"role": "user/assistant", "content": "..."},           ← last ≤4 raw turns
        ...
    ]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structured state (Layer 1)
# ---------------------------------------------------------------------------

@dataclass
class StructuredState:
    """
    Flat key-value block injected into every system prompt.
    Keep it narrow — every field here costs tokens on every LLM call.
    """
    patient_name: Optional[str] = None
    patient_phone: Optional[str] = None
    doctor_requested: Optional[str] = None
    preferred_date: Optional[str] = None
    appointment_type: str = "consultation"
    cnic_verified: bool = False
    language: str = "ur-PK"
    session_id: str = ""
    turn_count: int = 0
    booking_confirmed: bool = False

    def to_text(self) -> str:
        """Render the state as a compact text block for injection into the system prompt."""
        lines = [
            "=== SESSION STATE ===",
            f"session_id: {self.session_id or 'unknown'}",
            f"language: {self.language}",
            f"turn_count: {self.turn_count}",
            f"patient_name: {self.patient_name or 'not collected'}",
            f"patient_phone: {self.patient_phone or 'not collected'}",
            f"doctor_requested: {self.doctor_requested or 'not specified'}",
            f"preferred_date: {self.preferred_date or 'not specified'}",
            f"appointment_type: {self.appointment_type}",
            f"cnic_verified: {self.cnic_verified}",
            f"booking_confirmed: {self.booking_confirmed}",
            "=====================",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Conversation context
# ---------------------------------------------------------------------------

# Thresholds that control when compression kicks in
_COMPRESS_THRESHOLD = 12   # compress when raw buffer exceeds this
_COMPRESS_BATCH = 8        # how many old turns to fold into the summary
_VERBATIM_KEEP = 4         # number of most recent raw turns always kept


class ConversationContext:
    """
    Manages 3-layer conversation context for one active call session.

    Thread safety: designed for use within a single asyncio event loop.
    Not safe for concurrent access from multiple coroutines without external locking.
    """

    def __init__(self, session_id: str, language: str = "ur-PK") -> None:
        self._state = StructuredState(session_id=session_id, language=language)
        # Raw turn buffer: list of (speaker, text)
        # speaker is "user" or "assistant"
        self._raw_turns: list[tuple[str, str]] = []
        # Rolling summary paragraph (Layer 2)
        self._rolling_summary: Optional[str] = None

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    def add_turn(self, speaker: str, text: str) -> None:
        """
        Append a new (speaker, text) turn to the raw buffer.

        Automatically triggers rolling compression when the buffer
        exceeds _COMPRESS_THRESHOLD.

        Args:
            speaker: "user" or "assistant"
            text:    The spoken / generated text for this turn.
        """
        if speaker not in ("user", "assistant"):
            logger.warning(
                "ConversationContext.add_turn: unexpected speaker %r — normalising to 'user'",
                speaker,
            )
            speaker = "user"

        self._raw_turns.append((speaker, text))
        self._state.turn_count += 1

        # Trigger compression if raw buffer is too long
        if len(self._raw_turns) > _COMPRESS_THRESHOLD:
            self._maybe_compress()

    def _maybe_compress(self) -> None:
        """
        Compress the oldest _COMPRESS_BATCH turns into the rolling summary.
        Keeps the most recent _VERBATIM_KEEP turns verbatim.
        """
        if len(self._raw_turns) <= _COMPRESS_THRESHOLD:
            return

        # Take the oldest batch, leaving the verbatim tail
        compress_count = len(self._raw_turns) - _VERBATIM_KEEP
        # Round down to _COMPRESS_BATCH so we don't compress tiny batches
        compress_count = max(compress_count, _COMPRESS_BATCH)
        compress_count = min(compress_count, len(self._raw_turns) - _VERBATIM_KEEP)

        if compress_count <= 0:
            return

        turns_to_compress = self._raw_turns[:compress_count]
        self._raw_turns = self._raw_turns[compress_count:]

        new_summary_chunk = self._compress_to_summary(turns_to_compress)

        if self._rolling_summary:
            # Append to existing summary, keeping it bounded
            combined = self._rolling_summary + " " + new_summary_chunk
            # Truncate to 800 chars to prevent the summary itself from growing unbounded
            self._rolling_summary = combined[-800:]
        else:
            self._rolling_summary = new_summary_chunk

        logger.debug(
            "ConversationContext: compressed %d turns into rolling summary (len=%d chars)",
            compress_count,
            len(self._rolling_summary),
        )

    # ------------------------------------------------------------------
    # LLM message construction
    # ------------------------------------------------------------------

    def get_messages_for_llm(self, system_prompt: str) -> list[dict]:
        """
        Build the full messages list for the LLM call.

        Structure:
            [system message with structured state injected]
            [optional: rolling summary as assistant message]
            [last ≤4 raw turns as user/assistant messages]

        Args:
            system_prompt: The base system prompt for the current language.

        Returns:
            List of {"role": ..., "content": ...} dicts suitable for OpenAI chat.
        """
        messages: list[dict] = []

        # Layer 1: system prompt + structured state
        full_system = system_prompt + "\n\n" + self._state.to_text()
        messages.append({"role": "system", "content": full_system})

        # Layer 2: rolling summary (injected as an assistant message)
        if self._rolling_summary:
            messages.append({
                "role": "assistant",
                "content": f"[Conversation summary so far: {self._rolling_summary}]",
            })

        # Layer 3: last _VERBATIM_KEEP raw turns verbatim
        verbatim = self._raw_turns[-_VERBATIM_KEEP:] if self._raw_turns else []
        for speaker, text in verbatim:
            messages.append({"role": speaker, "content": text})

        return messages

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def update_state(self, **kwargs) -> None:
        """
        Update one or more fields of the StructuredState.

        Only existing StructuredState fields are accepted; unknown keys are
        logged and ignored.

        Example:
            ctx.update_state(patient_name="Ahmed", booking_confirmed=True)
        """
        valid_fields = {f.name for f in self._state.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        for key, value in kwargs.items():
            if key in valid_fields:
                setattr(self._state, key, value)
            else:
                logger.warning(
                    "ConversationContext.update_state: unknown field %r — ignored",
                    key,
                )

    def get_state(self) -> StructuredState:
        """Return a reference to the current StructuredState."""
        return self._state

    # ------------------------------------------------------------------
    # Internal compression
    # ------------------------------------------------------------------

    def _compress_to_summary(self, turns: list[tuple[str, str]]) -> str:
        """
        Compress a batch of (speaker, text) turns into a short summary paragraph.

        Production note: this would call an LLM with a summarisation prompt.
        For now it produces a simple concatenation of speaker-prefixed lines,
        truncated to 400 characters so it stays inside Layer 2's token budget.

        Args:
            turns: List of (speaker, text) pairs to compress.

        Returns:
            A single paragraph string (≤400 chars).
        """
        if not turns:
            return ""

        lines = []
        for speaker, text in turns:
            label = "Patient" if speaker == "user" else "Receptionist"
            # Truncate individual turns to avoid one long turn dominating the summary
            truncated = text[:120] + "..." if len(text) > 120 else text
            lines.append(f"{label}: {truncated}")

        full_text = " | ".join(lines)

        # Hard cap at 400 characters
        if len(full_text) > 400:
            full_text = full_text[:397] + "..."

        return full_text

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def debug_info(self) -> dict:
        """Return diagnostic information for logging."""
        return {
            "session_id": self._state.session_id,
            "turn_count": self._state.turn_count,
            "raw_turns_buffered": len(self._raw_turns),
            "has_rolling_summary": self._rolling_summary is not None,
            "rolling_summary_len": len(self._rolling_summary) if self._rolling_summary else 0,
        }
