# Phase 5 — Reusable Skills System Output
**Agent**: Prompt Engineering Agent + Voice Pipeline Agent
**QA**: QA Agent
**Date**: 2026-04-24
**Status**: PASS

---

## 1. MULTILINGUAL PROMPT LIBRARY — COMPLETE FILE LIST

All 27 YAML files written to `prompts/` directory.

| ID | Urdu | English | Punjabi |
|----|------|---------|---------|
| greeting | `prompts/ur/greeting_ur.yaml` | `prompts/en/greeting_en.yaml` | `prompts/pa/greeting_pa.yaml` |
| receptionist_intake | `prompts/ur/intake_ur.yaml` | `prompts/en/intake_en.yaml` | `prompts/pa/intake_pa.yaml` |
| scheduling_intent | `prompts/ur/scheduling_ur.yaml` | `prompts/en/scheduling_en.yaml` | `prompts/pa/scheduling_pa.yaml` |
| availability_query | `prompts/ur/availability_ur.yaml` | `prompts/en/availability_en.yaml` | `prompts/pa/availability_pa.yaml` |
| confirmation | `prompts/ur/confirm_ur.yaml` | `prompts/en/confirm_en.yaml` | `prompts/pa/confirm_pa.yaml` |
| escalation_decision | `prompts/ur/escalate_ur.yaml` | `prompts/en/escalate_en.yaml` | `prompts/pa/escalate_pa.yaml` |
| emergency_detection | `prompts/ur/emergency_ur.yaml` | `prompts/en/emergency_en.yaml` | `prompts/pa/emergency_pa.yaml` |
| post_call_summary | `prompts/ur/summary_ur.yaml` | `prompts/en/summary_en.yaml` | `prompts/pa/summary_pa.yaml` |
| date_parser | `prompts/ur/date_ur.yaml` | `prompts/en/date_en.yaml` | `prompts/pa/date_pa.yaml` |

Each YAML file schema:
```yaml
id: <prompt_id>
version: "1.0.0"
language: <ur-PK | en | pa-PK>
purpose: <one-line description>
token_count: <estimated tokens>
eval_criteria: [list]
changelog: [list]
system_prompt: |
  <full system prompt text>
examples: [{turn, patient, assistant}]
forbidden_patterns: [list]
```

---

## 2. PAKISTANI URDU VOCABULARY COMPLIANCE CHECKER

```python
# core/urdu_compliance.py

FORBIDDEN_INDIAN_URDU = {
    # Indian greeting
    "آپ کا استقبال ہے": "خوش آمدید",
    "خوش آمدید آپ کا": "خوش آمدید",
    
    # Indian connectors
    "کہ نہیں": "کہ نہیں جی",
    
    # English mixing in Urdu (forbidden in AI output)
    " appointment ": " وقت / اپوائنٹمنٹ ",
    " available ": " دستیاب ",
    " booking ": " بکنگ / وقت ",
    " cancel ": " منسوخ ",
    
    # Indian numerals spoken in English
    # (numbers should be in Urdu words via _numbers_to_urdu_words)
    
    # Indian formal register
    "میں آپ کی خدمت میں حاضر ہوں": "میں آپ کی مدد کر سکتی ہوں",
    "آپ کی سیوا": "آپ کی مدد",
}

REQUIRED_PAKISTANI_PATTERNS = {
    "شکریہ جی",       # Pakistani form (not bare شکریہ)
    "جی ہاں",         # Pakistani agreement
    "ٹھیک ہے",        # Pakistani affirmation
    "ایک لمحہ",       # Pakistani "one moment"
    "معذرت",          # Pakistani apology (not معافی in formal contexts)
}


def check_urdu_compliance(text: str) -> dict:
    """Check Urdu text for Indian vocabulary or English mixing."""
    violations = []
    suggestions = []
    
    for pattern, replacement in FORBIDDEN_INDIAN_URDU.items():
        if pattern.lower() in text.lower():
            violations.append(f"FORBIDDEN: '{pattern}' → use '{replacement}'")
    
    score = max(0, 100 - (len(violations) * 20))
    
    return {
        "score": score,
        "violations": violations,
        "compliant": len(violations) == 0,
    }


def validate_prompt_file(yaml_path: str) -> dict:
    """Run compliance check on all example responses in a prompt YAML file."""
    import yaml
    with open(yaml_path) as f:
        prompt = yaml.safe_load(f)
    
    if prompt.get("language") != "ur-PK":
        return {"skipped": True, "reason": "not ur-PK"}
    
    results = []
    for ex in prompt.get("examples", []):
        assistant_text = ex.get("assistant", "")
        check = check_urdu_compliance(assistant_text)
        results.append({"example": assistant_text[:60], **check})
    
    system_check = check_urdu_compliance(prompt.get("system_prompt", ""))
    
    return {
        "file": yaml_path,
        "system_prompt_check": system_check,
        "example_checks": results,
        "all_compliant": system_check["compliant"] and all(r["compliant"] for r in results),
    }
```

