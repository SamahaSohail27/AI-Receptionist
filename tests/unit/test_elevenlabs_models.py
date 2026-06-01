"""Tests for ElevenLabs model whitelist + voice presets."""
from __future__ import annotations

import pytest


def test_default_model_is_flash():
    from providers.tts.elevenlabs import _validate_model, _DEFAULT_MODEL
    assert _validate_model("") == _DEFAULT_MODEL
    assert _validate_model(None) == _DEFAULT_MODEL  # type: ignore[arg-type]


def test_allowed_models_pass_through():
    from providers.tts.elevenlabs import _validate_model
    assert _validate_model("eleven_flash_v2_5") == "eleven_flash_v2_5"
    assert _validate_model("eleven_multilingual_v2") == "eleven_multilingual_v2"
    assert _validate_model("eleven_turbo_v2_5") == "eleven_turbo_v2_5"


def test_unknown_model_falls_back_to_flash():
    from providers.tts.elevenlabs import _validate_model, _DEFAULT_MODEL
    assert _validate_model("eleven_bogus_v9_99") == _DEFAULT_MODEL


def test_v3_is_blocked():
    from providers.tts.elevenlabs import _validate_model
    for bad in ("eleven_v3", "eleven_v3_alpha", "eleven_v3_2024"):
        with pytest.raises(ValueError, match="HTTP 403"):
            _validate_model(bad)


def test_voice_presets_are_balanced():
    from providers.tts.elevenlabs import VOICE_PRESETS
    f = [v for v in VOICE_PRESETS if v["gender"] == "female"]
    m = [v for v in VOICE_PRESETS if v["gender"] == "male"]
    assert len(f) >= 3, "expect at least 3 female voices"
    assert len(m) >= 3, "expect at least 3 male voices"
    # All voice ids look like real ElevenLabs ids (20+ chars, alnum)
    for v in VOICE_PRESETS:
        assert len(v["voice_id"]) >= 20
        assert v["voice_id"].isalnum()


def test_model_presets_include_flash_default():
    from providers.tts.elevenlabs import MODEL_PRESETS
    ids = [m["model_id"] for m in MODEL_PRESETS]
    assert "eleven_flash_v2_5" in ids
    assert "eleven_multilingual_v2" in ids
    assert "eleven_turbo_v2_5" in ids
    # v3 must not appear in user-facing presets
    assert not any("v3" in i for i in ids)
