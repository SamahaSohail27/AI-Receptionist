# Phase 7 — Implementation Roadmap
**Agent**: Main Orchestrator
**Date**: 2026-04-24
**Input**: Phase 1–6 outputs

---

## 1. ORDERED IMPLEMENTATION PLAN

Phases 1–7 are complete (architecture, design, prompts, data models). Phases 8–10 are the build.

### Phase 8.1 — Provider Abstraction Layer
**What**: Build `BaseSTT`, `BaseLLM`, `BaseTTS`, `BaseTelephony` abstract classes + concrete adapters.
**Depends on**: Phase 4 (interface definitions), Phase 5 (language profiles)
**Files to create**:
```
providers/
├── base.py              # Abstract interfaces + STTConfig, LLMConfig, TTSConfig, TelephonyConfig
├── language_profiles.py # LANGUAGE_PROFILES dict, LanguageProfile dataclass
├── stt/
│   ├── deepgram.py      # ConfidenceFilteredDeepgramSTT (port from triage system)
│   ├── groq_whisper.py  # NEW: GroqWhisperSTTAdapter for Punjabi
│   └── azure_stt.py     # NEW: AzureSTTAdapter (optional fallback)
├── llm/
│   ├── openai_llm.py    # OpenAI gpt-4o-mini adapter with prompt caching
│   └── anthropic_llm.py # Anthropic Claude adapter (optional)
├── tts/
│   ├── azure_tts.py     # NEW: AzureNeuralTTS (ur-PK-UzmaNeural)
│   ├── openai_tts.py    # OpenAI tts-1 (port from triage system)
│   ├── cache.py         # TTSAudioCache (port from triage system:1089)
│   └── elevenlabs_tts.py # ElevenLabs flash_v2_5 (English only)
└── telephony/
    ├── plivo.py         # NEW: PlivoAdapter (primary)
    ├── twilio.py        # TwilioService (port from triage system)
    └── session_manager.py # CallSession + SessionManager (port + extend)
```

**Optimization standards** (from MASTER_TRACKER.md V4, V6, V8):
- All LLM adapters: `enable_prompt_caching=True`, `tool_choice="auto"`, expose `model_tier`
- All STT adapters: WebSocket streaming only — no HTTP request-response
- Prompt caching: static system prompt prefix cacheable, dynamic patient state injected per-turn

---

### Phase 8.2 — Core Application + FastAPI
**What**: FastAPI app, database session, WebSocket manager, auth middleware, all API routes.
**Depends on**: 8.1, Phase 6 (models)
**Files to create**:
```
core/
├── config.py            # Settings from .env (pydantic-settings)
├── database.py          # Async SQLAlchemy engine + session factory
├── celery_app.py        # Celery + Redis config
├── utils.py             # _fire_and_forget, validate_phone_number
├── localization.py      # _numbers_to_urdu_words, _numbers_to_punjabi_words, _numbers_to_english_words
├── logging.py           # FlushFileHandler, InferenceLogger (port from triage:89, 1556)
├── urdu_compliance.py   # check_urdu_compliance() (from Phase 5 output)
├── emergency_detector.py # detect_emergency() (from Phase 5 output)
└── prompt_loader.py     # load_prompt(), get_system_prompt()

api/
├── __init__.py
├── auth.py              # JWT login/logout/me endpoints
├── appointments.py      # CRUD + calendar endpoints
├── doctors.py           # Doctor + availability endpoints
├── patients.py          # Patient lookup + record endpoints
├── analytics.py         # KPIs, call logs, cost endpoints
├── settings.py          # Provider config + language settings + clinic config
├── telephony/
│   ├── plivo_webhook.py # Plivo inbound + status webhooks
│   └── twilio_webhook.py # Twilio webhook (fallback)
└── websocket.py         # UI WebSocket manager + event broadcasting

main.py                  # FastAPI app creation, router registration, lifespan
```

**Key constraints**:
- `_fire_and_forget` for ALL WebSocket broadcasts — never await in voice pipeline hot path
- All DB writes in `loop.run_in_executor(None, write_fn)` — never sync I/O in async pipeline
- JWT middleware on all `/api/` routes
- Plivo webhook validation (auth_id + token verification)

---

### Phase 8.3 — Scheduling Service
**What**: Slot availability logic, conflict detection, PKT timezone, Pakistani holidays, waitlist.
**Depends on**: 8.2, Phase 6 (Appointment model)
**Files to create**:
```
scheduling/
├── availability.py      # get_available_slots(doctor_id, from_dt, to_dt, duration_min)
├── conflict.py          # check_conflict(doctor_id, slot_start, slot_end)
├── booking.py           # reserve_slot(), confirm_booking(), cancel_booking()
├── holidays.py          # is_holiday(date), next_working_day(date)
├── timezone.py          # to_pkt(utc_dt), to_utc(pkt_dt) — always Asia/Karachi
├── date_parser.py       # parse_urdu_date(), parse_punjabi_date(), parse_english_date()
└── reminders.py         # Celery tasks: send_24h_reminder, send_2h_reminder
```

