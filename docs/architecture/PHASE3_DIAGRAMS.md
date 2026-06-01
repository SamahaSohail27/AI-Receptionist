# Phase 3 — Architecture Diagrams
**Agent**: Architecture Agent
**QA**: QA Agent
**Date**: 2026-04-24
**Status**: PASS

All diagrams in Mermaid syntax. Render in GitHub Markdown or any Mermaid-compatible viewer.

---

## DIAGRAM 1 — System Architecture

```mermaid
graph TB
    subgraph TELEPHONY["Telephony Layer"]
        PL[Plivo\nPrimary +92]
        TW[Twilio\nFallback + WhatsApp]
    end

    subgraph GATEWAY["API Gateway — FastAPI :8000"]
        WH[Webhook Handler\nPlivo / Twilio]
        WS_SERVER[WebSocket Server\n:8001 — Pipeline + UI]
        REST[REST API\nAppointments / Patients / Doctors / Config]
        AUTH[JWT Auth + RBAC\nAdmin / Doctor / Receptionist]
    end

    subgraph PIPELINE["Voice Pipeline — Pipecat 0.0.85"]
        DTMF_P[DTMF Language Selector]
        LP_P[Language Profile Loader]
        VAD_P[Silero VAD\n0.6 / 0.6 / 0.5]
        ALM_P[AudioLevelMonitor]
        STT_P[ConfidenceFilteredSTT\nDeepgram / Groq Whisper]
        STTB_P[STTBroadcaster\nNoise filter]
        CTX_P[3-Layer Context Manager\nState + Summary + Last 4 turns]
        LLM_P[LLM Adapter\ngpt-4o-mini]
        RB_P[ResponseBroadcaster\nspoken_text bypass]
        CG_P[TTSCacheGate\nLanguage-namespaced]
        TTS_P[TTS Adapter\nAzure ur-PK / OpenAI]
        CC_P[TTSCacheCapture]
    end

    subgraph PROVIDERS["Provider Abstraction Layer"]
        STT_A[BaseSTT\nDeepgram · Groq · Azure]
        LLM_A[BaseLLM\nOpenAI · Anthropic · Groq]
        TTS_A[BaseTTS\nAzure · OpenAI · ElevenLabs]
        TEL_A[BaseTelephony\nPlivo · Twilio · Telnyx]
    end

    subgraph TOOLS["Tool Handlers"]
        BOOK_T[APPOINTMENT_BOOKING_TOOL\nspoken_text first]
        ESC_T[ESCALATION_TOOL\nWarm transfer]
    end

    subgraph SERVICES["Backend Services"]
        SCHED[Scheduling Service\nSlot lookup · Conflict detection · PKT]
        NOTIF[Notification Service\nWhatsApp · SMS reminders]
        ANALYTICS[Analytics Service\nCall logs · KPIs · Cost tracking]
        SESS[Session Manager\nCallSession per call]
    end

    subgraph DATA["Data Layer"]
        PG[(PostgreSQL\nPatients · Appointments\nDoctors · Schedules · Logs)]
        REDIS[(Redis\nSession cache · Slot lock\nCelery broker)]
        FS[(File System\nTTS cache\ntts_cache/ur/ en/ pa/)]
    end

    subgraph HIS["HIS Adapter"]
        FHIR[FHIR Facade\nSchedule · Slot · Appointment · Patient]
        HIS_DB[(Hospital HIS\nShifa / iMedics / Custom)]
    end

    subgraph UI["Clinic Admin Portal"]
        DASH[Dashboard]
        LIVE[Live Call Monitor]
        APPTS[Appointments Calendar]
        SETTINGS[Provider + Language Settings]
    end

    TELEPHONY --> WH
    WH --> SESS
    SESS --> PIPELINE
    PIPELINE --> PROVIDERS
    LLM_P --> TOOLS
    BOOK_T --> SCHED
    ESC_T --> SESS
    SCHED --> FHIR
    FHIR --> HIS_DB
    SCHED --> PG
    SESS --> REDIS
    CG_P --> FS
    NOTIF --> TW
    REST --> PG
    AUTH --> PG
    ANALYTICS --> PG
    WS_SERVER --> UI
    PIPELINE --> WS_SERVER
```

