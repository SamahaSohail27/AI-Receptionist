# AI Medical Receptionist — Master Build Tracker
**Project**: Production-Grade AI Receptionist for Hospitals (Pakistan-focused)
**Backend**: Python (FastAPI + Pipecat 0.0.85)
**Frontend**: Tailwind CSS + Alpine.js (Jinja2 templates)
**Languages**: Pakistani Urdu (ur-PK) | Punjabi | English
**Deploy**: Local first → Cloud (AWS/GCP)
**Status**: Phase 10 ✅ — BUILD COMPLETE (CONDITIONAL GO — 3/5 pre-prod blockers resolved; 2 need human action)
**Last Updated**: 2026-04-25

---

## INHERITED KNOWLEDGE (From AI-Clinical-Triage-System — READ FIRST)

Every agent MUST read this entire section before making any decision. These are proven facts earned through 4 build sessions — not assumptions.

### What WORKS — Do Not Change
| Thing | Proven Value | Why |
|-------|-------------|-----|
| Pipecat version | `pipecat-ai==0.0.85` | Newer versions have breaking API changes |
| Silero VAD | `confidence=0.6, stop_secs=0.6, min_volume=0.5` | Urdu-friendly, tuned over 4 sessions |
| Deepgram STT | `endpointing=600ms, utterance_end_ms=1500, no_delay=true` | Best results for Urdu |
| STT confidence threshold | `0.45` | Lower catches valid Urdu; higher drops it — DO NOT raise |
| `allow_interruptions` | `true` | `false` causes buffered audio flooding |
| All UI broadcasts | Fire-and-forget (non-blocking) | Prevents pipeline lag |
| `_persist()` | Thread pool | Non-blocking DB writes |
| TTS cache | Disk-backed LRU | Greetings/confirmations play instantly |
| Audio filters | NONE | All tried options crash or distort (see failures below) |
| Noise word filter | 3-layer approach: VAD + confidence + text filter | No audio processing needed |
| ElevenLabs model | `eleven_flash_v2_5` only | v3 returns HTTP 403 in pipecat WS |

### What FAILED — Never Repeat
| What | Why It Failed |
|------|--------------|
| `NoisereduceFilter` | OMP library conflict → audio distortion |
| `FacebookDenoiserFilter` | Crashes pipecat pipeline (BaseAudioFilter incompatibility) |
| `KrispFilter` | Requires paid SDK (sales approval needed) |
| `AICFilter` (ai-coustics) | Requires paid license |
| `allow_interruptions=False` | Buffers audio during bot speech → double-transcription flooding |
| VAD `confidence=0.80` | Too strict for Urdu — drops valid speech |
| STT `confidence=0.70` | Drops valid Urdu (0.5–0.7 is normal range for Urdu speech) |
| ElevenLabs `eleven_v3` model | NOT supported by pipecat WebSocket API (HTTP 403) |
| `ElevenLabsTTSParams` class | Does not exist in pipecat 0.0.85 → ImportError |
| "persistent cough" alias of "cough" | Cross-contamination, inflates unrelated department scores |
| "fatigue" alias of "weakness" | Inflates 4 extra departments |

### Pakistani Urdu — Proven Specifics
- STT confidence 0.45–0.70 is **normal** for Pakistani Urdu — never treat as noise
- Noise word filter MUST include Urdu fillers: `آہ, ہاں, اچھا, جی, ٹھیک ہے`
- Numbers must convert to Urdu before TTS: `302 → تین سو دو`
- Roman Urdu aliases are mandatory for STT normalization: `dard = درد, bukhaar = بخار`
- 173+ aliases required covering: pure Urdu, Roman Urdu, English, common STT mistranscriptions
- **Pakistani Urdu ≠ Indian Urdu**: vocabulary, pronunciation, and loanwords differ significantly
- Always use locale code `ur-PK` (Pakistani) not `ur-IN` (Indian) in STT/TTS providers that support it
- ElevenLabs: free tier is blocked for Pakistan IPs — Creator key (`sk_4...`) required
- Azure Neural TTS: `ur-PK-AsadNeural` (male) and `ur-PK-UzmaNeural` (female) are the most authentic Pakistani Urdu voices available

### Punjabi — Important Limitations (Must Read)
- **Script divide**: Pakistani Punjabi = Shahmukhi (Perso-Arabic script) | Indian Punjabi = Gurmukhi
- Most commercial STT/TTS engines support Gurmukhi (`pa-IN`) only — NOT Shahmukhi
- **Azure, Google Speech, AssemblyAI**: only `pa-IN` (Indian Punjabi/Gurmukhi) — NOT usable for Pakistani Punjabi
- **Best option for Pakistani Punjabi STT**: OpenAI Whisper (`whisper-large-v3` via Groq for speed) — multilingual model handles spoken Pakistani Punjabi reasonably well
- **Best option for Pakistani Punjabi TTS**: No dedicated provider exists. Options:
  - Use Azure `ur-PK` voices (Pakistani Punjabi speakers understand Urdu TTS)
  - ElevenLabs multilingual v2 (partial Punjabi support, may have Indian accent)
- **Practical strategy**: For Punjabi calls, use Whisper STT (detects Punjabi speech) + Urdu TTS response (clinically acceptable — patients understand Urdu reply even if they spoke Punjabi)
- Punjabi support should be flagged clearly as "Phase 2 capability" — basic in v1, improved in v2

---

## VOICE PIPELINE OPTIMIZATION CHECKLIST (Universal — Apply to Every Voice Project)

Extracted from AI-Clinical-Triage-System audit. These apply to ALL voice AI projects — enforce during Phase 8 implementation. Items already covered in other sections (filler audio, TTS cache, streaming, confidence thresholds) are not repeated here.

### Pipeline Architecture
| # | Rule | Impact | Status |
|---|------|--------|--------|
| V1 | `spoken_text` must be **first field** in every tool schema JSON | ~500ms free — TTS streams before rest of JSON generates | Implement in Phase 8.1 |
| V2 | Use `tool_choice="auto"` not `"required"` | 300–700ms saved — skips tool call overhead on greeting/simple turns | Implement in Phase 8.4 |
| V3 | TTS bypass for known text — if tool handler sets response before LLM finishes, push directly to TTS, skip buffering gate | 1–2.5s saved per tool-call turn | Implement in Phase 8.4 |

### LLM
| # | Rule | Impact | Status |
|---|------|--------|--------|
| V4 | **Prompt caching** — Anthropic: `enable_prompt_caching=True`. OpenAI: automatic on identical prefixes ≥1024 tokens. Always enable, zero code cost for OpenAI | ~150ms saved on cached turns | Implement in Phase 8.1 |
| V5 | **3-layer context management**: Layer 1 = structured state as flat key-value block (~80 tokens, never grows). Layer 2 = rolling summary of turns older than last 4 (~80 tokens vs ~900). Layer 3 = always keep last 4 raw turns verbatim. Token count stays flat forever | 200–500ms LLM speedup + prevents context window exhaustion | Implement in Phase 8.4 |
| V6 | **Model tiering** — use fast/cheap model (gpt-4o-mini, Haiku) for standard turns; escalate to Sonnet/GPT-4o only when booking confirmation fails, LLM confidence low, or complex intent | 200–600ms saved per standard turn | Implement in Phase 8.1 |
| V7 | **Split system prompt into tiers** — static base (cacheable) + dynamic injection (per-turn: patient state, available slots, active doctor). Never inject full doctors list when only 1–2 candidates needed | Token reduction + cache hit rate increase | Implement in Phase 5/8.4 |

### STT
| # | Rule | Impact | Status |
|---|------|--------|--------|
| V8 | **WebSocket STT over HTTP** — always use streaming WebSocket (Deepgram) not HTTP request-response (OpenAI Whisper REST). ~4× faster: ~150ms vs ~1000ms | 500–900ms saved | Implement in Phase 8.1 |
| V9 | **STT circuit breaker** — Deepgram → Groq Whisper → Google. Never drop a turn; if STT fails twice, use regex/LLM fallback extraction | Reliability | Implement in Phase 8.4 |

### Infrastructure
| # | Rule | Impact | Status |
|---|------|--------|--------|
| V10 | **Offload all file/DB writes to thread executor** — `await loop.run_in_executor(None, write_fn)` for any synchronous I/O (logs, analytics writes). Synchronous writes block async event loop | 50–300ms saved per turn | Implement in Phase 8.2 |
| V11 | **Deploy in same region as STT/TTS providers** — Deepgram East US, Azure East US, OpenAI US. Cross-region adds 100–300ms RTT per API call | Network RTT elimination | Implement in Phase 10 |

---

## TECH STACK (Pre-Decided)

| Layer | Technology | Version | Rationale |
|-------|-----------|---------|-----------|
| Backend framework | FastAPI | Latest stable | Proven in existing system |
| Voice pipeline | Pipecat | `0.0.85` | Already working — DO NOT upgrade |
| Database | PostgreSQL | 15+ | Production-grade relational |
| ORM | SQLAlchemy | 2.x | Async support, migration-ready |
| Migrations | Alembic | Latest | Versioned, rollback-safe |
| Task queue | Celery + Redis | Latest | Async jobs: reminders, analytics |
| Auth | JWT + RBAC | python-jose | Clinic admin / doctor / system |
| UI framework | Tailwind CSS + Alpine.js | CDN for v1 | No build step, reactive, professional |
| UI charts | Chart.js | Latest | Free, rich analytics visuals |
| UI calendar | FullCalendar.js | Latest | Full scheduling calendar |
| UI icons | Heroicons | Latest | Clean, medical-appropriate |
| Template engine | Jinja2 (FastAPI) | Built-in | No separate frontend server needed |
| Environment | Conda (`ai-receptionist`) | Python 3.10 | Consistent with existing pattern |
| Local ports | 8000 (API) + 8001 (WebSocket) | — | Avoid conflict with triage system (7871) |
| Config | `.env` + python-dotenv | — | Match existing pattern |
| Containerization | Docker Compose (local) | Latest | Reproducible dev environment |

---

## LANGUAGE & LOCALIZATION ARCHITECTURE

### Supported Languages
| Language | Code | Script | STT Primary | TTS Primary | Status |
|----------|------|--------|------------|------------|--------|
| Pakistani Urdu | `ur-PK` | Nastaliq (Perso-Arabic) | Deepgram `ur` / Azure `ur-PK` | Azure `ur-PK-UzmaNeural` | Full support |
| English | `en` | Latin | Deepgram `en-US` | OpenAI tts-1 | Full support |
| Punjabi | `pa-PK` | Shahmukhi (Perso-Arabic) | Groq Whisper (multilingual) | Azure `ur-PK` (fallback) | Partial support |

### Language Selection Architecture
```
Method 1 — DTMF at call start:
  Patient calls → "Press 1 for Urdu, 2 for Punjabi, 3 for English" (pre-recorded audio)
  Patient presses → pipeline loads language-specific provider config

Method 2 — UI Config per clinic:
  Admin sets default language in clinic settings
  Can override per-doctor or per-phone-number

Method 3 — Auto-detect (Optional, Phase 2):
  First 3-5 seconds → Whisper multilingual → detect language → load correct pipeline config
```

### Language-Specific Pipeline Configuration
Each language has a named config profile that loads:
- STT provider + language code + confidence threshold
- LLM system prompt variant (language-specific persona + instructions)
- TTS provider + voice + language code
- Noise word filter list (language-specific filler words)
- Number-to-text converter (Urdu: تین سو دو | Punjabi: ਤਿੰਨ ਸੌ ਦੋ | English: three hundred two)
- Date/time format (Urdu: اتوار/پیر | Punjabi: ਐਤਵਾਰ | English: Monday)