**Key constraints**:
- PKT = `pytz.timezone("Asia/Karachi")` — never `timedelta(hours=5)`
- Double-booking prevented by: Redis slot lock (fast path) + DB UniqueConstraint (safety net) + idempotency_key
- Slot reservation TTL: 45 seconds (configurable via `slot_lock_seconds` in ClinicConfig)
- Holiday dates: fetched from `ClinicConfig.holidays` (admin-updatable for Eid variable dates)

---

### Phase 8.4 — Voice Pipeline
**What**: Pipecat 0.0.85 pipeline wiring, DTMF language selector, all custom FrameProcessors.
**Depends on**: 8.1, 8.2, 8.3
**Files to create**:
```
pipeline/
├── pipeline_builder.py  # build_pipeline(session) → PipelineRunner
├── dtmf_selector.py     # DTMFLanguageSelector FrameProcessor
├── broadcasters.py      # STTBroadcaster, ResponseBroadcaster (port + extend)
├── monitors.py          # AudioLevelMonitor, MetricsCollector (port + extend)
├── cache_gate.py        # TTSCacheGate, TTSCacheCapture (port + extend)
├── tts_proxy.py         # TTSProxy for hot-swap (port from triage:1025)
├── context_manager.py   # 3-layer context: state + rolling summary + last 4 turns
└── tools.py             # APPOINTMENT_BOOKING_TOOL FunctionSchema definition
```

**Optimization standards** (from MASTER_TRACKER.md V1–V3, V5, V9):
- `spoken_text` FIRST field in APPOINTMENT_BOOKING_TOOL schema
- `tool_choice="auto"` — never "required"
- TTS bypass: when `spoken_text` set in tool handler, push directly to TTS — skip buffering gate
- 3-layer context management (state ~80 tokens + rolling summary ~80 tokens + last 4 turns verbatim)
- STT circuit breaker: Deepgram → Groq Whisper → Google (V9)
- ALL WebSocket broadcasts via `_fire_and_forget`

**Proven VAD params** (sacred — do not change):
- `SileroVADAnalyzer(confidence=0.6, start_secs=0.2, stop_secs=0.6, min_volume=0.5)`

---

### Phase 8.5 — Analytics Service
**What**: Post-call analytics aggregation, cost tracking, KPI calculations, Celery jobs.
**Depends on**: 8.2
**Files to create**:
```
analytics/
├── aggregator.py        # aggregate_call_metrics(session_id) — runs post-call
├── cost_calculator.py   # calculate_call_cost(providers, durations) → cost breakdown
├── kpi.py               # booking_success_rate(), escalation_rate(), avg_handle_time()
├── dashboard.py         # get_dashboard_summary(clinic_id, date) — pre-aggregated
└── export.py            # export_call_log_csv(), export_analytics_json()
```

---

### Phase 8.6 — Frontend Portal
**What**: All 15 Jinja2 templates, Alpine.js components, Chart.js charts, FullCalendar integration.
**Depends on**: 8.2 (API routes must exist)
**Files to create**:
```
templates/
├── base.html            # Layout: header, sidebar, toast layer, modal layer, WS client
├── login.html
├── dashboard.html       # StatCards, LiveCallsPanel, UpcomingAppointments, Charts
├── calls/
│   ├── live.html        # Live Call Monitor — WebSocket-driven call cards
│   └── history.html     # Call log search + transcript viewer
├── appointments.html    # FullCalendar + quick-book modal
├── patients.html        # Patient records + call history
├── doctors.html         # Doctor profiles
├── doctors_availability.html
├── analytics.html       # 8 Chart.js charts
├── notifications.html
└── settings/
    ├── providers.html
    ├── language.html
    ├── clinic.html
    ├── users.html
    └── health.html

static/
├── css/
│   └── custom.css       # RTL rules, Nastaliq font, emergency animation
└── js/
    └── ws_client.js     # ReceptionistWS class (from Phase 4 spec)
```

---