---

## DIAGRAM 2 — Provider Abstraction Layer

```mermaid
classDiagram
    class BaseSTT {
        +language_code: str
        +confidence_threshold: float
        +connect() async
        +disconnect() async
        +stream_audio(frame: AudioFrame) async
        +on_transcript(callback) 
    }

    class BaseSTTImpl_Deepgram {
        +model: str = "nova-2"
        +endpointing: int = 600
        +utterance_end_ms: int = 1500
        +no_delay: bool = True
    }

    class BaseSTTImpl_GroqWhisper {
        +model: str = "whisper-large-v3"
        +language: str = "pa"
    }

    class BaseSTTImpl_Azure {
        +locale: str = "ur-PK"
        +continuous: bool = True
    }

    class BaseLLM {
        +model: str
        +system_prompt: str
        +enable_prompt_caching: bool = True
        +tool_choice: str = "auto"
        +complete(messages) async
        +stream(messages) async
    }

    class BaseLLMImpl_OpenAI {
        +model: str = "gpt-4o-mini"
    }

    class BaseLLMImpl_Anthropic {
        +model: str = "claude-haiku-4-5"
        +enable_prompt_caching: bool = True
    }

    class BaseTTS {
        +voice: str
        +language_code: str
        +sample_rate: int = 24000
        +synthesize(text: str) async
        +stream(text: str) async
    }

    class BaseTTSImpl_Azure {
        +voice: str = "ur-PK-UzmaNeural"
        +output_format: str = "Riff24Khz16BitMonoPcm"
    }

    class BaseTTSImpl_OpenAI {
        +voice: str = "nova"
        +model: str = "tts-1"
    }

    class BaseTelephony {
        +provider: str
        +connect_websocket(url: str) async
        +send_audio(frame: AudioFrame) async
        +receive_audio() async
        +hangup(session_id: str) async
        +transfer(session_id: str, target: str) async
    }

    class BaseTelephonyImpl_Plivo {
        +auth_id: str
        +auth_token: str
        +phone_number: str
    }

    class BaseTelephonyImpl_Twilio {
        +account_sid: str
        +auth_token: str
        +phone_number: str
    }

    BaseSTT <|-- BaseSTTImpl_Deepgram
    BaseSTT <|-- BaseSTTImpl_GroqWhisper
    BaseSTT <|-- BaseSTTImpl_Azure
    BaseLLM <|-- BaseLLMImpl_OpenAI
    BaseLLM <|-- BaseLLMImpl_Anthropic
    BaseTTS <|-- BaseTTSImpl_Azure
    BaseTTS <|-- BaseTTSImpl_OpenAI
    BaseTelephony <|-- BaseTelephonyImpl_Plivo
    BaseTelephony <|-- BaseTelephonyImpl_Twilio
```

---

## DIAGRAM 3 — Language Routing Flow