```python
LANGUAGE_PROFILES = {
    "ur-PK": {
        "stt": {"provider": "deepgram", "language": "ur", "confidence_threshold": 0.45},
        "llm": {"prompt_variant": "urdu", "output_language": "pure_urdu_pk"},
        "tts": {"provider": "azure", "voice": "ur-PK-UzmaNeural"},
        "noise_words": ["آہ", "ہاں", "اچھا", "جی", "ٹھیک ہے", "ام"],
        "number_converter": "urdu_numbers",
    },
    "en": {
        "stt": {"provider": "deepgram", "language": "en-US", "confidence_threshold": 0.70},
        "llm": {"prompt_variant": "english", "output_language": "english"},
        "tts": {"provider": "openai", "voice": "nova"},
        "noise_words": ["um", "uh", "hmm", "like", "you know"],
        "number_converter": "english_numbers",
    },
    "pa-PK": {
        "stt": {"provider": "groq_whisper", "language": "pa", "confidence_threshold": 0.40},
        "llm": {"prompt_variant": "punjabi", "output_language": "punjabi_shahmukhi"},
        "tts": {"provider": "azure", "voice": "ur-PK-UzmaNeural"},  # Urdu TTS fallback
        "noise_words": ["ਓ", "ਹਾਂ", "ਠੀਕ ਹੈ"],
        "number_converter": "punjabi_numbers",
        "tts_fallback_note": "Dedicated Pakistani Punjabi TTS not available — using ur-PK voice",
    }
}
```

### Language Isolation Rules
- Pakistani Urdu responses MUST use Pakistani vocabulary and idioms (not Indian Urdu)
- Forbidden in Pakistani Urdu output: `آپ کا استقبال ہے` (Indian) → use `خوش آمدید` (Pakistani)
- Forbidden: mixing English words in Urdu output (no "appointment لے لیں" → use "وقت بک کروائیں")
- Punjabi LLM output should use Shahmukhi script where possible
- English output: neutral professional, not regional accent-specific
- System prompt for each language must explicitly state script, vocabulary standard, and forbidden mixing

---

## PROVIDER OPTIONS MATRIX

### Telephony — Pakistan Cost Analysis
| Provider | +92 Numbers | Inbound/min | Outbound/min | Monthly Fee | Recommendation |
|----------|------------|------------|-------------|------------|----------------|
| **Plivo** | ✅ Available | ~$0.0085 | ~$0.013 | $0 | **PRIMARY — cheapest for PK** |
| **Telnyx** | ✅ Available | ~$0.009 | ~$0.012 | $0 | **SECONDARY — competitive** |
| **Twilio** | ✅ Available | ~$0.0085 | ~$0.022 | $1/number/mo | **FALLBACK — most reliable** |
| **Vonage** | ✅ Limited PK | ~$0.015 | ~$0.025 | $0 | Optional |
| **SignalWire** | Partial | ~$0.008 | ~$0.010 | $0 | Budget alternative |

> **Decision**: Plivo default. Twilio fallback. UI lets operator switch. All use same SIP/WebSocket interface.

### STT — Language Support Matrix
| Provider | Model | ur-PK | Punjabi | English | Latency | Cost/hr | Notes |
|----------|-------|-------|---------|---------|---------|---------|-------|
| **Deepgram Nova-2** | Nova-2 | ✅ `ur` (proven 0.45) | ❌ | ✅ | ~150ms | ~$0.59 | **DEFAULT Urdu+English** |
| **Groq Whisper** | whisper-large-v3 | ✅ Good | ✅ Best for Pa-PK | ✅ | ~80ms | ~$0.111 | **DEFAULT Punjabi + fastest** |
| **OpenAI Whisper** | whisper-1 | ✅ Good | ✅ Partial | ✅ | ~300ms | ~$0.36 | Cheaper, slower |
| **Azure Speech** | Fast/Accurate | ✅ `ur-PK` explicit | ❌ Gurmukhi only | ✅ | ~180ms | ~$1.00 | Best ur-PK accuracy |
| **Google Speech** | Latest | ✅ `ur-PK` | ❌ Gurmukhi only | ✅ | ~200ms | ~$0.72 | Enterprise option |
| **AssemblyAI** | Nano/Best | ⚠️ Limited | ❌ | ✅ | ~200ms | ~$0.65 | English backup only |

> **Pakistani Urdu default**: Deepgram `ur` (proven, 0.45 threshold)
> **Punjabi default**: Groq Whisper (only option with Pakistani Punjabi Shahmukhi)
> **English default**: Deepgram `en-US`

### LLM Options
| Provider | Model | Speed | Cost/1M tokens | Urdu Quality | Notes |
|----------|-------|-------|---------------|-------------|-------|
| **OpenAI** | gpt-4o-mini | Fast | $0.15 in / $0.60 out | Good | **DEFAULT — proven** |
| **OpenAI** | gpt-4o | Medium | $2.50 in / $10.00 out | Excellent | Higher quality |
| **Anthropic** | claude-haiku-4-5 | Fast | $0.80 in / $4.00 out | Good | Best voice conciseness |
| **Anthropic** | claude-sonnet-4-6 | Medium | $3.00 in / $15.00 out | Excellent | Best reasoning |
| **Groq** | llama-3.3-70b | Very Fast | $0.59 in / $0.79 out | Limited | Ultra-low latency |
| **Google** | gemini-2.0-flash | Fast | $0.10 in / $0.40 out | Good | Cheapest multilingual |
| **Mistral** | mistral-small | Fast | $0.20 in / $0.60 out | Limited | European data residency |

> **Default**: gpt-4o-mini (proven). Prompt caching enabled for all. Language-specific system prompts loaded per call.

### TTS — Language Support Matrix
| Provider | Model | ur-PK | Punjabi | English | Latency | Cost | Notes |
|----------|-------|-------|---------|---------|---------|------|-------|
| **Azure Neural** | ur-PK-UzmaNeural / ur-PK-AsadNeural | ✅ **BEST** | ❌ | ✅ | ~180ms | $16/1M chars | **DEFAULT for Urdu+Punjabi** |
| **OpenAI** | tts-1 / tts-1-hd | ❌ | ❌ | ✅ | ~200ms | $15/1M chars | **DEFAULT English only** |
| **ElevenLabs** | eleven_flash_v2_5 | ⚠️ Partial | ❌ | ✅ | ~150ms | $0.22/1K chars | Creator key needed from PK |
| **Google Cloud** | Neural2/WaveNet | ✅ `ur-PK` | ❌ | ✅ | ~200ms | $16/1M chars | Reliable fallback |
| **Cartesia** | Sonic | ❌ | ❌ | ✅ | ~100ms | $0.065/1K chars | Fastest English |
| **Deepgram Aura** | Aura-2 | ❌ | ❌ | ✅ | ~120ms | $0.015/1K chars | Cheapest English |

> **Urdu/Punjabi default**: Azure `ur-PK-UzmaNeural` (female) or `ur-PK-AsadNeural` (male) — authentic Pakistani Urdu
> **English default**: OpenAI tts-1 (proven)
> **NEVER use**: `eleven_v3` (HTTP 403), `ElevenLabsTTSParams` (ImportError in pipecat 0.0.85)

---

## UI DESIGN SYSTEM

### Design Philosophy
This is a **clinical product** used daily by hospital receptionists, doctors, and administrators. The UI must be:
- **Professional**: Medical-grade aesthetics — clean, structured, trustworthy
- **Fast**: Every action within 1-2 clicks from any screen
- **Informative**: Real-time data visible without navigation
- **Accessible**: WCAG 2.1 AA minimum, works on tablet (clinic front desk)
- **Multilingual-aware**: UI itself in English; content (patient names, transcripts) in Urdu/Punjabi rendered correctly (RTL support for Urdu/Punjabi text blocks)

### Design Language
| Element | Specification |
|---------|--------------|
| Primary color | Medical blue `#0EA5E9` (sky-500) |
| Accent color | Teal `#14B8A6` (teal-500) |
| Success | Green `#22C55E` (green-500) |
| Warning | Amber `#F59E0B` (amber-500) |
| Danger / Emergency | Red `#EF4444` (red-500) |
| Background (dark) | Navy `#0F172A` (slate-900) |
| Background (light) | `#F8FAFC` (slate-50) |
| Card (dark) | `#1E293B` (slate-800) |
| Card (light) | `#FFFFFF` |
| Font family | Inter (imported via Google Fonts) |
| Border radius | `rounded-xl` (12px) — modern, not sharp |
| Shadow | `shadow-lg` on cards, `shadow-xl` on modals |
| Sidebar width | 256px expanded / 72px collapsed |
| Header height | 64px fixed |

### Dark / Light Mode
- Default: **Dark mode** (medical monitors typically dark)
- Toggle in header (moon/sun icon)
- Preference stored in `localStorage`
- All components designed for both modes — no hardcoded colors

### Page Inventory — Full List
| Page | Route | Role Access | Purpose |
|------|-------|------------|---------|
| Login | `/login` | All | Clinic login with logo + branding |
| Dashboard | `/` | Admin, Doctor | Executive overview — live stats + activity |
| Live Call Monitor | `/calls/live` | Admin, Receptionist | Real-time active call management |
| Call History | `/calls/history` | Admin, Doctor, Receptionist | Searchable call logs with transcripts |
| Appointments | `/appointments` | Admin, Doctor, Receptionist | Calendar + list view, quick book |
| Patients | `/patients` | Admin, Doctor | Patient records, call history |
| Doctors | `/doctors` | Admin | Doctor profiles, schedules |
| Availability Setup | `/doctors/:id/availability` | Admin, Doctor | Set working hours, block offs, holidays |
| Analytics | `/analytics` | Admin | Charts: volume, cost, language distribution, provider performance |
| Provider Settings | `/settings/providers` | Admin | STT/LLM/TTS/Telephony selector with live pricing |
| Language Settings | `/settings/language` | Admin | Default language, per-doctor language config |
| Clinic Settings | `/settings/clinic` | Admin | Hospital name, branding, business hours |
| User Management | `/settings/users` | Admin | Create/manage staff accounts |
| Notifications | `/notifications` | All | Reminders sent, system alerts, failed calls |
| System Health | `/settings/health` | Admin | Provider status, pipeline health, API key validity |

### Dashboard Page — Component Breakdown
```
┌─────────────────────────────────────────────────────────────────────┐
│ HEADER: [Clinic Logo] [Clinic Name]      [🔔 Alerts] [👤 User] [🌙] │
├──────────┬──────────────────────────────────────────────────────────┤
│          │  STAT CARDS ROW                                          │
│ SIDEBAR  │  [Active Calls: 3] [Today's Appts: 24] [Booked: 18]     │
│          │  [Escalations: 1]  [Cost Today: $2.40] [System: ✅]     │
│ Dashboard│  ─────────────────────────────────────────────────────  │
│ Live     │  LIVE CALLS PANEL (left)   UPCOMING APPTS (right)       │
│ Calls    │  ┌─────────────────┐       ┌────────────────────────┐   │
│ Appts    │  │ 📞 Call #1      │       │ 09:00 Dr. Ahmed - Ali  │   │
│ Patients │  │ Urdu | 1:23     │       │ 09:20 Dr. Fatima - Zara│   │
│ Doctors  │  │ State: Booking  │       │ 09:40 Dr. Khan - Usman │   │
│ Analytics│  │ [View] [Xfer]   │       │ [View All]             │   │
│ Settings │  └─────────────────┘       └────────────────────────┘   │
│          │  ─────────────────────────────────────────────────────  │
│ [Collapse│  CALL VOLUME CHART (7 days)   LANGUAGE DISTRIBUTION     │
│  ←]      │  [Line chart - Chart.js]      [Pie: Urdu/Punjabi/Eng]   │
└──────────┴──────────────────────────────────────────────────────────┘
```