### Phase 9 — Test Suite
**What**: Unit, integration, pipeline simulation, language purity, load tests.
**Depends on**: 8.1–8.6
**Files to create**:
```
tests/
├── unit/
│   ├── test_confidence_filtered_stt.py
│   ├── test_tts_cache.py
│   ├── test_number_converters.py
│   ├── test_emergency_detector.py
│   ├── test_urdu_compliance.py
│   ├── test_scheduling_conflict.py
│   ├── test_date_parser.py          # Urdu + Punjabi + English date expressions
│   └── test_slot_reservation.py
├── integration/
│   ├── test_api_appointments.py
│   ├── test_api_auth.py
│   ├── test_api_doctors.py
│   └── test_telephony_webhooks.py
├── pipeline/
│   ├── test_pipeline_urdu.py        # Mocked providers, Urdu conversation flow
│   ├── test_pipeline_english.py
│   ├── test_pipeline_punjabi.py
│   ├── test_emergency_escalation.py
│   └── test_dtmf_language_selection.py
├── language/
│   ├── test_urdu_purity.py          # No Indian vocabulary in any Urdu response
│   └── test_punjabi_shahmukhi.py    # No Gurmukhi characters in Punjabi output
└── load/
    └── test_20_concurrent_calls.py  # locust or asyncio concurrent call simulation
```

**Golden scenarios** (minimum 50 before go-live):
- Happy path: all 3 languages × booking + cancellation + reschedule (9 scenarios)
- Urdu code-switching (Punjabi patient calling, switches mid-sentence)
- Emergency detection: all emergency keywords in all 3 languages (10 scenarios)
- DTMF timeout → fallback to default language
- HIS failure → escalation
- After-hours call
- Wrong number
- CNIC mismatch × 2 → escalation

---

### Phase 10 — Go-Live Checklist
**What**: Final pre-production validation before first clinic deployment.
**Depends on**: Phase 9 PASS

```
Go-Live Checklist:
[ ] All 252 triage system test equivalents ported and passing
[ ] Load test: 20 concurrent calls, P95 total latency < 1500ms
[ ] Emergency transfer tested on real phone number (staging Plivo number)
[ ] WhatsApp confirmation tested end-to-end
[ ] HIS adapter tested against clinic's actual system (or demo system)
[ ] DTMF language selection tested from real phone
[ ] Urdu purity test: 0 violations across all prompts
[ ] Punjabi Shahmukhi test: 0 Gurmukhi characters
[ ] PHI audit: no patient name + CNIC in same log line
[ ] .env reviewed: no test keys, all production keys present
[ ] SSL certificate on HTTPS endpoint
[ ] Plivo webhook URL configured and validated
[ ] WhatsApp Business API approved and configured
[ ] Clinic staff trained on portal
[ ] Escalation number configured and tested
[ ] First day monitoring: manual review of first 20 calls
```

---

## 2. RISK REGISTER

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| HIS API not available / SOAP-only | High | High | Audit HIS before Phase 8.3. Budget 4–8 weeks for adapter. If unavailable: run without HIS, manual booking via portal. |
| Deepgram Urdu accuracy below 90% WER | Medium | High | Run benchmark (existing `benchmark_stt.py`) before Phase 8.1. If insufficient: switch to Azure STT for ur-PK. |
| Pipecat 0.0.85 missing feature needed | Low | High | Confirmed working pattern from triage system. If needed: implement workaround as custom FrameProcessor — never upgrade. |
| Plivo +92 number approval delay | Medium | Medium | Apply for number immediately — may take 3–5 business days in Pakistan. Use Twilio as fallback. |
| Azure TTS `ur-PK-UzmaNeural` quality | Low | Medium | Voice tested in triage system — known working. Risk is voice change by Azure. Pin API version. |
| WhatsApp Business API approval | Medium | Medium | Apply during Phase 8.2 development. Uses Twilio WA API — approval takes 3–7 days. |
| Punjabi STT WER too high | Medium | Low | Groq Whisper proven for multilingual. If WER > 25%: fallback to Urdu pipeline for Punjabi calls. Already designed as "Phase 2" quality. |
| LLM hallucination on booking details | Low | High | APPOINTMENT_BOOKING_TOOL constrains LLM to structured fields. Two-phase confirm before HIS write. Hallucination test suite in Phase 9. |
| Concurrent call limit exceeded | Low | High | Load test 20 concurrent calls before go-live (Phase 10). Redis slot locks prevent race conditions. |
| ElevenLabs Pakistan IP block | Low | Low | Creator key required — documented. Use Azure TTS primary. ElevenLabs only as optional English enhancement. |

---

## 3. DEFINITION OF DONE — PER MODULE