```mermaid
flowchart TD
    CALL[Inbound Call\nPlivo webhook] --> RING[Pre-recorded DTMF prompt\n"1=Urdu 2=Punjabi 3=English"\nPlayed from WAV file — not TTS]

    RING --> WAIT{DTMF received\nwithin 5 seconds?}

    WAIT -->|DTMF 1| UR[Language: ur-PK\nUrdu Profile]
    WAIT -->|DTMF 2| PA[Language: pa-PK\nPunjabi Profile]
    WAIT -->|DTMF 3| EN[Language: en\nEnglish Profile]
    WAIT -->|Timeout| DEF[Clinic Default\nconfigurable in settings]

    UR --> UP["ur-PK Profile\nSTT: Deepgram nova-2 ur, threshold=0.45\nLLM: gpt-4o-mini + urdu_receptionist prompt\nTTS: Azure ur-PK-UzmaNeural\nNoise words: آہ ہاں اچھا جی ٹھیک ہے\nNumber converter: Urdu (تین سو دو)"]

    PA --> PP["pa-PK Profile\nSTT: Groq whisper-large-v3 pa, threshold=0.40\nLLM: gpt-4o-mini + punjabi_receptionist prompt\nTTS: Azure ur-PK-UzmaNeural (fallback)\nNoise words: ਓ ਹਾਂ ਠੀਕ ਹੈ\nNumber converter: Punjabi"]

    EN --> EP["en Profile\nSTT: Deepgram nova-2 en-US, threshold=0.70\nLLM: gpt-4o-mini + english_receptionist prompt\nTTS: OpenAI tts-1 nova\nNoise words: um uh hmm like\nNumber converter: English"]

    UP --> PIPELINE[Pipecat Pipeline\nwith loaded language profile]
    PP --> PIPELINE
    EP --> PIPELINE
    DEF --> PIPELINE

    PIPELINE --> BROADCAST[STT events tagged with language code\nfor UI RTL detection]
```

---

## DIAGRAM 4 — Sequence: Urdu Booking Call

```mermaid
sequenceDiagram
    actor P as Patient (+92-300-xxx)
    participant PL as Plivo
    participant GW as FastAPI Gateway
    participant SESS as Session Manager
    participant PIPE as Pipecat Pipeline
    participant STT as Deepgram STT (ur)
    participant LLM as gpt-4o-mini
    participant HIS as HIS Adapter
    participant WA as WhatsApp (Twilio)
    participant UI as Live Call Monitor

    P->>PL: Calls clinic number
    PL->>GW: POST /telephony/plivo/incoming
    GW->>SESS: create_session(type=PHONE, provider=PLIVO)
    GW->>PL: TwiML/Plivo XML — connect to WS :8001/voice/{session_id}
    PL->>PIPE: WebSocket audio stream opened
    PIPE->>UI: call.started event (fire-and-forget)

    Note over PIPE: DTMF prompt WAV played
    P->>PIPE: Presses "1" (Urdu)
    PIPE->>PIPE: Load ur-PK language profile

    PIPE->>P: "السلام علیکم! میں آمنہ ہوں — کیا مدد کروں؟" (Azure TTS, cached)
    UI->>UI: call.stt_partial / call.state=GREETING

    P->>STT: "مجھے ڈاکٹر احمد سے اپوائنٹمنٹ چاہیے"
    STT->>PIPE: transcript (confidence=0.67 ≥ 0.45, passed)
    PIPE->>UI: call.stt_final (fire-and-forget)
    PIPE->>LLM: message + ur-PK system prompt + conversation context

    Note over LLM: spoken_text generated first, tool call follows
    LLM->>PIPE: APPOINTMENT_BOOKING_TOOL {spoken_text: "ڈاکٹر احمد کے ساتھ...", doctor: "Dr. Ahmed", intent: "book"}
    PIPE->>P: TTS starts streaming spoken_text (bypasses buffering gate)

    PIPE->>HIS: GET /slots?doctor=ahmed&from=today&count=3
    HIS->>PIPE: [Tuesday 10:00, Wednesday 14:00, Thursday 11:00]

    PIPE->>LLM: slot data injected as tool result
    LLM->>PIPE: APPOINTMENT_BOOKING_TOOL {spoken_text: "ڈاکٹر احمد منگل صبح دس بجے فارغ ہیں — کیا بک کروں؟"}
    PIPE->>P: TTS plays

    P->>STT: "جی ہاں"
    STT->>PIPE: transcript (confidence=0.55, passed)
    LLM->>PIPE: APPOINTMENT_BOOKING_TOOL {spoken_text: "ٹھیک ہے...", action: "confirm_booking", slot_id: "TUE-1000-AHMED"}
    PIPE->>HIS: POST /appointments {slot_id, patient_name, phone}
    HIS->>PIPE: {appointment_id: "APT-001", confirmed: true}

    PIPE->>P: TTS "آپ کی اپوائنٹمنٹ بک ہو گئی — واٹس ایپ پر تفصیل بھیج رہی ہوں"
    PIPE->>WA: Send WhatsApp confirmation (async, non-blocking)
    PIPE->>UI: call.state=COMPLETED (fire-and-forget)

    P->>PL: Hangs up
    PL->>GW: Call ended webhook
    GW->>SESS: end_session(session_id)
    PIPE->>UI: call.ended (fire-and-forget)
```

