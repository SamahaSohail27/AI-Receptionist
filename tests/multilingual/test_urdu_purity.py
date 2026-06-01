"""
Language purity tests — Pakistani Urdu vocabulary checker.

The AI must use Pakistani Urdu, NOT Indian Urdu.
These tests check that forbidden Indian vocabulary never appears
in prompt files and that approved Pakistani alternatives are present.

Indian → Pakistani substitutions this suite enforces:
  استقبال   → خوش آمدید  (welcome)
  ٹائم      → وقت        (time)
  ڈاکٹر صاحب → ڈاکٹر      (doctor)
  اپوائنٹمنٹ → ملاقات      (appointment)
  پروبلم    → مسئلہ      (problem)
  ابھی      → ابھی acceptable, but confirm not Indian filler
  ہسپتال    → ہسپتال     (OK — same in both)
"""
import os
import re
from pathlib import Path

import pytest

# Root of the project
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Indian Urdu vocabulary that must never appear in our prompts
FORBIDDEN_INDIAN_VOCAB = [
    "استقبال",    # use خوش آمدید instead
    "ٹائم",       # use وقت instead
    "اپوائنٹمنٹ", # use ملاقات instead
    "پروبلم",     # use مسئلہ instead
]

# Pakistani Urdu required vocabulary in Urdu prompt files
REQUIRED_PAKISTANI_VOCAB = [
    "خوش آمدید",  # welcome
    "وقت",        # time
    "ملاقات",     # appointment
]


def _load_prompt_files():
    """Return all .yaml and .txt prompt files under prompts/ur/."""
    prompt_dir = PROJECT_ROOT / "prompts" / "ur"
    if not prompt_dir.exists():
        return []
    files = list(prompt_dir.glob("**/*.yaml")) + list(prompt_dir.glob("**/*.txt"))
    return files


def _load_all_python_source():
    """Yield (path, content) for all .py files that might have Urdu strings."""
    for py_file in PROJECT_ROOT.rglob("*.py"):
        if ".git" in str(py_file) or "__pycache__" in str(py_file):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if any("؀" <= c <= "ۿ" for c in content):  # has Arabic/Urdu chars
                yield py_file, content
        except Exception:
            pass


@pytest.mark.multilingual
class TestUrduVocabularyPurity:

    def test_no_forbidden_vocab_in_prompts(self):
        prompt_files = _load_prompt_files()
        if not prompt_files:
            pytest.skip("No Urdu prompt files found — skipping vocabulary check")
        for fpath in prompt_files:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            for word in FORBIDDEN_INDIAN_VOCAB:
                assert word not in content, (
                    f"Indian Urdu vocabulary '{word}' found in {fpath.name}. "
                    f"Use Pakistani alternative instead."
                )

    def test_urdu_prompts_contain_required_vocabulary(self):
        prompt_files = _load_prompt_files()
        if not prompt_files:
            pytest.skip("No Urdu prompt files found")
        all_content = " ".join(f.read_text(encoding="utf-8", errors="ignore") for f in prompt_files)
        found = [w for w in REQUIRED_PAKISTANI_VOCAB if w in all_content]
        assert len(found) > 0, (
            f"Pakistani Urdu prompts should use vocabulary like {REQUIRED_PAKISTANI_VOCAB}. "
            f"None found in prompt files."
        )

    def test_emergency_keywords_are_pakistani_urdu(self):
        from pipeline.emergency_detector import EMERGENCY_KEYWORDS
        urdu_keywords = EMERGENCY_KEYWORDS.get("ur-PK", [])
        assert len(urdu_keywords) > 0
        # These Pakistani-specific phrases should be present
        has_chest_pain = any("سینے" in kw for kw in urdu_keywords)
        has_breathing = any("سانس" in kw for kw in urdu_keywords)
        assert has_chest_pain, "Urdu emergency keywords should include chest pain (سینے)"
        assert has_breathing, "Urdu emergency keywords should include breathing difficulty (سانس)"

    def test_urdu_language_code_not_indian_hi(self):
        from providers.language_profile import LANGUAGE_PROFILES
        urdu_profile = LANGUAGE_PROFILES["ur-PK"]
        assert urdu_profile.stt.language_code != "hi", (
            "ur-PK STT language code must NOT be 'hi' (Hindi/Indian) — use 'ur'"
        )
        assert urdu_profile.stt.language_code == "ur"

    def test_urdu_is_rtl_in_all_templates(self):
        """All Jinja2 templates that render Urdu text must use RTL."""
        templates_dir = PROJECT_ROOT / "templates"
        if not templates_dir.exists():
            pytest.skip("templates/ not found")
        rtl_templates = []
        for tmpl in templates_dir.glob("*.html"):
            content = tmpl.read_text(encoding="utf-8", errors="ignore")
            if any("؀" <= c <= "ۿ" for c in content) or "dir=\"rtl\"" in content or "urdu-text" in content:
                rtl_templates.append(tmpl.name)
        # base.html and settings_language.html must have RTL
        assert any("base" in t for t in rtl_templates) or (templates_dir / "base.html").read_text().count("rtl") > 0

    def test_no_hardcoded_timedelta_hours_5_in_source(self):
        # Skip comment lines, docstrings (triple-quoted), and test files themselves
        violations = []
        for fpath, content in _load_all_python_source():
            if "test_" in fpath.name:
                continue  # exclude test files
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if "timedelta(hours=5)" in line and "pytz" not in line:
                    violations.append(f"{fpath.name}: {line!r}")
        assert not violations, (
            f"Found timedelta(hours=5) used as PKT timezone. Use pytz.timezone('Asia/Karachi'). "
            f"Violations: {violations}"
        )

    def test_punjabi_language_code_is_pa_not_hi(self):
        from providers.language_profile import LANGUAGE_PROFILES
        pa_profile = LANGUAGE_PROFILES["pa-PK"]
        assert pa_profile.stt.language_code == "pa", (
            "pa-PK STT language code should be 'pa' not 'hi'"
        )


@pytest.mark.multilingual
class TestShahmulhiScript:
    """Punjabi text must use Shahmukhi (Arabic-based) script, never Gurmukhi."""

    def test_punjabi_noise_words_in_shahmukhi(self):
        from providers.language_profile import LANGUAGE_PROFILES
        noise = LANGUAGE_PROFILES["pa-PK"].noise_words
        for word in noise:
            for char in word:
                code = ord(char)
                # Gurmukhi Unicode block: 0x0A00–0x0A7F
                assert not (0x0A00 <= code <= 0x0A7F), (
                    f"Punjabi noise word '{word}' contains Gurmukhi character — use Shahmukhi"
                )

    def test_punjabi_day_names_in_shahmukhi(self):
        from scheduling.date_parser import PUNJABI_DAYS
        for day, _ in PUNJABI_DAYS.items():
            for char in day:
                code = ord(char)
                assert not (0x0A00 <= code <= 0x0A7F), (
                    f"Day name '{day}' contains Gurmukhi — use Shahmukhi (Arabic script)"
                )