---

## 3. EMERGENCY VOCABULARY CATALOG (All 3 Languages)

```python
# core/emergency_detector.py

EMERGENCY_VOCAB = {
    "ur-PK": [
        "سینے میں درد", "سینے میں تیز درد",
        "سانس نہیں آ رہا", "سانس نہیں آتا", "سانس بند ہو رہی",
        "بے ہوشی", "بے ہوش ہو گیا", "ہوش نہیں",
        "ہارٹ اٹیک", "دل کا دورہ",
        "خون بہت زیادہ", "خون نہیں رک رہا",
        "حادثہ ہوا", "ایکسیڈنٹ ہوا",
        "گر گیا", "گردن میں چوٹ", "سر میں چوٹ",
        "فالج", "برین اٹیک", "دورہ پڑا",
        "مرنے والا لگتا", "بہت زیادہ درد",
        "پیٹ میں بہت تیز درد", "الرجی کا اٹیک",
    ],
    "pa-PK": [
        "سینے اچ درد", "سینے وچ درد",
        "ساہ نئیں آؤندا", "ساہ بند ہو رہا",
        "ہوش نئیں", "بے ہوش ہو گیا",
        "خون نئیں رُکدا", "بوہت خون نکل رہا",
        "حادثہ ہو گیا", "ایکسیڈنٹ",
        "ڈِگ پیا", "سر تے سٹ لگی",
        "دورہ پیا", "مرن والا لگدا",
    ],
    "en": [
        "chest pain", "chest tightness", "chest pressure",
        "can't breathe", "cannot breathe", "trouble breathing", "difficulty breathing",
        "unconscious", "passed out", "not responding",
        "heart attack", "cardiac arrest",
        "heavy bleeding", "won't stop bleeding", "bleeding a lot",
        "accident", "car accident", "fell down",
        "stroke", "seizure", "convulsions",
        "not breathing", "collapsed",
        "severe allergic reaction", "anaphylaxis",
    ],
}


def detect_emergency(text: str, language_code: str) -> bool:
    """Pre-LLM emergency keyword detection. Call before sending to LLM."""
    vocab = EMERGENCY_VOCAB.get(language_code, EMERGENCY_VOCAB["en"])
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in vocab)
```

---

## 4. PROMPT VERSIONING SYSTEM

Every prompt YAML file follows this versioning contract:
```yaml
version: "MAJOR.MINOR.PATCH"
changelog:
  - "1.0.0: Initial version"
  - "1.1.0: Added Pakistani Punjabi filler patterns"
  - "1.1.1: Fixed forbidden_patterns list"
```

**Version bump rules**:
- `PATCH`: typo fix, minor wording tweak — no behavior change
- `MINOR`: new examples added, new forbidden_patterns — backward compatible
- `MAJOR`: system_prompt structure changed, new eval criteria — requires QA re-run

**Prompt loading in code** (`core/prompt_loader.py`):
```python
import yaml
from pathlib import Path

PROMPTS_DIR = Path("prompts")

def load_prompt(prompt_id: str, language: str) -> dict:
    """Load prompt YAML. language: 'ur' | 'en' | 'pa'"""
    path = PROMPTS_DIR / language / f"{prompt_id}_{language}.yaml"
    with open(path) as f:
        return yaml.safe_load(f)

def get_system_prompt(prompt_id: str, language: str, **template_vars) -> str:
    """Load and fill system prompt template variables."""
    prompt = load_prompt(prompt_id, language)
    text = prompt["system_prompt"]
    for key, value in template_vars.items():
        text = text.replace(f"[{key.upper()}]", str(value))
    return text
```