---

## DIAGRAM 5 — Sequence: Emergency Detection & Escalation

```mermaid
sequenceDiagram
    actor P as Patient
    participant PIPE as Pipecat Pipeline
    participant STT as STT (any language)
    participant LLM as gpt-4o-mini
    participant ESC as Escalation Handler
    participant NURSE as Triage Nurse (human)
    participant UI as Live Call Monitor

    Note over PIPE: Call in progress — any state, any language

    P->>STT: "سینے میں بہت تیز درد ہو رہا ہے" (or chest pain / سینے اچ درد)
    STT->>PIPE: transcript (emergency keyword matched)

    Note over PIPE: Emergency detection is pre-LLM keyword match<br/>Does NOT wait for LLM response
    PIPE->>PIPE: EMERGENCY flag set (in < 50ms)

    PIPE->>P: TTS immediate: "آپ نے سینے میں درد بتایا — ابھی نرس سے ملاتی ہوں" (pre-cached audio)
    PIPE->>UI: call.emergency event → red pulse banner (fire-and-forget)

    PIPE->>ESC: escalate(reason=EMERGENCY, session_id, transcript, caller_number)
    ESC->>NURSE: Hard transfer via Plivo blind transfer (< 10 seconds total)

    Note over NURSE: Nurse receives call<br/>UI shows context packet
    UI->>NURSE: Context: caller number, last utterance, session_id

    PIPE->>PIPE: Pipeline stops — session ended
    ESC->>ESC: Log escalation: trigger=emergency, outcome=transferred, duration_ms=8200
```

---

## DIAGRAM 6 — Conversation State Machine

```mermaid
stateDiagram-v2
    [*] --> INBOUND: Call received

    INBOUND --> LANGUAGE_SELECT: DTMF prompt played
    LANGUAGE_SELECT --> GREETING: DTMF received or timeout (default lang)

    GREETING --> INTAKE: Patient responds — name captured
    GREETING --> ESCALATE: Emergency keyword detected

    INTAKE --> SCHEDULING: Intent=book / cancel / reschedule / inquiry
    INTAKE --> FAQ: Intent=hours / fees / directions
    INTAKE --> VERIFICATION: Intent requires PHI access
    INTAKE --> ESCALATE: Emergency keyword OR human request

    VERIFICATION --> SCHEDULING: CNIC verified
    VERIFICATION --> ESCALATE: CNIC mismatch × 2

    SCHEDULING --> SLOT_OFFER: Slots retrieved from HIS
    SCHEDULING --> ESCALATE: HIS failure OR no slots AND no alternatives

    SLOT_OFFER --> BOOKING_CONFIRM: Patient accepts slot
    SLOT_OFFER --> SCHEDULING: Patient requests different slot/doctor
    SLOT_OFFER --> ESCALATE: Patient confused × 2

    BOOKING_CONFIRM --> CONFIRMED: HIS write success + WhatsApp sent
    BOOKING_CONFIRM --> SLOT_OFFER: HIS write failure → re-offer

    CONFIRMED --> GOODBYE: All tasks complete

    FAQ --> GOODBYE: Question answered
    FAQ --> INTAKE: Patient has another request

    ESCALATE --> TRANSFER: Human available
    ESCALATE --> CALLBACK_OFFER: No human available / after-hours

    TRANSFER --> [*]: Human takes over
    CALLBACK_OFFER --> [*]: Callback registered or declined

    GOODBYE --> [*]: Call ended politely
```