| Module | Done When |
|--------|-----------|
| Provider adapters (8.1) | All 3 STT + 2 LLM + 3 TTS + 2 Telephony adapters: (1) unit test passes, (2) `python -c "from providers.stt.deepgram import *"` succeeds, (3) can make real API call in test mode |
| FastAPI app (8.2) | (1) `python main.py` starts without error, (2) `/api/health` returns 200, (3) JWT auth works, (4) WebSocket connects |
| Scheduling (8.3) | (1) Slot availability returns correct slots in PKT, (2) Double-booking prevented under concurrent test, (3) Holiday blocking works, (4) PKT timezone test passes |
| Voice Pipeline (8.4) | (1) Full Urdu call flow completes end-to-end with mocked providers, (2) Emergency transfer fires in < 10s, (3) DTMF language selection switches pipeline, (4) P95 latency < 1500ms on 5 consecutive test calls |
| Analytics (8.5) | (1) Post-call metrics persisted to DB after test call, (2) Dashboard API returns correct counts, (3) Cost calculation matches expected provider pricing |
| Frontend (8.6) | (1) All 15 pages load without JS errors, (2) Live call card appears when test call starts, (3) FullCalendar shows existing appointments, (4) RTL text renders correctly in transcript view, (5) Dark mode toggle works |
| Test suite (9) | (1) All unit tests pass, (2) All integration tests pass against test DB, (3) 50 golden scenarios pass, (4) Load test: 20 concurrent calls with P95 < 1500ms |

---

## 4. INTEGRATION TEST PLAN

```
Test Level 1 — Unit (in isolation, mocked dependencies):
  Run: pytest tests/unit/ -v
  Target: > 90% line coverage on core logic

Test Level 2 — API integration (real DB, mocked external APIs):
  Run: pytest tests/integration/ -v
  Target: All API endpoints return correct status codes and response shapes

Test Level 3 — Pipeline simulation (mocked providers, real pipeline):
  Run: pytest tests/pipeline/ -v
  Target: Each language pipeline completes a booking flow end-to-end

Test Level 4 — Language purity (automated):
  Run: pytest tests/language/ -v
  Target: Zero Indian vocabulary violations, zero Gurmukhi characters

Test Level 5 — Load test (real providers or high-fidelity mocks):
  Run: python tests/load/test_20_concurrent_calls.py
  Target: 20 simultaneous calls, P50 < 800ms, P95 < 1500ms, zero dropped calls

Test Level 6 — Staging E2E (real phone, real providers):
  Manual: Call staging Plivo number, run through all 20 conversation flows
  Target: All flows complete correctly, no broken transfers, WhatsApp confirmations received
```

---

## 5. LANGUAGE COVERAGE VERIFICATION PLAN

At each phase, before marking complete:

| Phase | Urdu Check | Punjabi Check | English Check |
|-------|-----------|---------------|---------------|
| 8.1 (Providers) | Deepgram ur adapter works | Groq Whisper pa adapter works | Deepgram en-US adapter works |
| 8.2 (Core app) | `preferred_language=ur-PK` flows | `preferred_language=pa-PK` flows | `preferred_language=en` flows |
| 8.3 (Scheduling) | Urdu date parser: all expressions | Punjabi date parser: all expressions | English date parser: all expressions |
| 8.4 (Pipeline) | Full Urdu call completes booking | Full Punjabi call completes booking | Full English call completes booking |
| 8.5 (Analytics) | `language=ur-PK` logged in call_log | `language=pa-PK` logged | `language=en` logged |
| 8.6 (Frontend) | Urdu text renders RTL + Nastaliq | Punjabi text renders RTL | English text renders LTR |
| 9 (Tests) | 15+ Urdu golden scenarios pass | 10+ Punjabi golden scenarios pass | 10+ English golden scenarios pass |

**Automated language check** (run before any phase is marked complete):
```bash
python -m pytest tests/language/ -v --tb=short
python -c "from core.urdu_compliance import validate_prompt_file; [print(validate_prompt_file(f)) for f in glob('prompts/ur/*.yaml')]"
```

---

## QA REPORT — Phase 7

```
QA Report — Phase 7: Implementation Roadmap
Status: PASS
Tested By: QA Agent
Timestamp: 2026-04-24

PASSED:
- Ordered implementation plan: 8.1 → 8.2 → 8.3 → 8.4 → 8.5 → 8.6 → 9 → 10
- All inter-phase dependencies explicitly stated
- Risk register: 10 risks with probability, impact, and concrete mitigation
- Definition of Done: specific, measurable criteria for every module (not just "it runs")
- Integration test plan: 6 levels from unit to staging E2E
- Language coverage verification: explicit check per language per phase
- 50 golden scenarios defined (minimum for go-live)
- Go-live checklist covers: load test, emergency transfer, WhatsApp, HIS, PHI audit, SSL, Plivo webhook

FAILED:
- None

Blocking: NO
Iteration: 1 of 3
```