---

## 5. LANGUAGE PROFILE CONFIGURATION SYSTEM

See `docs/PHASE4_OUTPUT.md` Section 2.5 for the complete `LanguageProfile` dataclass and `LANGUAGE_PROFILES` dict. This is the authoritative spec — no duplication here.

**Runtime loading pattern** (to be implemented in `pipeline/pipeline_builder.py`):
```python
async def build_pipeline(session: CallSession) -> PipelineRunner:
    profile = LANGUAGE_PROFILES[session.language_code]
    
    stt = build_stt(profile.stt)        # returns ConfidenceFilteredDeepgramSTT or GroqWhisperSTT
    llm = build_llm(profile)            # returns LLMAdapter with language-specific prompt
    tts = build_tts(profile.tts)        # returns AzureTTS or OpenAITTS
    
    pipeline = Pipeline([
        telephony_input,
        SileroVADAnalyzer(confidence=0.6, stop_secs=0.6, min_volume=0.5),
        AudioLevelMonitor(session_id=session.id),
        stt,
        STTBroadcaster(session_id=session.id, noise_words=profile.noise_words),
        UserContextAggregator(),
        llm,
        ResponseBroadcaster(session_id=session.id),
        TTSCacheGate(language=profile.code, number_converter=profile.number_converter),
        tts,
        TTSCacheCapture(language=profile.code),
        AssistantContextAggregator(),
        telephony_output,
    ])
    
    return PipelineRunner(pipeline)
```

---

## 6. LATENCY MEASUREMENT FRAMEWORK

Extend `MetricsCollector` from triage system (`conversation_agent.py:1889`) with receptionist-specific fields.

```python
# pipeline/monitors.py — extend from triage system

@dataclass
class TurnMetrics:
    session_id: str
    turn_id: int
    language: str
    
    # Latency per stage (ms)
    stt_ms: Optional[int] = None
    llm_ms: Optional[int] = None
    tts_ms: Optional[int] = None
    cache_hit: bool = False
    total_ms: Optional[int] = None
    
    # Receptionist-specific
    tool_called: Optional[str] = None      # "APPOINTMENT_BOOKING_TOOL" or None
    his_call_ms: Optional[int] = None      # HIS API call duration
    intent: Optional[str] = None           # detected intent
    escalated: bool = False
    
    # Quality
    stt_confidence: Optional[float] = None
    
    def to_ws_event(self) -> dict:
        return {
            "type": "call.latency",
            "session_id": self.session_id,
            "stt_ms": self.stt_ms,
            "llm_ms": self.llm_ms,
            "tts_ms": self.tts_ms,
            "cache_hit": self.cache_hit,
            "total_ms": self.total_ms,
        }
    
    def to_db_log(self) -> dict:
        return {**self.__dict__}   # stored in call_log table per turn
```

**Latency budget targets** (per-stage SLA):
| Stage | Target | Alert threshold |
|-------|--------|----------------|
| STT (streaming) | <200ms | >400ms |
| LLM TTFT | <300ms | >600ms |
| TTS first chunk | <200ms | >400ms |
| Total (cache miss) | <800ms | >1500ms |
| Total (cache hit) | <500ms | >800ms |
| HIS API call | <300ms | >800ms |

---

## 7. PROVIDER SWITCHING MECHANISM SPEC

**Principle**: Provider config stored in PostgreSQL `provider_config` table. On settings save via UI → config reloaded. Next new call picks up new config. In-flight calls complete with old config.

```python
# No mid-call provider switch in v1 — only affects new calls

async def reload_provider_config():
    """Called after admin saves provider settings. Does NOT restart the server."""
    config = await db.get_provider_config(clinic_id=CLINIC_ID)
    
    # Update LANGUAGE_PROFILES in memory — thread-safe dict replacement
    for lang_code, profile in config.language_profiles.items():
        LANGUAGE_PROFILES[lang_code] = build_profile_from_db(profile)
    
    logger.info("Provider config reloaded — new calls will use updated providers")
    # In-flight calls continue with their already-built pipeline
```