### Live Call Monitor Page — Component Breakdown
```
Each active call shown as a card:
┌────────────────────────────────────────────────────────────┐
│ 📞 +92-300-1234567      🟢 ACTIVE    Language: Urdu    2:14 │
│ ─────────────────────────────────────────────────────────── │
│ State: [INTAKE ▶]  STT: Deepgram  LLM: gpt-4o-mini  TTS: Azure │
│ Mic level: ████████░░░░░░░░                                │
│ ─────────────────────────────────────────────────────────── │
│ Transcript (live):                                          │
│ 👤 Patient: "میرا نام احمد ہے اور مجھے ڈاکٹر صاحب سے..."  │
│ 🤖 AI: "جی احمد صاحب، آپ کس ڈاکٹر سے ملنا چاہتے ہیں؟"   │
│ ─────────────────────────────────────────────────────────── │
│ Latency: STT 143ms | LLM 187ms | TTS 165ms | Total 495ms   │
│ [Force Transfer] [Override Language] [End Call] [View Full] │
└────────────────────────────────────────────────────────────┘
```

### Appointments Page — FullCalendar Integration
- Month / Week / Day views
- Color-coded by doctor (each doctor gets assigned color)
- Click slot → Quick Book modal
- Drag-to-reschedule (with conflict check before saving)
- Hover tooltip: patient name, duration, booking source (AI/manual)
- List view toggle for front-desk tablet use
- Print day sheet button
- "Booked via AI" vs "Booked manually" indicator badge
- Filter by doctor, department, appointment type

### Analytics Page — Charts
| Chart | Type | Data |
|-------|------|------|
| Call volume (7/30/90 days) | Line chart | Total calls per day |
| Language distribution | Pie/Donut | Urdu / Punjabi / English % |
| Booking success rate | Bar chart | Booked / Escalated / Abandoned |
| Provider performance | Multi-bar | Per-provider: latency, cost, error rate |
| Cost breakdown | Stacked bar | STT + LLM + TTS + Telephony per day |
| Peak hours heatmap | Heatmap | Call volume by hour and day of week |
| Escalation reasons | Pie | Why calls were escalated to human |
| Top appointment types | Horizontal bar | Most booked specialties |

### Provider Settings Page — Design
```
┌─────────────────────────────────────────────────────────────────┐
│ PROVIDER CONFIGURATION                            [Save Changes] │
│                                                                 │
│ TELEPHONY                                                       │
│ ○ Plivo (Primary — $0.0085/min)    ● Active                    │
│ ○ Twilio (Fallback — $0.0085/min)                              │
│ ○ Telnyx ($0.009/min)                                          │
│                                                                 │
│ STT — Per Language                                              │
│ Urdu:    [Deepgram Nova-2 ▼]  [0.45 confidence ▼]  $0.59/hr  │
│ English: [Deepgram en-US  ▼]  [0.70 confidence ▼]  $0.59/hr  │
│ Punjabi: [Groq Whisper    ▼]  [0.40 confidence ▼]  $0.11/hr  │
│                                                                 │
│ LLM                                                             │
│ [gpt-4o-mini ▼] $0.15/1M in · $0.60/1M out                   │
│ [Test with sample Urdu conversation]                           │
│                                                                 │
│ TTS — Per Language                                              │
│ Urdu:    [Azure ur-PK-UzmaNeural ▼]  $16/1M chars             │
│ English: [OpenAI tts-1 nova       ▼]  $15/1M chars            │
│ Punjabi: [Azure ur-PK-UzmaNeural ▼]  (fallback) $16/1M chars  │
│                                                                 │
│ [Test TTS] [Hear Sample] [Reset to Defaults]                  │
└─────────────────────────────────────────────────────────────────┘
```

### Language Settings Page — Design
```
┌──────────────────────────────────────────────────────────────┐
│ LANGUAGE & LOCALIZATION                       [Save Changes]  │
│                                                              │
│ Default Call Language                                        │
│ ● Urdu (Pakistani)    ○ English    ○ Punjabi                 │
│                                                              │
│ Language Detection                                           │
│ ○ DTMF prompt at call start (recommended)                   │
│ ○ Auto-detect (Whisper — adds ~200ms to first response)     │
│ ○ Use clinic default for all calls                          │
│                                                              │
│ DTMF Prompt Language                                         │
│ "Press 1 for Urdu, Press 2 for Punjabi, Press 3 for English" │
│ [Regenerate Audio] [Preview] [Upload Custom]                 │
│                                                              │
│ Pakistani Urdu Standards                                     │
│ ✅ Use Pakistani vocabulary (not Indian Urdu)                │
│ ✅ Shahmukhi script for Punjabi responses                    │
│ ✅ Pakistani date format (Monday=پیر not सोमवार)             │
│                                                              │
│ Per-Doctor Language Override                                 │
│ Dr. Ahmed:  [Urdu ▼]    Dr. Smith: [English ▼]              │
└──────────────────────────────────────────────────────────────┘
```

### RTL Text Rendering Requirements
- All Urdu/Punjabi text blocks in UI must use `dir="rtl"` attribute
- Font: `Noto Nastaliq Urdu` or `Jameel Noori Nastaleeq` for proper Urdu rendering
- Transcript display: automatic RTL detection per message
- Patient names in Urdu/Arabic script: correct shaping required
- Line height for Nastaliq: minimum `2.0` (Nastaliq needs more vertical space than Latin)

### UI Accessibility & UX Requirements
- Keyboard shortcuts for common actions (documented in help modal)
- All buttons have `aria-label` attributes
- Color is never the only indicator (icons + color + text)
- Loading skeletons for all async data (no empty flash states)
- Toast notifications for real-time events (call started, appointment booked, error)
- Confirmation dialogs for destructive actions (cancel appointment, end call forcefully)
- Responsive: works on 1024px+ (clinic desk/tablet), not mobile-optimized in v1
- Emergency calls highlighted with red pulse animation (cannot be missed)

---

## GLOBAL EXECUTION RULES

| Rule | Description |
|------|-------------|
| No phase skipping | Every phase must complete before the next begins |
| QA gate mandatory | Every phase output must pass QA Agent before proceeding |
| Production-grade only | No placeholder code, no TODOs in shipped modules |
| Modularity enforced | Every provider (STT/LLM/TTS/Telephony) independently swappable |
| Language isolation | Pakistani Urdu must not mix Indian vocabulary — enforced in prompts and tests |
| Testability required | Every module exposes a testable interface |
| Inherited params sacred | Do NOT change proven VAD/STT params without running full test suite |
| No audio filters | Proven to crash or distort — see failure list |
| Latency budgets enforced | End-to-end voice under 800ms per turn |
| HIPAA basics enforced | No PHI in logs, API keys never printed, recordings need consent |
| Max 3 QA iterations | After 3 failures → BLOCKED → human review required |
| RTL compliance | All Urdu/Punjabi text must render correctly with RTL layout |
| UI design system | All UI components must follow the design system above |

---

## AGENT STRUCTURE

### 1. Main Orchestrator Agent

**Role**: Central controller. Never writes code directly. Plans, assigns, reviews, routes.

**Skills**:
- Roadmap ownership (maintains this tracker as the single source of truth)
- Task decomposition (breaks each phase into atomic tasks with explicit input/output contracts)
- Assignment routing (selects correct sub-agent based on task type and current state)
- Output review (validates sub-agent deliverables match expected schema before QA handoff)
- QA triggering (passes deliverables to QA Agent with explicit acceptance criteria)
- Iteration management (tracks failure counts per task, detects infinite loops, escalates blockers)
- Cross-module consistency (API contracts, naming conventions, language configs coherent across agents)
- Dependency resolution (identifies when Phase N requires Phase M output before starting)
- Inherited knowledge enforcement (ensures agents respect proven params and failure patterns)
- Language coverage verification (ensures every feature is implemented for all 3 languages)

**Assignment Protocol**:
```
[ORCHESTRATOR → SUB-AGENT]
Task ID: <PHASE>.<SUBTASK>
Assigned To: <Agent Name>
Input: <what the agent receives>
Expected Output: <exact deliverable format>
Acceptance Criteria: <what QA will check>
Inherited Constraints: <specific rules from lessons-learned that apply>
Language Coverage Required: Urdu (ur-PK) | English | Punjabi (pa-PK)
```

---

### 2. Architecture Agent

**Role**: System design, service topology, data flow, all structural decisions.

**Skills**:
- Mermaid diagram generation (architecture, sequence, data flow, deployment, state machine, language routing flow, conversation flow)
- Service boundary definition (what each service owns, what it never touches)
- API contract drafting (REST interface specs between all services)
- Technology selection with explicit rationale (must consider each option against alternatives)
- Scalability analysis (load estimates, bottleneck identification, concurrent call capacity planning)
- Legacy system triage (module-by-module reuse/discard/wrap analysis with file-level specifics)
- Infrastructure planning (Docker Compose local, cloud migration path)
- Failure mode analysis (what happens when each external dependency fails — fallback chains)
- Provider swap architecture (how STT/LLM/TTS switches happen at runtime without call drop)
- Language routing architecture (how call language detection flows into pipeline config selection)
- UI architecture (page structure, component hierarchy, data flow from backend to UI)
- Architecture Decision Records (ADR) for every non-obvious choice

**Output Requirements**:
- All diagrams in Mermaid syntax (must render in GitHub Markdown)
- Service inventory table: name, responsibility, tech stack, owned data, exposed APIs, port
- Provider abstraction layer design (pluggable interface for each provider type)
- Language profile routing diagram (how DTMF/auto-detect selects language pipeline)
- UI sitemap and page hierarchy diagram
- Dependency graph between all services

---

### 3. Backend Agent

**Role**: All server-side logic, APIs, database models, business rules, integrations.

**Skills**:
- FastAPI route design (OpenAPI 3.0 spec-first approach)
- SQLAlchemy async model design (normalized, indexed, migration-ready)
- Telephony webhook handlers (Plivo + Twilio + Telnyx — each has different webhook format)
- Provider abstraction layer implementation (one interface per type, multiple backends)
- Language profile management (store/retrieve per-clinic language configs)
- JWT auth + RBAC (clinic admin / doctor / receptionist / system roles)
- Appointment CRUD with full audit trail
- Patient entity management (no PHI in logs — hash/mask where needed)
- Async task processing (Celery for reminders, post-call analytics jobs)
- Rate limiting and request throttling
- Alembic migration scripts (versioned, tested for rollback safety)
- Error handling (circuit breakers, retries with exponential backoff for provider APIs)
- Config management (all provider API keys in .env, switchable via UI without restart)
- WebSocket server for real-time UI updates (live call state, transcript streaming)

**Output Requirements**:
- OpenAPI YAML spec for every endpoint
- SQLAlchemy model files with relationships, indexes, and constraints
- Provider adapter classes (one per provider, conforming to abstract base class)
- Alembic migration files (versioned and tested)
- WebSocket event schema (all event types with payload definitions)

---

### 4. Voice Pipeline Agent

**Role**: End-to-end voice interaction — from telephony inbound to TTS response.

**Skills**:
- Pipecat 0.0.85 pipeline construction (MUST use exactly this version — no upgrades)
- Language-aware pipeline initialization (load language profile at call start based on DTMF/auto-detect)
- STT provider adapter (Deepgram default for Urdu/English, Groq Whisper for Punjabi)
- LLM provider adapter (gpt-4o-mini default, language-specific system prompt loaded per call)
- TTS provider adapter (Azure ur-PK for Urdu/Punjabi, OpenAI for English)
- Telephony adapter (Plivo default, webhook handler per provider)
- Streaming pipeline (STT partials → LLM start → TTS first chunk — all overlapped)
- Filler audio system (pre-recorded in all 3 languages: Urdu/Punjabi/English)
- TTS cache system (disk-backed LRU — separate cache per language to prevent cross-language hits)
- Confidence-filtered STT (custom subclass — thresholds: Urdu 0.45, English 0.70, Punjabi 0.40)
- Barge-in/interruption handling (`allow_interruptions=true` — never change this)
- Silence detection and timeout handling
- DTMF language selection handler (detects "1/2/3" at call start → loads language profile)
- Noise word filter (language-specific filler word lists loaded per language profile)
- Conversation state machine (idle → language_select → greeting → intake → scheduling → escalation → goodbye)
- Emergency detection trigger (2-turn maximum — all 3 languages have emergency vocabulary)
- Number-to-text conversion per language (Urdu: تین سو دو, Punjabi: ਤਿੰਨ ਸੌ ਦੋ, English: three hundred two)
- Latency instrumentation (per-stage timing logged for every call)
- AudioLevelMonitor (broadcast to UI for mic level meter)
- RTL-aware transcript broadcasting (flag language in WebSocket event for UI rendering)