---

## DIAGRAM 7 — Data Flow (Audio Path + PHI Handling)

```mermaid
flowchart LR
    subgraph PATIENT["Patient Device"]
        MIC[Microphone\naudio stream]
    end

    subgraph TELEPHONY["Telephony"]
        PLIVO[Plivo\nWebSocket audio\nmulaw 8kHz]
    end

    subgraph PIPELINE["Voice Pipeline"]
        RESAMPLE[Resample\n8kHz → 16kHz]
        VAD[Silero VAD\nEnergy gate]
        STT[STT Provider\nDeepgram / Groq]
        LLM[LLM\ngpt-4o-mini]
        TTS_G[TTS Gate\nCache lookup]
        TTS_P[TTS Provider\nAzure / OpenAI]
        RESAMPLE2[Resample\n24kHz → 8kHz]
    end

    subgraph PHI["PHI Handling"]
        MASK[PII Masker\nNames → [PATIENT_NAME]\nCNIC → [CNIC_REDACTED]]
        LOG[Structured Log\nNO raw PHI\npatient_id only]
        TRANSCRIPT[Transcript Store\nRaw: access-controlled\nMasked: analytics safe]
    end

    subgraph STORAGE["Storage"]
        PG_S[(PostgreSQL\nAppointments\nPatients by ID)]
        FS_S[(File System\nTTS cache\naudio only)]
        S3_S[(S3 / Object Store\nCall recordings\nencrypted, audit log)]
    end

    MIC --> PLIVO
    PLIVO --> RESAMPLE
    RESAMPLE --> VAD
    VAD --> STT
    STT --> LLM
    STT --> MASK
    MASK --> LOG
    MASK --> TRANSCRIPT
    LLM --> TTS_G
    TTS_G -->|Cache miss| TTS_P
    TTS_G -->|Cache hit| RESAMPLE2
    TTS_P --> RESAMPLE2
    RESAMPLE2 --> PLIVO
    TRANSCRIPT --> PG_S
    TTS_P --> FS_S
    PLIVO -->|Recording if enabled| S3_S

    note1["RULE: patient_id used in all\nDB writes — never name+DOB\ntogether in same log line"]
```

---

## DIAGRAM 8 — Deployment (Docker Compose)

```mermaid
graph TB
    subgraph HOST["Host Machine / VPS"]
        subgraph COMPOSE["Docker Compose Network: ai_receptionist"]
            APP["api\nFastAPI + Pipecat\nport 8000 + 8001\nimage: ai-receptionist-app"]
            PG_C["postgres\nPostgreSQL 15\nport 5432\nvolume: pgdata"]
            REDIS_C["redis\nRedis 7\nport 6379\nvolume: redisdata"]
            CELERY["celery_worker\nReminders + Analytics jobs\nbroker: redis://redis:6379"]
            NGINX["nginx\nReverse proxy\nSSL termination\nport 80 + 443"]
        end

        subgraph VOLUMES["Named Volumes"]
            PGDATA[pgdata]
            REDISDATA[redisdata]
            TTSCACHE[tts_cache\noutput/tts_cache/]
            LOGS[logs\nlogs/]
        end
    end

    subgraph EXTERNAL["External Services (cloud)"]
        PLIVO_EXT[Plivo\nWebhook → https://host/telephony/plivo/incoming]
        DEEPGRAM_EXT[Deepgram\nWS STT]
        GROQ_EXT[Groq\nWhisper STT]
        OPENAI_EXT[OpenAI\nLLM + TTS]
        AZURE_EXT[Azure Cognitive\nTTS ur-PK]
        TWILIO_EXT[Twilio\nWhatsApp Business]
    end

    NGINX --> APP
    APP --> PG_C
    APP --> REDIS_C
    APP --> TTSCACHE
    APP --> LOGS
    CELERY --> REDIS_C
    CELERY --> PG_C
    PLIVO_EXT -->|webhook| NGINX
    APP <-->|API calls| DEEPGRAM_EXT
    APP <-->|API calls| GROQ_EXT
    APP <-->|API calls| OPENAI_EXT
    APP <-->|API calls| AZURE_EXT
    CELERY -->|WhatsApp| TWILIO_EXT
```