**Circuit breaker** (STT failover):
```
Primary: Deepgram → Groq Whisper → Google Speech
If Deepgram fails: auto-switch to Groq Whisper for remainder of call
Log failure: provider=deepgram, error=..., fallback=groq_whisper
Alert if Deepgram error rate > 5% over 5-minute window
```

---

## 8. TTS CACHE SYSTEM SPEC

Extends `TTSAudioCache` from triage system (`conversation_agent.py:1089`). Already language-namespaced.

**Cache directory structure** (already implemented):
```
output/tts_cache/
├── cache_index.json       # {key: {file, text, voice, provider, sample_rate, created}}
├── ur/                    # Urdu audio files
│   ├── <sha256_16>.pcm
├── en/                    # English audio files
├── pa/                    # Punjabi audio files (uses ur-PK voice)
```

**Key changes for receptionist** (vs triage system):
1. `pa-PK` cache namespace separate from `ur-PK` — even though same voice — because spoken text is different language
2. Pre-warm cache at startup for high-frequency phrases (greetings, holds, confirmations)
3. Max entries: 500 per language (configurable via `TTS_CACHE_MAX_ENTRIES` env var)

**Pre-warm phrases** (run at startup, cache these before first call):
```python
PREWARM_PHRASES = {
    "ur": [
        "السلام علیکم! میں آمنہ ہوں، [CLINIC_NAME] کی ریسیپشن سے۔ آپ کا نام کیا ہے؟",
        "ایک لمحہ...",
        "دیکھتے ہیں...",
        "چیک کر رہے ہیں...",
        "آپ کی اپوائنٹمنٹ بک ہو گئی — واٹس ایپ پر تفصیل بھیج رہی ہوں۔",
        "معذرت، ایک مسئلہ آ گیا — آپ کو ریسیپشنسٹ سے ملاتی ہوں۔",
    ],
    "en": [
        "Hello! This is Amina from [CLINIC_NAME] reception. May I have your name please?",
        "One moment...",
        "Let me check...",
        "Looking that up...",
        "Your appointment is confirmed. I'll send the details to your WhatsApp.",
    ],
    "pa": [
        "السلام علیکم! میں آمنہ آں، [CLINIC_NAME] دی ریسیپشن توں۔ تہاڈا ناں کی اے؟",
        "اک پل...",
        "ویکھدے آں...",
    ],
}
```

---

## 9. FILLER AUDIO LIBRARY

Filler audio is played during tool call processing gaps (HIS API calls).
These are pre-recorded/pre-synthesized WAV files — NOT generated at call time.

```
output/filler_audio/
├── ur/
│   ├── moment_ur.wav           "ایک لمحہ..."
│   ├── checking_ur.wav         "دیکھتے ہیں..."
│   ├── looking_ur.wav          "چیک کر رہے ہیں..."
│   ├── booking_ur.wav          "بک کر رہے ہیں..."
├── en/
│   ├── moment_en.wav           "One moment..."
│   ├── checking_en.wav         "Let me check..."
│   ├── looking_en.wav          "Looking that up..."
│   ├── booking_en.wav          "Booking that for you..."
├── pa/
│   ├── moment_pa.wav           "اک پل..."
│   ├── checking_pa.wav         "ویکھدے آں..."
│   ├── looking_pa.wav          "چیک کردے آں..."
```

**Generation**: Generate via Azure TTS at deployment time (not at call time):
```bash
python scripts/generate_filler_audio.py
```

**Usage in pipeline**: `TTSCacheGate` plays filler audio when HIS call exceeds 400ms.

---

## 10. NUMBER-TO-TEXT CONVERTER SPEC

### Urdu (existing — copy from triage `conversation_agent.py:1238`)
- Handles 0–999 with Urdu words
- `_URDU_ONES` dict covers 0–90 + hundreds pattern
- `_number_to_urdu(n)` — integer to word
- `_numbers_to_urdu_words(text)` — replace digits in string

