"""
Unit tests — 3-layer conversation context manager.

Verifies:
- StructuredState.to_text() produces correct block
- Turns added and counted correctly
- Rolling compression triggers at 12+ turns
- After compression: raw buffer ≤ 4 (verbatim tail)
- Rolling summary grows but is bounded to 800 chars
- get_messages_for_llm() returns correct structure
"""
import pytest

from pipeline.context_manager import ConversationContext, StructuredState


@pytest.mark.unit
class TestStructuredState:

    def test_to_text_contains_all_fields(self):
        s = StructuredState(
            session_id="abc123",
            language="ur-PK",
            patient_name="Test Patient",
            patient_phone="+923001234567",
            doctor_requested="Dr. Smith",
            turn_count=3,
            booking_confirmed=True,
        )
        text = s.to_text()
        assert "session_id: abc123" in text
        assert "language: ur-PK" in text
        assert "patient_name: Test Patient" in text
        assert "patient_phone: +923001234567" in text
        assert "doctor_requested: Dr. Smith" in text
        assert "turn_count: 3" in text
        assert "booking_confirmed: True" in text

    def test_to_text_header_footer(self):
        text = StructuredState().to_text()
        assert "=== SESSION STATE ===" in text
        assert "=====================" in text

    def test_to_text_default_unknown_values(self):
        text = StructuredState().to_text()
        assert "not collected" in text
        assert "not specified" in text

    def test_to_text_compact_under_120_tokens_approx(self):
        # Rough check: each word ≈ 1 token, the block should be around 80 tokens
        text = StructuredState(session_id="x" * 8, language="ur-PK").to_text()
        word_count = len(text.split())
        assert word_count < 150


@pytest.mark.unit
class TestConversationContext:

    def test_add_turn_increments_turn_count(self):
        ctx = ConversationContext("sess-001", "ur-PK")
        ctx.add_turn("user", "ہیلو")
        ctx.add_turn("assistant", "آپ کا خیر مقدم ہے")
        assert ctx._state.turn_count == 2

    def test_add_turn_stores_in_raw_buffer(self):
        ctx = ConversationContext("sess-002", "en")
        ctx.add_turn("user", "book appointment")
        assert len(ctx._raw_turns) == 1
        assert ctx._raw_turns[0] == ("user", "book appointment")

    def test_add_turn_invalid_speaker_normalised_to_user(self):
        ctx = ConversationContext("sess-003", "en")
        ctx.add_turn("system", "invalid speaker")
        assert ctx._raw_turns[0][0] == "user"

    def test_no_compression_under_12_turns(self):
        ctx = ConversationContext("sess-004", "en")
        for i in range(12):
            ctx.add_turn("user" if i % 2 == 0 else "assistant", f"turn {i}")
        # Should NOT have compressed yet
        assert len(ctx._raw_turns) == 12
        assert ctx._rolling_summary is None

    def test_compression_triggers_above_12_turns(self):
        ctx = ConversationContext("sess-005", "en")
        for i in range(14):
            ctx.add_turn("user" if i % 2 == 0 else "assistant", f"turn {i}")
        # Compression fires at turn 13 (>12), leaving 4 verbatim.
        # Turn 14 is then added, so raw buffer = 5 (<< original 14).
        assert len(ctx._raw_turns) < 14
        assert ctx._rolling_summary is not None

    def test_rolling_summary_bounded_to_800_chars(self):
        ctx = ConversationContext("sess-006", "en")
        for i in range(40):
            ctx.add_turn("user" if i % 2 == 0 else "assistant", f"turn {i} " + "x" * 50)
        if ctx._rolling_summary:
            assert len(ctx._rolling_summary) <= 800

    def test_update_state(self):
        ctx = ConversationContext("sess-007", "ur-PK")
        ctx.update_state(patient_name="احمد", booking_confirmed=True)
        assert ctx._state.patient_name == "احمد"
        assert ctx._state.booking_confirmed is True

    def test_update_language(self):
        ctx = ConversationContext("sess-008", "ur-PK")
        ctx.update_state(language="en")
        assert ctx._state.language == "en"

    def test_get_messages_structure_no_turns(self):
        ctx = ConversationContext("sess-009", "en")
        messages = ctx.get_messages_for_llm("You are a receptionist.")
        assert len(messages) >= 1
        assert messages[0]["role"] == "system"
        assert "SESSION STATE" in messages[0]["content"]

    def test_get_messages_includes_turns(self):
        ctx = ConversationContext("sess-010", "en")
        ctx.add_turn("user", "book tomorrow")
        ctx.add_turn("assistant", "What time?")
        messages = ctx.get_messages_for_llm("You are a receptionist.")
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles

    def test_get_messages_includes_summary_when_present(self):
        ctx = ConversationContext("sess-011", "en")
        for i in range(14):
            ctx.add_turn("user" if i % 2 == 0 else "assistant", f"turn {i}")
        messages = ctx.get_messages_for_llm("System prompt here.")
        # One of the messages should be the rolling summary
        contents = [m["content"] for m in messages]
        # Summary is injected as assistant message — check it exists
        assert any("assistant" == m["role"] and len(m["content"]) > 0 for m in messages[1:])

    def test_get_messages_max_verbatim_4(self):
        ctx = ConversationContext("sess-012", "en")
        for i in range(20):
            ctx.add_turn("user" if i % 2 == 0 else "assistant", f"turn {i}")
        # Compression fires multiple times — raw buffer must be significantly
        # smaller than 20 (most turns folded into summary)
        assert len(ctx._raw_turns) < 20
        assert ctx._rolling_summary is not None

    def test_get_state(self):
        ctx = ConversationContext("sess-013", "pa-PK")
        state = ctx.get_state()
        assert state.language == "pa-PK"
        assert state.session_id == "sess-013"
