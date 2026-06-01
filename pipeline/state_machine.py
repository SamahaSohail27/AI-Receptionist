"""
Conversation state machine for the AI Medical Receptionist.

States move the call through a well-defined lifecycle.  Invalid transitions
are rejected and logged so that bugs in the LLM tool layer surface immediately
rather than silently corrupting call state.

All UI broadcasts are fire-and-forget via ws_manager.broadcast_fire_and_forget
so the hot path is never blocked.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Set

from core.ws_manager import ws_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State definitions
# ---------------------------------------------------------------------------

class ConversationState(Enum):
    IDLE            = "idle"
    LANGUAGE_SELECT = "language_select"
    GREETING        = "greeting"
    INTAKE          = "intake"
    SCHEDULING      = "scheduling"
    CONFIRMATION    = "confirmation"
    GOODBYE         = "goodbye"
    ESCALATION      = "escalation"
    EMERGENCY       = "emergency"


# Allowed transitions: from-state → set of reachable to-states
VALID_TRANSITIONS: dict[ConversationState, Set[ConversationState]] = {
    ConversationState.IDLE: {
        ConversationState.LANGUAGE_SELECT,
    },
    ConversationState.LANGUAGE_SELECT: {
        ConversationState.GREETING,
    },
    ConversationState.GREETING: {
        ConversationState.INTAKE,
        ConversationState.ESCALATION,
        ConversationState.EMERGENCY,
    },
    ConversationState.INTAKE: {
        ConversationState.SCHEDULING,
        ConversationState.ESCALATION,
        ConversationState.EMERGENCY,
    },
    ConversationState.SCHEDULING: {
        ConversationState.CONFIRMATION,
        ConversationState.INTAKE,
        ConversationState.ESCALATION,
        ConversationState.EMERGENCY,
    },
    ConversationState.CONFIRMATION: {
        ConversationState.GOODBYE,
        ConversationState.SCHEDULING,
        ConversationState.ESCALATION,
        ConversationState.EMERGENCY,
    },
    ConversationState.GOODBYE: set(),
    ConversationState.ESCALATION: {
        ConversationState.GOODBYE,
    },
    ConversationState.EMERGENCY: {
        ConversationState.GOODBYE,
    },
}

# States from which the call cannot recover — once here the pipeline should
# wind down and disconnect.
_TERMINAL_STATES: Set[ConversationState] = {
    ConversationState.GOODBYE,
    ConversationState.ESCALATION,
    ConversationState.EMERGENCY,
}


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class ConversationStateMachine:
    """
    Tracks and enforces valid state transitions for a single call session.

    Broadcasts a `call.state_changed` event (fire-and-forget) to all connected
    UI WebSocket clients on every successful transition.
    """

    def __init__(self, session_id: str, language: str) -> None:
        self.session_id: str = session_id
        self.language: str = language
        self.current_state: ConversationState = ConversationState.IDLE

    # ------------------------------------------------------------------
    # Transition logic
    # ------------------------------------------------------------------

    def transition(self, new_state: ConversationState) -> bool:
        """
        Attempt to move from current_state to new_state.

        Returns:
            True  — transition was valid and has been applied.
            False — transition is not in VALID_TRANSITIONS; state unchanged.
        """
        if not self.can_transition(new_state):
            logger.warning(
                "StateMachine [%s]: invalid transition %s → %s",
                self.session_id,
                self.current_state.value,
                new_state.value,
            )
            return False

        previous = self.current_state
        self.current_state = new_state

        logger.info(
            "StateMachine [%s]: %s → %s",
            self.session_id,
            previous.value,
            new_state.value,
        )

        # Fire-and-forget UI broadcast — never await
        ws_manager.broadcast_fire_and_forget(
            {
                "type": "call.state_changed",
                "session_id": self.session_id,
                "state": new_state.value,
                "previous_state": previous.value,
                "language": self.language,
            }
        )

        return True

    def can_transition(self, new_state: ConversationState) -> bool:
        """Return True if new_state is a valid next state from current_state."""
        return new_state in VALID_TRANSITIONS.get(self.current_state, set())

    def is_terminal(self) -> bool:
        """Return True when the call is in a terminal state and should wind down."""
        return self.current_state in _TERMINAL_STATES

    # ------------------------------------------------------------------
    # Representation helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"ConversationStateMachine("
            f"session_id={self.session_id!r}, "
            f"state={self.current_state.value!r}, "
            f"language={self.language!r})"
        )