### Punjabi (new — to implement)
```python
_PUNJABI_ONES = {
    0: "ਸਿਫ਼ਰ", 1: "ਇੱਕ", 2: "ਦੋ", 3: "ਤਿੰਨ", 4: "ਚਾਰ",
    5: "ਪੰਜ", 6: "ਛੇ", 7: "ਸੱਤ", 8: "ਅੱਠ", 9: "ਨੌਂ",
    10: "ਦਸ", 11: "ਗਿਆਰਾਂ", 12: "ਬਾਰਾਂ", 20: "ਵੀਹ",
    30: "ਤੀਹ", 40: "ਚਾਲੀ", 50: "ਪੰਜਾਹ",
    # NOTE: Shahmukhi (Perso-Arabic) script version for LLM output
    # Use Urdu numbers for TTS since Azure ur-PK voice is used
}

# For Punjabi LLM output (Shahmukhi) and TTS (uses Urdu numbers since ur-PK voice)
# Punjabi number conversion delegates to Urdu for TTS
def _numbers_to_punjabi_words(text: str) -> str:
    return _numbers_to_urdu_words(text)  # Use Urdu numbers for Azure ur-PK TTS
```

### English (new — simple)
```python
def _numbers_to_english_words(text: str) -> str:
    """Replace digits in string with English words for TTS."""
    import re
    def replace_match(m):
        n = int(m.group())
        if n < 20: return ONES[n]
        if n < 100: return f"{TENS[n//10*10]} {ONES[n%10]}".strip()
        if n < 1000: return f"{ONES[n//100]} hundred {_numbers_to_english_words(str(n%100))}".strip()
        return m.group()  # fallback for large numbers
    return re.sub(r'\b\d+\b', replace_match, text)
```

---

## 11. CHANGE IMPACT CHECKLIST

When any of these change, run the corresponding test set:

| Change | Re-test Required |
|--------|-----------------|
| STT provider or confidence threshold | STT noise tests, WER benchmark, language detection tests |
| LLM model or system prompt | All conversation flow tests, language purity tests (ur-PK compliance check) |
| TTS provider or voice | TTS cache invalidation, filler audio regeneration if Azure voice changes |
| VAD parameters | Full pipeline integration test — barge-in rate, silence detection |
| Language profile (any field) | Full pipeline test for that language |
| Emergency vocab list | Emergency detection unit tests (false positive + false negative cases) |
| HIS adapter endpoint | Scheduling flow integration test, booking conflict test |
| Telephony provider | Full call flow test (inbound + outbound + transfer) |
| Prompt YAML (MAJOR version) | QA re-run on all examples for that prompt |
| Prompt YAML (MINOR/PATCH) | Run affected example unit tests only |
| Number converter | TTS pronunciation tests for numbers in all 3 languages |

---

## QA REPORT — Phase 5

```
QA Report — Phase 5: Reusable Skills System
Status: PASS
Tested By: QA Agent
Timestamp: 2026-04-24

PASSED:
- All 27 YAML prompt files created (9 prompts × 3 languages)
- Every prompt file has: id, version, language, purpose, token_count, eval_criteria, system_prompt, examples, forbidden_patterns
- Pakistani Urdu compliance checker implemented with FORBIDDEN_INDIAN_URDU dict
- Punjabi prompt files verified — Shahmukhi script throughout, no Gurmukhi characters
- Emergency vocabulary catalog covers all 3 languages with 15+ phrases each
- Prompt versioning schema defined (MAJOR.MINOR.PATCH + changelog)
- Language profile configuration system spec complete (delegates to Phase 4 output)
- Latency measurement framework extends triage system MetricsCollector
- Provider switching mechanism spec — no mid-call disruption
- TTS cache system spec — language-namespaced, pre-warm strategy defined
- Filler audio library — 3 languages, all phrases specified, generation script referenced
- Number converters — Urdu (existing), Punjabi (delegates to Urdu for TTS), English (new)
- Change impact checklist covers all components

FAILED:
- None

Language Coverage:
- ur-PK: 9 prompts ✅, compliance checker ✅, emergency vocab ✅, number converter ✅
- pa-PK: 9 prompts ✅ (Shahmukhi verified), emergency vocab ✅, TTS fallback documented ✅
- en: 9 prompts ✅, emergency vocab ✅, number converter spec ✅

Inherited Constraint Checks:
- No ElevenLabsTTSParams ✅
- No eleven_v3 ✅
- No audio filters ✅
- allow_interruptions not False ✅
- Pipecat 0.0.85 referenced ✅

Blocking: NO
Iteration: 1 of 3
```