**Proven Pipeline Architecture** (inherit and extend):
```
Telephony Input → DTMF Language Selector → Silero VAD → AudioLevelMonitor
→ ConfidenceFilteredSTT [language-aware threshold]
→ STTBroadcaster [language-tagged events] → UserContextAggregator
→ LLM [language profile system prompt]
→ ResponseBroadcaster → TTSCacheGate [language-namespaced cache]
→ TTS [language profile voice] → TTSCacheCapture
→ AssistantContextAggregator → Telephony Output
```

**Proven VAD Parameters** (do not change without full test run):
- `confidence=0.6, start_secs=0.2, stop_secs=0.6, min_volume=0.5`

**Proven STT Parameters** (do not change without full test run):
- `endpointing=600ms, utterance_end_ms=1500, no_delay=true, vad_events=true`
- Confidence thresholds: `Urdu=0.45 | English=0.70 | Punjabi=0.40`

---

### 5. Scheduling Agent

**Role**: All appointment logic, doctor availability, conflict resolution, calendar management.

**Skills**:
- Availability window modeling (recurring weekly schedules, per-day exceptions, Pakistani public holidays)
- Conflict detection algorithm (O(log n) slot lookup, DB-level constraint enforcement)
- Appointment CRUD with full audit trail
- PKT timezone handling (UTC+5, no DST observed in Pakistan)
- Pakistani public holiday calendar (Eid ul-Fitr, Eid ul-Adha, Independence Day, etc.)
- Multi-doctor load balancing (round-robin, specialty routing, seniority-based priority)
- Waitlist management (auto-notify on cancellation via SMS/WhatsApp)
- Double-booking prevention (optimistic locking + DB unique constraint)
- Reminder logic (SMS via Plivo, WhatsApp via Twilio Business API — configurable intervals)
- Cancellation and reschedule flows with configurable clinic policy
- Slot duration configuration per appointment type (GP: 10min, Specialist: 20min, Procedure: 45min)
- Voice-driven booking intent parser (extract date, time, doctor, appointment type from natural language)
- Multi-language date/time parser (Urdu date expressions, Punjabi date expressions, English)
  - Urdu: "پرسوں دوپہر" → relative date to absolute PKT datetime
  - Punjabi: "اگلے اتوار" → next Sunday in PKT
  - English: "next Monday afternoon" → PKT datetime

**Output Requirements**:
- ERD for scheduling domain
- Conflict detection algorithm pseudocode with complexity analysis
- Edge case matrix (simultaneous bookings, DST none for PK, provider block-offs, Eid holidays variable dates)
- Multi-language date/time parser specification with test cases in all 3 languages

---

### 6. Prompt Engineering Agent

**Role**: All LLM prompt design, the multilingual prompt library, optimization, and versioning.

**Skills**:
- System prompt architecture (persona, constraints, tone, escalation rules — one variant per language)
- Pakistani Urdu prompt design: pure Pakistani vocabulary, no Indian Urdu, no English mixing
- Punjabi prompt design: Shahmukhi script, appropriate dialect, medical vocabulary in Punjabi
- English prompt design: neutral professional, clear medical reception persona
- Medical reception persona (empathetic, efficient, professional — not robotic)
- Few-shot example curation (minimum 5 examples per prompt per language = 45+ total examples)
- Prompt chaining (multi-turn context: patient name remembered, previous statements not repeated)
- Anti-hallucination guardrails ("if unknown, ask don't guess" — critical for medical scheduling)
- One-question-at-a-time rule (proven effective in triage system — inherit)
- Conversation flow rules (greeting → name → reason → doctor → time → confirm)
- Emergency vocabulary per language (all trigger phrases that activate emergency_detection prompt)
- Urdu output enforcement rules (forbidden: English mixing, Indian vocabulary, excessive formality)
- Punjabi output rules (Shahmukhi script, Pakistani Punjabi dialect, not Gurmukhi)
- Prompt versioning (version ID, changelog, token count, evaluation score per prompt)
- Token optimization (minimize prompt length while preserving behavior)
- Prompt caching strategy (Claude API: which prefix to cache, how long)
- Pakistani date/time expressions in prompt examples (Urdu months, Islamic calendar awareness)

**Required Prompt Library** (all 3 language variants for each = 27 prompt files minimum):
| ID | Purpose | Urdu | English | Punjabi |
|----|---------|------|---------|---------|
| `greeting` | Initial call, patient identification | `greeting_ur.yaml` | `greeting_en.yaml` | `greeting_pa.yaml` |
| `receptionist_intake` | Name, reason, doctor preference | `intake_ur.yaml` | `intake_en.yaml` | `intake_pa.yaml` |
| `scheduling_intent` | Extract appointment details | `scheduling_ur.yaml` | `scheduling_en.yaml` | `scheduling_pa.yaml` |
| `availability_query` | Present available slots | `availability_ur.yaml` | `availability_en.yaml` | `availability_pa.yaml` |
| `confirmation` | Confirm/cancel/reschedule | `confirm_ur.yaml` | `confirm_en.yaml` | `confirm_pa.yaml` |
| `escalation_decision` | Transfer to human | `escalate_ur.yaml` | `escalate_en.yaml` | `escalate_pa.yaml` |
| `emergency_detection` | Life-threatening situation | `emergency_ur.yaml` | `emergency_en.yaml` | `emergency_pa.yaml` |
| `post_call_summary` | Structured clinic record | `summary_ur.yaml` | `summary_en.yaml` | `summary_pa.yaml` |
| `date_parser` | Extract date/time | `date_ur.yaml` | `date_en.yaml` | `date_pa.yaml` |

**Emergency Vocabulary** (must cover all trigger phrases per language):
- Urdu: `سینے میں درد, سانس نہیں آ رہا, بے ہوشی, حادثہ, ہارٹ اٹیک, خون زیادہ`
- Punjabi: `سینے اچ درد, ساہ نئیں آؤندا, ہوش نئیں, حادثہ ہو گیا`
- English: `chest pain, can't breathe, unconscious, accident, heart attack, heavy bleeding`

**Output Requirements**:
- YAML prompt files: `id, version, language, purpose, system_prompt, examples[], forbidden_patterns[], eval_criteria[], token_count`
- Language compliance test: each Urdu prompt tested for zero Indian vocabulary
- Prompt evaluation scorecard: clarity, accuracy, safety, tone, language purity, response conciseness

---

### 7. Analytics Agent

**Role**: Call log processing, transcript analysis, cost tracking, and reporting.

**Skills**:
- Call event schema design (start, DTMF language select, STT events, LLM events, TTS events, end, transfer, errors, provider-switch events)
- Language distribution tracking (per-call language tagged, aggregated in analytics)
- Transcript storage (raw + PII-masked version, RTL language flagged for UI rendering)
- Post-call analysis (intent classification, resolution: booked/escalated/abandoned/emergency)
- KPI definitions: AHT (Average Handle Time), booking success rate, escalation rate, call abandonment rate, language distribution, cost per booking
- Provider performance tracking (per-provider: latency p50/p95, error rate, cost per call)
- Cost per call tracker (STT cost + LLM cost + TTS cost + telephony cost = total per call)
- Dashboard pre-aggregated tables (daily/weekly/monthly rollups for fast chart queries)
- Anomaly detection (spike in escalations, sudden latency increase, provider error rate > 5%)
- HIPAA-conscious logging (no patient names in analytics tables — use anonymized IDs)
- WebSocket real-time feed (live call count, active language distribution, current cost today)
- Export pipeline (CSV/JSON for clinic management reports, de-identified for analytics)

---

### 8. Frontend / Portal Agent

**Role**: Build the complete clinic-facing web portal — professional, feature-rich, attractive, and functional.

**Skills**:
- Tailwind CSS + Alpine.js component development (no build step — CDN-loaded for v1)
- Jinja2 template architecture (base template, page templates, component partials)
- Dark/light mode implementation (CSS variables + Alpine.js + localStorage persistence)
- RTL text rendering (Urdu/Punjabi transcript and name display with `dir="rtl"`)
- Noto Nastaliq Urdu font integration (correct Urdu script shaping)
- FullCalendar.js integration (appointment calendar with drag-drop, month/week/day views)
- Chart.js dashboard (line, bar, pie, donut, heatmap — all with dark mode support)
- WebSocket client (Alpine.js reactive — live call cards, live transcript streaming, live stats)
- Provider selector UI (per-language STT/LLM/TTS/Telephony dropdowns with live pricing display)
- Language settings UI (DTMF config, default language, per-doctor overrides)
- Live call monitor cards (mic level meter, per-stage latency display, real-time transcript)
- Appointment calendar (FullCalendar, color-coded by doctor, drag-to-reschedule)
- Patient records (call history, AI-generated summaries, appointment timeline)
- Analytics charts (all 8 chart types from UI Design System section)
- Toast notification system (non-blocking alerts for bookings, errors, emergencies)
- Modal dialog system (confirmation, quick-book, force-transfer, view transcript)
- Sidebar navigation (collapsible, active state highlighting, role-aware menu items)
- Form validation (client-side via Alpine.js + server-side via FastAPI)
- Loading skeleton states (for every async data component)
- Print stylesheet (appointment day sheet, call log report)
- Keyboard shortcuts (documented in help modal — `?` key opens help)
- RBAC-aware rendering (admin menu items hidden from doctors/receptionists)
- Emergency call visual alert (red pulse animation, cannot be dismissed accidentally)
- System health indicators in sidebar footer (provider status: green/yellow/red dots)

**Component Library** (reusable across all pages):
- `StatCard` — metric with icon, number, trend indicator
- `CallCard` — live call with state, language, latency, transcript preview
- `AppointmentCard` — appointment with doctor color, patient name, time, source badge
- `ProviderSelector` — dropdown with pricing label
- `LanguageBadge` — Urdu / English / Punjabi with flag icon
- `TranscriptBlock` — message with RTL detection, speaker identification, timestamp
- `LatencyBar` — visual bar showing STT/LLM/TTS breakdown
- `ToastNotification` — success/warning/error/emergency variants
- `DataTable` — sortable, filterable, paginated
- `ConfirmDialog` — destructive action confirmation
- `EmergencyBanner` — full-width red alert for emergency calls

**Output Requirements**:
- All pages from the page inventory implemented and functional
- Design system compliance (colors, fonts, spacing, dark mode verified)
- RTL rendering verified for Urdu/Punjabi content
- All components responsive at 1024px minimum
- WebSocket live updates working on Dashboard and Live Call Monitor
- FullCalendar properly integrated with backend appointment API
- Chart.js charts populated from analytics API

---

### 9. Testing Agent

**Role**: All automated test suites — unit, integration, E2E, load, regression, and multilingual.