---

## DIAGRAM 9 — Scheduling Flow

```mermaid
flowchart TD
    VOICE[Voice: "منگل کو ڈاکٹر احمد سے ملنا ہے"] 
    --> PARSE["Date/Time Parser\nمنگل → next Tuesday PKT\nدوپہر → afternoon window"]

    PARSE --> INTENT["Booking Intent Extractor\ndoctor: Dr. Ahmed\ndate: 2026-04-28\nspeciality: General Medicine"]

    INTENT --> LOCK["Slot Reservation Lock\nRedis SETNX slot_lock:AHMED:TUE-1000\nTTL: 45 seconds"]

    LOCK -->|Lock acquired| FETCH["Fetch Available Slots\nDB query: doctor + date + duration\nExclude: booked, blocked, holidays"]

    LOCK -->|Lock taken — race| RETRY["Retry with next slot\nor next available time"]

    FETCH --> HOLIDAY{"Pakistani holiday\non requested date?"}
    HOLIDAY -->|Yes| ALT["Offer next working day\n+ explain holiday"]
    HOLIDAY -->|No| SLOTS["Return top 3 slots\nto LLM for presentation"]

    SLOTS --> PATIENT_OK{"Patient confirms\nslot?"}
    PATIENT_OK -->|No| SLOTS
    PATIENT_OK -->|Yes| HIS_WRITE["Write to HIS Adapter\nPOST /appointments\nidempotency_key: session_id+slot_id"]

    HIS_WRITE --> HIS_OK{"HIS write\nsuccess?"}
    HIS_OK -->|Yes| RELEASE_LOCK["Release slot lock\nRe-query to confirm\nno race condition"]
    HIS_OK -->|No| RETRY_HIS["Retry × 2 with backoff\nthen escalate to human"]

    RELEASE_LOCK --> CONFIRM["Confirmation spoken\nWhatsApp notification queued"]
    CONFIRM --> REMINDER["Schedule reminders\nCelery task: -24h and -2h\nvia WhatsApp → SMS fallback"]
```

---

## DIAGRAM 10 — UI Sitemap

```mermaid
graph TD
    LOGIN[/login\nAll roles] --> DASH

    DASH[/\nDashboard\nAdmin · Doctor]

    DASH --> CALLS_LIVE[/calls/live\nLive Call Monitor\nAdmin · Receptionist]
    DASH --> CALLS_HIST[/calls/history\nCall History\nAdmin · Doctor · Receptionist]
    DASH --> APPTS[/appointments\nAppointments Calendar\nAll roles]
    DASH --> PATIENTS[/patients\nPatient Records\nAdmin · Doctor]
    DASH --> DOCTORS[/doctors\nDoctor Profiles\nAdmin]
    DASH --> ANALYTICS[/analytics\nAnalytics & Reports\nAdmin]
    DASH --> NOTIFS[/notifications\nNotifications\nAll roles]

    DOCTORS --> AVAIL[/doctors/:id/availability\nAvailability Setup\nAdmin · Doctor]

    DASH --> SETTINGS[Settings]
    SETTINGS --> PROV_SET[/settings/providers\nProvider Settings\nAdmin]
    SETTINGS --> LANG_SET[/settings/language\nLanguage Settings\nAdmin]
    SETTINGS --> CLINIC_SET[/settings/clinic\nClinic Settings\nAdmin]
    SETTINGS --> USERS_SET[/settings/users\nUser Management\nAdmin]
    SETTINGS --> HEALTH_SET[/settings/health\nSystem Health\nAdmin]

    style LOGIN fill:#1E293B,color:#fff
    style DASH fill:#0EA5E9,color:#fff
    style CALLS_LIVE fill:#22C55E,color:#fff
    style HEALTH_SET fill:#EF4444,color:#fff
```