**Skills**:
- Unit tests for all business logic (target: >90% coverage)
- Integration tests for all API endpoints (happy path + error cases)
- Provider adapter tests (each STT/LLM/TTS/Telephony adapter tested independently)
- Voice pipeline simulation (mocked providers with canned responses in all 3 languages)
- Language isolation tests (Urdu output has zero Indian vocabulary — automated vocabulary checker)
- Scheduling conflict scenario matrix tests
- DTMF language selection flow tests (press 1 → loads Urdu profile, etc.)
- Load testing (20 simultaneous simulated calls — mix of all 3 languages)
- Latency benchmark assertions (each pipeline stage within budget)
- Provider switch tests (switch STT mid-test → verify pipeline hot-reloads correctly)
- UI functional tests (provider selector saves, language settings apply to next call)
- Regression suite (blocks all previously-fixed bugs from returning)
- Test data factory:
  - Pakistani patient names (Urdu script + Roman transliteration)
  - Doctor names (common Pakistani medical names)
  - Sample Urdu symptoms (extend triage system's 173+ aliases)
  - Punjabi symptom phrases
  - Pakistani date expressions in all 3 languages

---

### 10. QA & Validation Agent (Independent)

**Role**: Independent validation. Does not trust any agent's self-assessment. Never approves incomplete work.

**Validation Checklist**:
- Completeness: every deliverable item from phase spec is present
- Schema: outputs match required format exactly
- Inherited constraints: zero violations of proven params or failure patterns
- Language coverage: all 3 languages implemented (not just English)
- Language purity: Pakistani Urdu has zero Indian vocabulary; Punjabi is Shahmukhi
- Logic: no contradictions between modules
- Edge cases: boundary conditions explicitly handled
- Security: no API keys in logs, no PHI exposed
- Provider abstraction: each provider truly swappable without code changes
- Latency: all pipeline stages within defined budget
- UI: design system compliance, RTL rendering, dark mode
- Cross-module: Backend API matches what Voice Pipeline calls, UI matches API
- Regression: this phase doesn't break any previously validated phase

**QA Report Format**:
```
QA Report — Phase <N>.<SubTask>
Status: PASS | FAIL
Tested By: QA Agent
Timestamp: <datetime>

PASSED:
- <item>

FAILED:
- <item>: <specific failure description>

Language Coverage Gaps:
- <language>: <what is missing>

Required Fixes Before Re-Test:
1. <exact fix required>

Blocking: YES / NO
Iteration: <N of 3 max>
[ESCALATE TO HUMAN — if iteration 3 and still FAIL]
```

---

## LOOP SYSTEM

```
Orchestrator reads tracker → selects next incomplete phase
        ↓
Decomposes into atomic tasks with explicit input/output contracts
Ensures language coverage required is specified in each task
        ↓
Assigns to Sub-Agent with:
  Task ID + Input + Expected Output + Acceptance Criteria
  + Inherited Constraints + Language Coverage Required
        ↓
Sub-Agent executes within its domain only
        ↓
Orchestrator reviews output:
  - Schema compliance check
  - Language coverage check (all 3 languages present)
  - Inherited constraints check
        ↓
QA Agent runs independent validation
        ↓
         ┌──────────┤
       FAIL         PASS
         │              ↓
         │         Tracker checkbox updated ✅
         │         Phase marked COMPLETE
         │         Next phase begins
         ↓
QA report sent to responsible Sub-Agent
Sub-Agent fixes specific failures
Re-submits (iteration count++)
If count > 3 → BLOCKED → human review required
```

---

## EXECUTION PHASES

### PHASE STATUS LEGEND
| Symbol | Meaning |
|--------|---------|
| ⬜ | NOT STARTED |
| 🔄 | IN PROGRESS |
| 🔁 | IN QA LOOP (fixing failures) |
| ✅ | COMPLETE (QA passed, locked) |
| 🚫 | BLOCKED (3+ QA failures, human review needed) |

---

### Phase 1 — Legacy System Triage `✅`
**Agent**: Architecture Agent
**QA**: QA Agent
**Input**: AI-Clinical-Triage-System codebase (all files in directory)

**Deliverables**:
- [x] Module-by-module reuse/discard table: auth, DB, pipeline, TTS cache, STT filter, scoring, noise filter, UI, tests — with exact file name per module
- [x] Reusable utilities list with exact `file.py:function_name` references
- [x] Proven pipeline architecture diagram (Mermaid) — show what to extend, not replace
- [x] Complete anti-pattern list (reference specific failures from CLAUDE_CONTEXT.md + CLAUDE.md)
- [x] Language support assessment: what exists for Urdu, what needs to be added for Punjabi/English
- [x] Effort estimate: reuse vs rebuild per component (Low/Medium/High)

**QA Acceptance Criteria**:
- Every file in triage system categorized (no un-reviewed files)
- Reusable items reference specific file paths and function names
- No contradiction with inherited knowledge table
- Language support gaps explicitly identified

**Output**: `docs/PHASE1_OUTPUT.md` ✅

---

### Phase 2 — Product Definition `✅`
**Agent**: Architecture Agent
**Input**: Phase 1 output + receptionist system requirements

**Deliverables**:
- [x] Full patient conversation flow list (20 scenarios — all 3 languages)
- [x] Escalation decision tree (when AI transfers to human, to whom, via what channel — per language)
- [x] Out-of-scope definition (what the AI will explicitly NOT do)
- [x] Non-functional requirements: latency (<800ms), availability (99%+), concurrent calls (20)
- [x] Pakistan-specific requirements:
  - [x] PKT timezone (UTC+5, no DST)
  - [x] +92 phone number format handling
  - [x] WhatsApp preferred for appointment reminders (over SMS)
  - [x] Pakistani public holiday calendar integration
  - [x] Urdu/Punjabi/English trilingual support
  - [x] Pakistani Urdu vocabulary standard (NOT Indian Urdu)
- [x] Language selection UX definition (DTMF chosen with rationale)
- [x] Provider selection rationale (Plivo primary, Twilio fallback — cost projection for 1000 calls/month ~$52)
- [x] UI feature requirements list (15 pages with routes, interactions, real-time flags)
- [x] UI design language decision (Tailwind + Alpine.js confirmed with rationale)

**Output**: `docs/PHASE2_OUTPUT.md` ✅

---

### Phase 3 — Diagram Creation `✅`
**Agent**: Architecture Agent
**Input**: Phase 2 product definition

**Deliverables** (all in Mermaid syntax, all must render):
- [x] System architecture diagram (all services + provider abstraction + language routing)
- [x] Provider abstraction layer diagram (how each provider type plugs in)
- [x] Language routing flow diagram (DTMF input → language profile → pipeline config)
- [x] Sequence diagram: Urdu call — inbound → DTMF → greeting → intake → booking → confirmation
- [x] Sequence diagram: Emergency detection → escalation (all 3 languages)
- [x] Conversation state machine (with language selection as initial state)
- [x] Data flow diagram (call audio path, transcript path, PHI handling)
- [x] Deployment diagram (Docker Compose services, ports, volumes)
- [x] Scheduling flow diagram (voice intent → conflict check → booking → reminder)
- [x] UI sitemap diagram (all pages + navigation + role access)
- [x] UI component hierarchy diagram (Dashboard component tree)

**Output**: `docs/architecture/PHASE3_DIAGRAMS.md` ✅

---

### Phase 4 — Architecture Definition `✅`
**Agent**: Architecture Agent
**Input**: Phase 3 diagrams

**Deliverables**:
- [x] Service inventory table (name, responsibility, framework, DB, APIs, port, language awareness)
- [x] Provider abstraction interface definitions (abstract base classes: BaseSTT, BaseLLM, BaseTTS, BaseTelephony, BaseLanguageProfile)
- [x] Language profile architecture spec (how language configs are stored, loaded, and switched)
- [x] Inter-service communication contracts (REST + WebSocket event schemas)
- [x] External dependency list with version pins (pipecat==0.0.85, all provider SDKs pinned)
- [x] Docker Compose configuration (all services, environment variables, volume mounts)
- [x] Environment variable schema (all API keys, language configs, feature flags, port assignments)
- [x] Secrets management approach (.env + .gitignore — keys never in code)
- [x] UI architecture spec (Tailwind CDN config, Alpine.js store design, WebSocket client architecture)
- [x] RTL rendering approach (CSS + font specification for Urdu/Punjabi text)
- [x] ADRs for: language detection method chosen, Punjabi TTS fallback decision, UI framework choice

**Output**: `docs/PHASE4_OUTPUT.md` ✅

---

### Phase 5 — Reusable Skills System `✅`
**Agent**: Prompt Engineering Agent + Voice Pipeline Agent
**Input**: Phase 4 architecture + inherited lessons

**Deliverables**:
- [x] Complete multilingual prompt library (27 YAML files — 9 prompts × 3 languages)
- [x] Pakistani Urdu vocabulary compliance checker (automated — list of forbidden Indian vocabulary)
- [x] Punjabi prompt validation (Shahmukhi script verified, Pakistani dialect)
- [x] Emergency vocabulary catalog (all trigger phrases in all 3 languages)
- [x] Prompt versioning system (version ID, changelog, eval score per prompt file)
- [x] Language profile configuration system (code + config spec for all 3 language profiles)
- [x] Latency measurement framework (per-stage instrumentation hooks — extend triage system approach)
- [x] Provider switching mechanism spec (runtime config change → pipeline hot-reload without call drop)
- [x] TTS cache system spec (language-namespaced disk-backed LRU — extend triage system)
- [x] Filler audio library (pre-recorded in all 3 languages for tool call gaps)
- [x] Change impact checklist (what to re-test when each provider or language config changes)
- [x] Number-to-text converter spec (Urdu + Punjabi + English implementations)

**Output**: `docs/PHASE5_OUTPUT.md` + `prompts/ur/` + `prompts/en/` + `prompts/pa/` (27 YAML files) ✅

---

### Phase 6 — Data Model Design `✅`
**Agent**: Backend Agent
**Input**: Phase 4 architecture + Phase 2 product definition

**Deliverables**:
- [x] Complete ERD (entities: Patient, Doctor, DoctorAvailability, Appointment, SlotReservation, CallLog, Transcript, ProviderConfig, ClinicConfig, AuditLog, NotificationLog)
- [x] SQLAlchemy model files (all columns, types, constraints, indexes, relationships)
- [x] ProviderConfig model (STT/LLM/TTS/Telephony selection + per-language JSON config)
- [x] CallLog model (language used, provider chain, per-stage latency, total cost)
- [x] Transcript model (raw text, language code, RTL flag, PII-masked version)
- [x] ClinicConfig model (clinic name, default language, business hours JSON, holidays JSON)
- [x] Audit log schema (user, action, entity, old value, new value, timestamp, IP)
- [x] Alembic migration files (versioned, upgrade + downgrade)
- [x] Data retention policy (call recordings: 90 days, transcripts: 1 year, analytics: indefinite masked)
- [x] PHI field inventory (14 PHI fields across 5 tables)

**Output**: `models/*.py` + `migrations/versions/0001_initial_schema.py` + `docs/PHASE6_OUTPUT.md` ✅

---

### Phase 7 — Implementation Roadmap `✅`
**Agent**: Main Orchestrator
**Input**: All Phase 1-6 outputs

**Deliverables**:
- [x] Ordered implementation plan with inter-phase dependencies
- [x] Risk register (what could block each phase, mitigation approach)
- [x] Definition of Done per module (explicit criteria beyond "it runs")
- [x] Integration test plan (how modules are tested together end-to-end)
- [x] Language coverage verification plan (explicit test for each language at each phase)

**Output**: `docs/PHASE7_ROADMAP.md` ✅

---

### Phase 8 — Implementation (Iterative) `⬜`

**Rule**: Every sub-phase → Build → Orchestrator Review → QA Test → Loop until PASS

---

#### 8.1 — Provider Abstraction Layer `✅`
**Agent**: Backend Agent + Voice Pipeline Agent

**Optimization Standards (from checklist V4, V6, V8)**:
- [ ] Prompt caching enabled on all LLM adapters at construction time (Anthropic: `enable_prompt_caching=True`, OpenAI: automatic)
- [ ] All LLM adapters expose `model_tier` parameter — `fast` (gpt-4o-mini / Haiku) and `quality` (GPT-4o / Sonnet)
- [ ] All STT adapters use WebSocket streaming — no HTTP request-response adapters (Deepgram WS default)

**STT Adapters**:
- [ ] `BaseSTT` abstract class (methods: `transcribe_stream()`, `set_language()`, `get_confidence_threshold()`)
- [ ] `DeepgramSTTAdapter` — language `ur` / `en-US`, proven confidence thresholds
- [ ] `GroqWhisperSTTAdapter` — for Punjabi (`pa`) and fastest English/Urdu
- [ ] `OpenAIWhisperSTTAdapter` — multilingual fallback
- [ ] `AzureSpeechSTTAdapter` — `ur-PK` explicit locale for best Pakistani Urdu
- [ ] `GoogleSTTAdapter` — `ur-PK` and `en-US`
- [ ] `AssemblyAISTTAdapter` — English-only backup

**LLM Adapters**:
- [ ] `BaseLLM` abstract class (methods: `stream_response()`, `set_language_profile()`, `cache_prefix()`)
- [ ] `OpenAILLMAdapter` — gpt-4o-mini (default), gpt-4o
- [ ] `AnthropicLLMAdapter` — claude-haiku-4-5, claude-sonnet-4-6 (with prompt caching)
- [ ] `GeminiLLMAdapter` — gemini-2.0-flash
- [ ] `GroqLLMAdapter` — llama-3.3-70b

**TTS Adapters**:
- [ ] `BaseTTS` abstract class (methods: `synthesize_stream()`, `set_language()`, `set_voice()`)
- [ ] `AzureTTSAdapter` — `ur-PK-UzmaNeural` (default Urdu/Punjabi), English voices (BEST for Pakistani Urdu)
- [ ] `OpenAITTSAdapter` — `nova` voice (default English — proven)
- [ ] `ElevenLabsTTSAdapter` — `eleven_flash_v2_5` model ONLY (never v3)
- [ ] `GoogleTTSAdapter` — `ur-PK` + English
- [ ] `CartesiaTTSAdapter` — English only, fastest

**Telephony Adapters**:
- [ ] `BaseTelephony` abstract class (methods: `handle_inbound()`, `handle_dtmf()`, `transfer_call()`, `end_call()`)
- [ ] `PlivoAdapter` — default (cheapest for Pakistan)
- [ ] `TwilioAdapter` — fallback (most reliable)
- [ ] `TelnxAdapter` — secondary option

**Language Profile System**:
- [ ] `LanguageProfile` class (loads STT + LLM + TTS config per language code)
- [ ] `DTMFLanguageSelector` pipeline component (detects 1/2/3 keypress → returns language code)
- [ ] Provider registry + factory (instantiate any provider by string key from config)
- [ ] Runtime switching mechanism (config API call → pipeline rebuilds for next call)

---

#### 8.2 — Backend Core `✅`
**Agent**: Backend Agent
- [ ] All analytics writes, call log writes, and transcript saves use `run_in_executor` — never `await` synchronous I/O in the hot path (checklist V10)
- [ ] FastAPI app scaffold with lifespan events (startup: load providers, warmup TTS cache)
- [ ] PostgreSQL connection + async SQLAlchemy session management
- [ ] Alembic migration runner (auto-runs on startup in dev mode)
- [ ] Patient CRUD endpoints (`GET/POST/PUT /patients`)
- [ ] Doctor management endpoints (`GET/POST/PUT/DELETE /doctors`)
- [ ] Appointment CRUD endpoints (`GET/POST/PUT/DELETE /appointments`)
- [ ] ProviderConfig endpoints (`GET /providers/config`, `PUT /providers/config`)
- [ ] LanguageProfile endpoints (`GET /language/profiles`, `PUT /language/config`)
- [ ] ClinicConfig endpoints (`GET/PUT /clinic/settings`)
- [ ] Audit logging middleware (every write operation logged)
- [ ] JWT auth middleware + RBAC decorator
- [ ] WebSocket manager (broadcast live events to connected portal clients)
- [ ] Health check endpoints (`/health`, `/health/providers` — check all API key validity)
- [ ] Static file serving (Tailwind CSS build artifacts, fonts, pre-recorded audio files)

---

#### 8.3 — Scheduling Engine `✅`
**Agent**: Scheduling Agent
- [ ] `AvailabilityEngine` — generate slots from doctor schedule + block-offs + holidays
- [ ] `ConflictDetector` — O(log n) check: given doctor + datetime + duration → conflicts?
- [ ] `BookingService` — atomic booking with optimistic locking (no double-book)
- [ ] `WaitlistManager` — queue patients, notify via SMS/WhatsApp on cancellation
- [ ] `PKTCalendar` — Pakistani timezone utility (UTC+5, no DST, Islamic calendar integration)
- [ ] `HolidayCalendar` — Pakistani public holidays (static list + variable date calculation for Eid)
- [ ] `ReminderScheduler` — Celery tasks for SMS/WhatsApp reminders at configurable intervals
- [ ] `MultiLanguageDateParser` — extract date/time from Urdu, Punjabi, English voice input
  - Urdu: "پرسوں", "اگلے جمعے", "دوپہر بارہ بجے" → PKT datetime
  - Punjabi: "اگلے اتوار", "کل صبح" → PKT datetime
  - English: "next Monday at 2pm", "tomorrow morning" → PKT datetime
- [ ] Cancellation/reschedule API (with policy: cancel within 2 hours = flagged)
- [ ] Voice intent to booking parameters mapper (LLM tool call → booking API call)

---

#### 8.4 — Voice Pipeline `✅`
**Agent**: Voice Pipeline Agent
- [ ] Pipecat 0.0.85 pipeline initialization (one pipeline instance per active call)
- [ ] `tool_choice="auto"` enforced — never `"required"` (checklist V2: saves 300–700ms on greeting/simple turns)
- [ ] Tool schema: `spoken_text` defined as first field in every tool definition (checklist V1: ~500ms free)
- [ ] TTS bypass gate: when tool handler sets `spoken_text` before LLM finishes, push directly to TTS — skip buffering (checklist V3: 1–2.5s saved)
- [ ] 3-layer context management: Layer 1 structured state block + Layer 3 last 4 raw turns (implement Layer 2 rolling summary if calls exceed 12 turns) (checklist V5)
- [ ] Model tiering logic: `fast` tier (gpt-4o-mini) for standard turns; escalate to `quality` tier on booking confirmation failure or low-confidence intent (checklist V6)
- [ ] Tiered system prompt: static cacheable base + dynamic injection (patient state, active doctor, available slots — never full doctors list) (checklist V7)
- [ ] STT circuit breaker: Deepgram → Groq Whisper → Google; if STT fails twice use regex extraction (checklist V9)
- [ ] Telephony webhook handler (Plivo primary — inbound call → pipeline start)
- [ ] `DTMFLanguageSelector` component (at call start — "Press 1/2/3" → language profile load)
- [ ] Language profile injector (loads correct STT/LLM/TTS adapters post-DTMF)
- [ ] `ConfidenceFilteredSTT` subclass (language-aware threshold: Urdu 0.45, English 0.70, Punjabi 0.40)
- [ ] Silero VAD with proven parameters (confidence=0.6, stop_secs=0.6 — DO NOT CHANGE)
- [ ] `STTBroadcaster` with language-specific noise word filter
- [ ] LLM integration (language profile system prompt loaded, prompt caching enabled)
- [ ] `ResponseBroadcaster` (language-tagged WebSocket events for RTL UI rendering)
- [ ] `TTSCacheGate` + `TTSCacheCapture` (language-namespaced disk-backed LRU)
- [ ] Filler audio player (language-specific pre-recorded phrases — plays during tool calls)
- [ ] `AudioLevelMonitor` (inherit from triage system — broadcast mic level to UI)
- [ ] Number-to-text converter (per-language: Urdu/Punjabi/English implementations)
- [ ] Conversation state machine:
  `idle → language_select → greeting → intake → scheduling → confirm → goodbye`
  `→ escalation` (from any state)
  `→ emergency` (from any state, 2-turn max detection)
- [ ] Emergency detection trigger (per-language vocabulary — escalate immediately)
- [ ] Scheduling tool call integration (LLM calls → BookingService → confirms slot)
- [ ] Latency instrumentation logger (per-stage timing every turn)
- [ ] Post-call event publisher (triggers analytics ingestion + summary generation)

---

#### 8.5 — Analytics System `✅`
**Agent**: Analytics Agent
- [ ] Call event ingestion API (accepts structured events during call lifecycle)
- [ ] Language distribution aggregation (Urdu/Punjabi/English call counts per day)
- [ ] Per-provider latency tracking (p50/p95 per STT/LLM/TTS provider per day)
- [ ] Cost per call calculator (STT seconds × rate + LLM tokens × rate + TTS chars × rate + telephony minutes × rate)
- [ ] Booking outcome tracker (booked / escalated / abandoned / emergency per call)
- [ ] Transcript storage (full raw + PII-masked, language code + RTL flag stored)
- [ ] Post-call summary generation (LLM call summarizing transcript → structured clinic record)
- [ ] Pre-aggregated analytics tables (daily/weekly/monthly rollups — Celery batch job)
- [ ] Anomaly detector (escalation rate > 20% triggers alert, latency p95 > 1000ms triggers alert)
- [ ] Analytics API endpoints (power all 8 dashboard charts)
- [ ] Real-time WebSocket broadcaster (live: active call count, cost today, language distribution)
- [ ] CSV/JSON export endpoint (for clinic management reports)

---

#### 8.6 — Doctor Portal (Full UI) `✅`
**Agent**: Frontend / Portal Agent

**Foundation**:
- [ ] Base Jinja2 template (`base.html`) — sidebar + header + main content + toast container
- [ ] Tailwind CSS CDN config (custom color palette from design system)
- [ ] Alpine.js CDN + global store (auth state, dark mode, WebSocket connection, notifications)
- [ ] Noto Nastaliq Urdu font (Google Fonts import for RTL text rendering)
- [ ] Inter font (Google Fonts import for UI text)
- [ ] Heroicons SVG sprite
- [ ] WebSocket client (Alpine.js — auto-reconnect, event dispatcher to components)
- [ ] Toast notification system (success / warning / error / emergency variants)
- [ ] Dark/light mode toggle (CSS variables switch, localStorage persist)

**Pages — Full Implementation**:
- [ ] **Login page** (`/login`) — clinic logo, credentials form, JWT store, redirect on success
- [ ] **Dashboard** (`/`) — stat cards + live call panel + upcoming appointments + volume chart + language pie chart
- [ ] **Live Call Monitor** (`/calls/live`) — active call cards with: language badge, state machine indicator, mic level bar, per-stage latency display, live transcript with RTL detection, action buttons (transfer, override language, end call)
- [ ] **Call History** (`/calls/history`) — filterable table (date, language, outcome, duration), click → full transcript modal with RTL rendering, cost breakdown per call
- [ ] **Appointments** (`/appointments`) — FullCalendar (month/week/day + list view), doctor color coding, click-to-book modal, drag-to-reschedule with conflict check, "AI booked" badge
- [ ] **Patients** (`/patients`) — searchable list, patient profile: demographics + call history timeline + AI summaries
- [ ] **Doctors** (`/doctors`) — doctor list, profiles, schedule overview, edit availability shortcut
- [ ] **Availability Setup** (`/doctors/:id/availability`) — weekly schedule builder, block-off dates, Pakistani holiday auto-import, per-doctor language override
- [ ] **Analytics** (`/analytics`) — all 8 Chart.js charts (call volume, language distribution, booking rate, provider performance, cost breakdown, peak hours heatmap, escalation reasons, top appointment types), date range picker
- [ ] **Provider Settings** (`/settings/providers`) — per-language STT/LLM/TTS/Telephony selectors with live pricing, test buttons, reset to defaults
- [ ] **Language Settings** (`/settings/language`) — default language, DTMF config + audio preview, auto-detect toggle, per-doctor overrides, Pakistani Urdu standards toggles
- [ ] **Clinic Settings** (`/settings/clinic`) — hospital name, logo upload, business hours, branding colors
- [ ] **User Management** (`/settings/users`) — create/edit staff, role assignment (admin/doctor/receptionist)
- [ ] **System Health** (`/settings/health`) — provider API key status lights, pipeline health, last error log

**UI Quality Requirements**:
- [ ] All pages dark mode verified (no hardcoded light colors)
- [ ] All Urdu/Punjabi text blocks verified RTL rendered correctly
- [ ] All async data has loading skeleton states (no blank flash)
- [ ] All destructive actions have confirmation dialogs
- [ ] Emergency call visual alert tested (red pulse, cannot be accidentally dismissed)
- [ ] All 8 analytics charts verified populated with real API data
- [ ] FullCalendar booking flow tested end-to-end
- [ ] Provider selector verified: changes propagate to next incoming call
- [ ] Print stylesheet verified for appointment day sheet

---

### Phase 9 — Testing & Benchmarking `✅`
**Agent**: Testing Agent

- [ ] Unit tests (>90% coverage on business logic: scheduling engine, conflict detector, language profiles, provider adapters)
- [ ] Integration tests (all API endpoints: happy path + error cases + auth boundary cases)
- [ ] Provider adapter tests (each STT/LLM/TTS/Telephony adapter tested independently with mocked SDKs)
- [ ] Language isolation tests (automated checker: Urdu output → zero Indian vocabulary detected)
- [ ] Pakistani Urdu vocabulary test suite (verify: خوش آمدید not استقبال, وقت not ٹائم, etc.)
- [ ] Punjabi prompt tests (Shahmukhi script verified in all Punjabi outputs)
- [ ] Multilingual pipeline simulation (canned Urdu + Punjabi + English conversations through full pipeline)
- [ ] DTMF language selection test (press 1 → Urdu pipeline, 2 → Punjabi, 3 → English)
- [ ] Provider switch test (change STT mid-test → verify next call uses new provider)
- [ ] Scheduling conflict scenario matrix (20+ scenarios: simultaneous booking, double-book attempt, DST-edge none for PK, Eid holiday, waitlist trigger)
- [ ] Latency benchmark suite (assert per stage: STT <300ms, LLM <400ms, TTS <200ms, total <800ms p95)
- [ ] Load test (20 concurrent simulated calls — mix of 3 languages — verify no degradation)
- [ ] Security scan (no API keys in logs, no PHI in analytics, auth bypass checks)
- [ ] UI functional tests (provider selector saves, language settings apply, charts load, RTL renders)
- [ ] Regression test baseline committed (blocks all previously-fixed failures)

---

### Phase 10 — Final Validation & Release Readiness `✅`
**Agent**: QA Agent (full independent audit)
**Decision**: CONDITIONAL GO — code complete, 5 pre-production blockers documented in `docs/RELEASE_REPORT.md`

- [x] All 15 conversation scenarios verified via unit/integration tests (live 45-run matrix: pre-prod blocker #3)
- [x] Pakistani Urdu language purity audit — PASS: 0 forbidden vocabulary; fixed `ریئل ٹائم` → `فوری طور پر`
- [x] Punjabi support verified — Whisper STT + Urdu TTS fallback (documented Phase 2 limitation)
- [x] All provider switches verified — provider registry tested, all adapters independently swappable
- [x] Latency budget architecture verified — all 4 optimizations implemented; live p95: pre-prod blocker #4
- [x] UI implemented across all 15 pages — live visual review: pre-prod blocker (needs staging)
- [x] FullCalendar booking flow implemented — live end-to-end: pre-prod blocker #3
- [x] Analytics charts implemented — real data: needs live PostgreSQL (staging)
- [x] HIPAA basics verified — no key leakage (automated), CNIC hashed, RBAC on all PHI routes
- [x] All critical and high-severity issues resolved — 242/243 tests pass, 0 failures
- [ ] Cloud deployment co-located with STT/TTS providers — PRE-PROD BLOCKER #4 (needs cloud account)
- [x] Docker Compose startup — `docker-compose.yml` + `Dockerfile` created ✅
- [x] Deployment runbook — `docs/DEPLOYMENT_RUNBOOK.md` created ✅
- [x] DTMF audio generation — `scripts/generate_dtmf_audio.py` created (run on staging) ✅
- [x] Release readiness report: **CONDITIONAL GO** — see `docs/RELEASE_REPORT.md`

---

### Phase 11 — Voice Settings Propagation + Advanced Scenarios `✅`
**Agent**: Voice Pipeline Agent + Prompt Engineering Agent + Testing Agent
**Trigger**: Post-launch user request — saved TTS voice not reaching live sessions; richer conversational scenarios required.

**Root cause identified (2026-04-30):**
- `api/realtime_session.py` hardcodes per-language OpenAI voices and never reads `ProviderConfig._ui` from DB.
- `pipeline/voice_pipeline.py` only branches `azure` vs `openai`; the ElevenLabs adapter is never instantiated even when selected.
- Neither path subscribes to the `config.updated` WebSocket event, so even rebuilt sessions see no change until process restart.

**Constraints (re-confirmed sacred):**
- VAD: confidence=0.6, stop_secs=0.6, min_volume=0.5
- STT thresholds: ur=0.45, en=0.70, pa=0.40
- ElevenLabs: `eleven_flash_v2_5` default; allowed companions `eleven_multilingual_v2`, `eleven_turbo_v2_5`. **Any v3 model is blocked at the adapter (HTTP 403 upstream).**

#### 11.1 Runtime config — saved settings reach every session `✅`
- [ ] `core/runtime_config.py` — load `ProviderConfig.language_providers["_ui"]` into a typed `VoiceSettings` dataclass (`tts_provider`, `female_voice_id`, `male_voice_id`, `custom_voice_id`, `eleven_model_id`, `openai_realtime_voice`).
- [ ] In-memory cache; subscribe to `config.updated` WS broadcast → invalidate on save.
- [ ] Unit test: save → reload returns updated values; cache hit path; missing/empty `_ui` returns sensible defaults.

#### 11.2 Realtime session honors saved voice (OpenAI path) `✅`
- [ ] `api/realtime_session.py` — replace hardcoded `_LANG_VOICE` with `runtime_config.load_voice_settings(...)` at session start.
- [ ] Map saved gender preference (default = female) → OpenAI Realtime voice (`alloy/echo/fable/nova/shimmer`); fallback to `nova` for English / `shimmer` for Urdu/Punjabi if no setting.
- [ ] Mid-call swap: send fresh `session.update` when a new gender preference is inferred from the conversation (e.g. "I want a female receptionist").

#### 11.3 Pipecat (PSTN) pipeline honors saved voice `✅`
- [ ] Replace static `azure | openai` branch in `pipeline/voice_pipeline.py:475-484` with `registry.make_tts(saved_provider, lang, voice)`.
- [ ] Preserve TTS cache compatibility (cache key includes `provider+voice+model`).
- [ ] Hot-swap on `config.updated` between turns (never mid-utterance).

#### 11.4 ElevenLabs additional models + adapter hardening `✅`
- [ ] `providers/tts/elevenlabs.py` — `MODEL_PRESETS` list (`flash` default, `multilingual_v2`, `turbo_v2_5`); `model_id` overridable via constructor + settings.
- [ ] Block v3 explicitly (`raise ValueError` with the 403 reason).
- [ ] Settings UI: add "Model" dropdown to TTS panel; persist to `_ui.elevenlabs_model_id`.

#### 11.5 Realtime proxy — ElevenLabs path `✅`
- [ ] If `tts_provider == "elevenlabs"`: configure session with `modalities=["text"]`, suppress upstream audio, stream LLM text deltas through `ElevenLabsTTSAdapter.synthesize_stream`, push to browser as `audio.delta` events.
- [ ] Confirm browser audio frame contract before merging (calls_live.html). If incompatible, ship for PSTN-only and document limitation.

#### 11.6 Tool surface for scenarios `✅`
Extend `api/test_session.py:_TOOLS` (auto picks up in realtime via `_REALTIME_TOOLS` mirror):
- [ ] `search_doctors(speciality?, gender?, name?, language?)` — supports "female doctor" / "specialist" filters.
- [ ] `get_earliest_availability(doctor_id?, speciality?, urgency=normal|urgent)` — earliest-slot fast path.
- [ ] `triage_severity(symptom_text)` — rule-based; returns `low|medium|high|emergency` + suggested action.
- [ ] `find_existing_appointment(patient_phone, last_4_cnic?)` — reschedule/cancel precondition.
- [ ] `reschedule_appointment(appointment_id, new_slot_utc)`.
- [ ] `cancel_appointment(appointment_id)` (already exists in API; expose as tool).
- [ ] All tool schemas: `spoken_text` first field; `tool_choice="auto"`.

#### 11.7 System prompts rewrite (per language) `✅`
- [ ] `prompts/{en,ur,pa}/intake_*.yaml` + `scheduling_*.yaml` — empathy variants, multi-step state retention rules, one-question-per-turn rule, Pakistani-Urdu vocabulary purity.
- [ ] Scenario examples covered explicitly: general booking, urgent ("severe headache"), severe symptom empathy, reschedule, female-doctor preference, time-specific ("tomorrow evening"), confused input ("not feeling well").

#### 11.8 Triage red-flag extensions `✅`
- [ ] `pipeline/emergency_detector.py` — high-severity lexicon en/ur/pa; emergency overrides booking with transfer to `ClinicConfig.triage_nurse_number`.

#### 11.9 Tests & docs `✅`
- [ ] `tests/integration/test_runtime_config.py` — settings save → load round-trip + cache invalidation.
- [ ] `tests/integration/test_scenarios.py` — text-mode scripted multi-turn flows for each scenario; assert tool call sequence + outcome.
- [ ] `tests/unit/test_elevenlabs_models.py` — preset list, v3 rejection.
- [ ] `docs/SCENARIOS.md` — table of scenarios → tool sequence → example transcript (ur/en).
- [ ] Final smoke: `python main.py` boots, dashboard populates, browser realtime session uses the saved female voice, switching to male in settings updates next turn.

#### Open risks / human verification required
- Live audio quality on actual phone calls (no automation possible).
- Native Pakistani Urdu / Punjabi speaker review of new prompts.
- ElevenLabs latency budget (LLM→text→ElevenLabs adds 150–300 ms; acceptable but documented).

---

## LATENCY BUDGET

| Stage | Target | Maximum | How Measured |
|-------|--------|---------|-------------|
| Telephony receive | 50ms | 100ms | Webhook arrival timestamp |
| DTMF detect + language load | 100ms | 200ms | Profile load timing |
| STT transcription (streaming) | 150ms | 300ms | STT callback delta |
| LLM first token | 200ms | 400ms | Time-to-first-byte |
| TTS first audio chunk | 100ms | 200ms | TTS callback delta |
| Audio delivery | 50ms | 100ms | Network RTT |
| **Total per turn (after language select)** | **550ms** | **800ms** | **End-to-end log** |

**Proven optimizations (all must be implemented)**:
1. Full streaming pipeline (STT partials → LLM start → TTS first chunk — all overlapped, not sequential)
2. Filler audio during scheduling tool calls (hides 500ms–2000ms dead air completely)
3. Language-namespaced TTS cache (greetings and confirmations play at 0ms)
4. Language profile pre-loaded at DTMF detection (no mid-call loading delay)

---

## SUCCESS CRITERIA (Measurable)

| Criterion | Target | How Measured |
|-----------|--------|-------------|
| Voice turn latency | <800ms p95 | Automated latency test suite |
| Scheduling accuracy | 100% conflict-free | Conflict scenario matrix |
| Call completion rate | >85% without escalation | Call simulation suite |
| Language coverage | All 3 languages working | Multilingual simulation tests |
| Pakistani Urdu purity | 0 Indian vocabulary instances | Automated vocabulary checker |
| Punjabi STT accuracy | >70% transcription rate | Punjabi canned phrase tests |
| Test coverage (business logic) | >90% | Coverage report |
| All provider adapters | Independently swappable | Provider swap test suite |
| Provider switch from UI | <5 seconds, no call drop | Manual + automated test |
| Concurrent call capacity | 20 simultaneous | Load test |
| Emergency detection | ≤2 turns in all 3 languages | Safety prompt eval |
| API response time | <200ms p99 | Integration benchmark |
| Cost tracking accuracy | ±5% of actual billing | Provider invoice vs tracker |
| UI dark mode | 100% pages compliant | Visual review checklist |
| RTL rendering | 100% Urdu/Punjabi text correct | RTL review checklist |

---

## FILE STRUCTURE (Target)

```
AI-Receptionist/
├── MASTER_TRACKER.md              ← This file (live state)
├── LATENCY_OPTIMIZATION.md        ← Existing (keep)
├── .env                           ← API keys (gitignored)
├── .env.example                   ← Template (committed)
├── .gitignore
├── requirements.txt
├── main.py                        ← FastAPI app entry point
├── core/
│   ├── config.py                  ← All env var loading
│   ├── database.py                ← Async SQLAlchemy session
│   └── auth.py                    ← JWT + RBAC
├── providers/
│   ├── base.py                    ← BaseSTT, BaseLLM, BaseTTS, BaseTelephony
│   ├── registry.py                ← Factory + provider registry
│   ├── language_profile.py        ← LanguageProfile loader (ur-PK/en/pa-PK)
│   ├── stt/
│   │   ├── deepgram.py            ← DEFAULT Urdu+English (0.45 threshold)
│   │   ├── groq_whisper.py        ← DEFAULT Punjabi + fastest multilingual
│   │   ├── openai_whisper.py      ← Fallback multilingual
│   │   ├── azure_speech.py        ← ur-PK explicit locale
│   │   ├── google_speech.py       ← ur-PK + en-US
│   │   └── assemblyai.py          ← English-only backup
│   ├── llm/
│   │   ├── openai.py              ← DEFAULT (gpt-4o-mini)
│   │   ├── anthropic.py           ← Claude Haiku/Sonnet + prompt caching
│   │   ├── gemini.py              ← Cheapest multilingual option
│   │   └── groq.py                ← Ultra-fast inference
│   ├── tts/
│   │   ├── azure_neural.py        ← DEFAULT Urdu/Punjabi (ur-PK voices)
│   │   ├── openai_tts.py          ← DEFAULT English (proven)
│   │   ├── elevenlabs.py          ← flash_v2_5 ONLY — never v3
│   │   ├── google_tts.py          ← ur-PK fallback
│   │   └── cartesia.py            ← English only, fastest
│   └── telephony/
│       ├── plivo.py               ← DEFAULT (cheapest PK)
│       ├── twilio.py              ← Fallback (most reliable)
│       └── telnyx.py              ← Secondary option
├── pipeline/
│   ├── voice_pipeline.py          ← Pipecat 0.0.85 pipeline
│   ├── dtmf_language_selector.py  ← DTMF "1/2/3" → language profile
│   ├── stt_filter.py              ← ConfidenceFilteredSTT (language-aware)
│   ├── tts_cache.py               ← Language-namespaced disk-backed LRU
│   ├── filler_audio.py            ← Pre-recorded phrases all 3 languages
│   ├── number_converter.py        ← Urdu/Punjabi/English number-to-text
│   └── state_machine.py           ← Conversation states
├── models/
│   ├── patient.py
│   ├── doctor.py
│   ├── appointment.py
│   ├── call_log.py                ← Includes language_code, per-stage latency, cost
│   ├── transcript.py              ← Includes language_code, rtl_flag, pii_masked
│   ├── provider_config.py         ← Per-language provider assignments
│   ├── language_profile.py        ← Language profile DB model
│   └── clinic_config.py
├── api/
│   ├── appointments.py
│   ├── scheduling.py
│   ├── calls.py
│   ├── providers.py               ← Provider config CRUD + switch endpoint
│   ├── language.py                ← Language config CRUD
│   ├── analytics.py               ← Dashboard chart data endpoints
│   ├── clinic.py                  ← Clinic settings
│   └── health.py                  ← Provider API key health checks
├── scheduling/
│   ├── engine.py                  ← Conflict detection + slot generation
│   ├── availability.py            ← Doctor schedule management
│   ├── pkt_calendar.py            ← PKT timezone + Pakistani holidays
│   └── date_parser.py             ← Urdu/Punjabi/English date parser
├── prompts/
│   ├── ur/                        ← Pakistani Urdu prompts
│   │   ├── greeting_ur.yaml
│   │   ├── intake_ur.yaml
│   │   ├── scheduling_ur.yaml
│   │   ├── availability_ur.yaml
│   │   ├── confirm_ur.yaml
│   │   ├── escalate_ur.yaml
│   │   ├── emergency_ur.yaml
│   │   ├── summary_ur.yaml
│   │   └── date_ur.yaml
│   ├── en/                        ← English prompts
│   │   └── (9 files)
│   └── pa/                        ← Punjabi prompts
│       └── (9 files)
├── analytics/
│   ├── ingestion.py               ← Call event consumer
│   ├── aggregation.py             ← Celery batch aggregation jobs
│   ├── metrics.py                 ← KPI calculations
│   └── cost_tracker.py            ← Per-call cost calculation
├── templates/
│   ├── base.html                  ← Sidebar + header + toast container
│   ├── login.html
│   ├── dashboard.html
│   ├── calls_live.html
│   ├── calls_history.html
│   ├── appointments.html
│   ├── patients.html
│   ├── doctors.html
│   ├── doctor_availability.html
│   ├── analytics.html
│   ├── settings_providers.html
│   ├── settings_language.html
│   ├── settings_clinic.html
│   ├── settings_users.html
│   └── settings_health.html
├── static/
│   ├── audio/
│   │   ├── dtmf_prompt_ur.wav     ← "Press 1 for Urdu..." in Urdu
│   │   ├── dtmf_prompt_en.wav     ← "Press 1 for Urdu..." in English
│   │   ├── filler_ur/             ← Pre-recorded Urdu filler phrases
│   │   ├── filler_pa/             ← Pre-recorded Punjabi filler phrases
│   │   └── filler_en/             ← Pre-recorded English filler phrases
│   ├── fonts/
│   │   └── NotoNastaliqUrdu/      ← Urdu font files
│   └── js/
│       └── alpine-store.js        ← Alpine.js global store
├── tts_cache/                     ← Disk-backed TTS audio cache
│   ├── ur-PK/
│   ├── en/
│   └── pa-PK/
├── migrations/                    ← Alembic migration files
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   ├── multilingual/              ← Language-specific test suites
│   │   ├── test_urdu_pipeline.py
│   │   ├── test_punjabi_pipeline.py
│   │   ├── test_english_pipeline.py
│   │   └── test_language_purity.py ← Pakistani Urdu vocabulary checker
│   └── benchmarks/
│       ├── test_latency.py
│       └── test_load.py
├── docs/
│   ├── architecture/              ← All Mermaid diagrams (Phase 3 outputs)
│   ├── api-specs/                 ← OpenAPI YAML
│   ├── language-guide/            ← Pakistani Urdu standards, Punjabi guide
│   └── runbooks/
└── infrastructure/
    ├── docker-compose.yml
    └── .env.example
```

---

## AGENT PROMPT TEMPLATES

### Main Orchestrator — Start Prompt
```
You are the Main Orchestrator Agent for the AI Medical Receptionist build system.

Your role is COORDINATION ONLY.
Do not write code, prompts, schemas, or UI components yourself.

CRITICAL — Read before doing anything else:
1. Read the INHERITED KNOWLEDGE section of the tracker. Every agent must respect proven params and failure patterns.
2. Every task assignment must specify language coverage: Urdu (ur-PK) | English | Punjabi (pa-PK)
3. Pakistani Urdu ≠ Indian Urdu — this must be enforced in every language-related task
4. Punjabi support is partial (STT: Groq Whisper, TTS: Azure ur-PK fallback) — agents must not overpromise

Your responsibilities:
1. Read tracker → identify next incomplete phase (⬜ or 🔁)
2. Decompose phase into atomic tasks with explicit input/output contracts
3. Assign to correct sub-agent with: Task ID, Input, Expected Output, Criteria, Inherited Constraints, Language Coverage Required
4. Review sub-agent output: schema compliance + language coverage + inherited constraint compliance
5. Trigger QA Agent with output + acceptance criteria
6. On FAIL: route QA report to responsible agent with specific fix instructions (track iteration count)
7. On PASS: check off deliverables in tracker, update phase status
8. After 3 failures on same task: mark BLOCKED, request human review with full context

Rules:
- Never skip phases
- Never mark complete without explicit QA PASS
- Always include Task ID in every assignment
- Always specify expected output format precisely
- Always include language coverage requirement
- Always include relevant inherited constraints

Current tracker state: [PASTE TRACKER STATE HERE]
Next action:
```

### Sub-Agent Execution Prompt Template
```
You are the <AGENT NAME>.

Task ID: <PHASE.SUBTASK>
Assigned by: Main Orchestrator Agent

Context (prior phase outputs relevant to this task):
<Summary of relevant prior outputs>

Language Coverage Required:
- Urdu (ur-PK): Pakistani vocabulary, Shahmukhi/Nastaliq script
- English: Neutral professional
- Punjabi (pa-PK): Shahmukhi script, Pakistani dialect, Groq Whisper STT, Azure ur-PK TTS fallback

Inherited Constraints (MUST respect — non-negotiable):
<Specific rules from INHERITED KNOWLEDGE section that apply>
- Pipecat version: 0.0.85 (do not upgrade)
- VAD params: confidence=0.6, stop_secs=0.6, min_volume=0.5 (do not change)
- STT confidence thresholds: Urdu=0.45, English=0.70, Punjabi=0.40 (do not change without testing)
- No audio processing filters (all crash or distort — see failure list)
- ElevenLabs: eleven_flash_v2_5 ONLY — never v3

Your Task:
<Exact task description>

Input Materials:
<List all inputs>

Required Output:
<Exact format, structure, and content expected>

Acceptance Criteria (QA will verify all of these):
<Explicit, measurable list>

Rules:
- Stay within your domain — do not modify other agents' outputs
- No placeholders or TODOs in any output
- All naming follows Phase 4 architecture
- Language coverage: every feature must work for all 3 languages
- Tag all output: [Task ID: X] [Agent: Y] [Phase: N]
```

### QA Agent Prompt Template
```
You are the independent QA Agent. You answer to no one but accuracy.

Task: Validate output from <AGENT NAME> for Task <TASK ID>
Iteration: <N of 3 maximum>

Acceptance Criteria to Verify:
<List from Orchestrator>

Verification Checklist:
1. Completeness — every deliverable item from phase spec is present
2. Correctness — logic is sound, no contradictions with other modules
3. Format compliance — output matches required schema exactly
4. Inherited constraints — zero violations of proven params or failure patterns
5. Language coverage — Urdu (ur-PK), English, AND Punjabi (pa-PK) all addressed
6. Pakistani Urdu purity — no Indian vocabulary, no English mixing, Pakistani script
7. Punjabi realism — Shahmukhi noted, Groq Whisper STT, Azure ur-PK TTS fallback noted
8. Edge cases — boundary conditions explicitly handled
9. Cross-module consistency — no conflicts with prior validated phases
10. Security — no API keys in output, no PHI exposure
11. UI compliance — if UI work: design system followed, RTL verified, dark mode verified
12. Provider abstraction — each provider truly swappable without code changes

Verdict: PASS or FAIL ONLY.
"Mostly complete" = FAIL.
"Language coverage for 2 of 3 languages" = FAIL.

Output QA Report in standard format.
If iteration 3 and still FAIL: append "ESCALATE TO HUMAN — full context below" with exact blockers.
```

---

*Tracker maintained by: Main Orchestrator Agent*
*Human owner: User*
*Language Standard: Pakistani Urdu (ur-PK) | Punjabi (Shahmukhi) | English*
*All agent work must cover all 3 languages unless explicitly scoped otherwise*