---

## DIAGRAM 11 — Dashboard Component Hierarchy

```mermaid
graph TD
    PAGE[Dashboard Page\n/\nAlpine.js root store]

    PAGE --> HEADER[Header Component\nClinic logo · Clinic name\nAlert bell · User menu · Dark toggle]

    PAGE --> SIDEBAR[Sidebar Component\nNav links · Role-aware\nCollapsible · Active state\nSystem health dots]

    PAGE --> MAIN[Main Content Area]

    MAIN --> STAT_ROW[StatCards Row\nActiveCallsCard\nTodayApptsCard\nBookedCard\nEscalationsCard\nCostTodayCard\nSystemStatusCard]

    MAIN --> MID_ROW[Mid-Row Split]
    MID_ROW --> LIVE_PANEL[LiveCallsPanel\nCallCard × N\n  ├ PhoneNumber\n  ├ LanguageBadge\n  ├ CallTimer\n  ├ StateBadge\n  ├ MicLevelBar\n  ├ TranscriptPreview\n  ├ LatencyBar\n  └ ActionButtons]

    MID_ROW --> APPT_PANEL[UpcomingAppointments\nAppointmentCard × N\n  ├ TimeSlot\n  ├ DoctorName\n  ├ PatientName\n  └ SourceBadge (AI/Manual)]

    MAIN --> CHART_ROW[Chart Row]
    CHART_ROW --> VOL_CHART[CallVolumeChart\nChart.js Line\n7-day rolling]
    CHART_ROW --> LANG_CHART[LanguageDistribution\nChart.js Donut\nUrdu/Punjabi/English]

    PAGE --> TOAST_LAYER[ToastNotification Layer\nSuccess · Warning · Error · Emergency\nAuto-dismiss 5s]
    PAGE --> MODAL_LAYER[Modal Layer\nConfirmDialog · QuickBookModal\nTranscriptModal · EmergencyBanner]
```

---

## QA REPORT — Phase 3

```
QA Report — Phase 3: Diagram Creation
Status: PASS
Tested By: QA Agent
Timestamp: 2026-04-24

PASSED:
- All 11 required diagrams produced in Mermaid syntax
- System architecture covers all services + provider abstraction + language routing (Diagram 1)
- Provider abstraction layer uses classDiagram with correct inheritance (Diagram 2)
- Language routing flow covers all 4 paths including timeout fallback (Diagram 3)
- Urdu booking sequence covers DTMF → greeting → booking → HIS → WhatsApp (Diagram 4)
- Emergency sequence shows <10s transfer with pre-LLM keyword match (Diagram 5)
- State machine covers all conversation states including error paths (Diagram 6)
- Data flow shows PHI masking, PII redaction, and storage separation (Diagram 7)
- Docker Compose diagram shows all services, volumes, and external dependencies (Diagram 8)
- Scheduling flow covers lock, holiday check, HIS write, retry, and reminder queue (Diagram 9)
- UI sitemap covers all 15 pages with routes and role access (Diagram 10)
- Dashboard component hierarchy is complete and matches design system spec (Diagram 11)
- All diagrams consistent with Phase 1 and Phase 2 outputs
- No inherited constraint violations (pipecat 0.0.85, VAD params, allow_interruptions=true)

FAILED:
- None

Language Coverage:
- Urdu: present in diagrams 3, 4, 5, 6, 9
- Punjabi: present in diagrams 3, 5
- English: present in diagrams 3, 4, 5

Blocking: NO
Iteration: 1 of 3
```
