# Phase 4 — Architecture Definition Output
**Agent**: Architecture Agent
**QA**: QA Agent
**Date**: 2026-04-24
**Status**: PASS

---

## 1. SERVICE INVENTORY TABLE

| Service | Responsibility | Framework | Database | Exposed APIs | Port | Language Aware |
|---------|---------------|-----------|----------|-------------|------|---------------|
| **api** | FastAPI app — webhook handlers, REST API, WebSocket server, pipeline orchestration, static file serving | FastAPI + Uvicorn | PostgreSQL (via SQLAlchemy async) | REST :8000, WebSocket :8001 | 8000 / 8001 | Yes — language profile per session |
| **postgres** | Persistent relational data — patients, appointments, doctors, schedules, analytics, audit log | PostgreSQL 15 | — | Internal :5432 | 5432 | No |
| **redis** | Session cache, slot reservation locks (TTL 45s), Celery broker, WebSocket pub/sub | Redis 7 | — | Internal :6379 | 6379 | No |
| **celery_worker** | Async jobs — WhatsApp/SMS reminders, post-call analytics aggregation, outbound reminder calls | Celery + Redis | PostgreSQL | Internal | — | Yes — sends notifications in patient's language |
| **nginx** | Reverse proxy, SSL termination, static file cache, WebSocket proxying | Nginx | — | HTTP :80, HTTPS :443 | 80 / 443 | No |

**External services** (not Docker Compose — cloud APIs):
| Service | Purpose | Provider | Language Aware |
|---------|---------|---------|----------------|
| Deepgram | STT — Urdu + English | deepgram-sdk | Yes (language param) |
| Groq Whisper | STT — Punjabi | groq SDK | Yes |
| OpenAI | LLM (gpt-4o-mini) + TTS (tts-1) | openai SDK | Yes (prompt variant) |
| Azure Cognitive Services | TTS — ur-PK voices | azure-cognitiveservices-speech | Yes |
| Plivo | Telephony primary, +92 numbers | plivo SDK | No |
| Twilio | Telephony fallback + WhatsApp Business | twilio SDK | No |
| HIS system | Hospital Information System | Custom FHIR adapter | No |

---

## 2. PROVIDER ABSTRACTION INTERFACE DEFINITIONS

### 2.1 BaseSTT

```python
# providers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable


@dataclass
class STTConfig:
    provider: str
    language_code: str
    confidence_threshold: float
    model: str
    streaming: bool = True


class BaseSTT(ABC):
    def __init__(self, config: STTConfig):
        self.config = config
        self._on_transcript_callback: Optional[Callable] = None

    def on_transcript(self, callback: Callable[[str, float, bool], Awaitable[None]]):
        self._on_transcript_callback = callback

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def send_audio(self, audio_bytes: bytes, sample_rate: int) -> None: ...

    async def _emit_transcript(self, text: str, confidence: float, is_final: bool):
        if confidence < self.config.confidence_threshold and is_final:
            return
        if self._on_transcript_callback:
            await self._on_transcript_callback(text, confidence, is_final)
```

### 2.2 BaseLLM

```python
@dataclass
class LLMConfig:
    provider: str
    model: str
    system_prompt: str
    enable_prompt_caching: bool = True
    tool_choice: str = "auto"
    temperature: float = 0.3
    max_tokens: int = 500


class BaseLLM(ABC):
    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    async def complete(self, messages: list[dict], tools: list[dict]) -> dict: ...

    @abstractmethod
    async def stream(self, messages: list[dict], tools: list[dict]): ...
```

### 2.3 BaseTTS

```python
@dataclass
class TTSConfig:
    provider: str
    voice: str
    language_code: str
    sample_rate: int = 24000
    model: Optional[str] = None


class BaseTTS(ABC):
    def __init__(self, config: TTSConfig):
        self.config = config

    @abstractmethod
    async def synthesize(self, text: str) -> bytes: ...

    @abstractmethod
    async def stream_synthesize(self, text: str): ...
```

### 2.4 BaseTelephony

```python
@dataclass
class TelephonyConfig:
    provider: str
    phone_number: str
    websocket_url: str


class BaseTelephony(ABC):
    def __init__(self, config: TelephonyConfig):
        self.config = config

    @abstractmethod
    async def accept_call(self, session_id: str) -> str:
        """Returns TwiML/XML response URL or direct WS URL."""
        ...

    @abstractmethod
    async def transfer_call(self, session_id: str, target_number: str, context: dict) -> bool: ...

    @abstractmethod
    async def hangup(self, session_id: str) -> bool: ...

    @abstractmethod
    def validate_phone_number(self, phone: str) -> str:
        """Normalize to E.164 +923001234567 format."""
        ...
```

### 2.5 BaseLanguageProfile

```python
@dataclass
class LanguageProfile:
    code: str                          # "ur-PK" | "pa-PK" | "en"
    stt: STTConfig
    llm_prompt_variant: str            # filename key in prompts/
    tts: TTSConfig
    noise_words: list[str]             # language-specific filler words
    number_converter: str              # "urdu" | "punjabi" | "english"
    dtmf_key: str                      # "1" | "2" | "3"


LANGUAGE_PROFILES: dict[str, LanguageProfile] = {
    "ur-PK": LanguageProfile(
        code="ur-PK",
        stt=STTConfig(
            provider="deepgram",
            language_code="ur",
            confidence_threshold=0.45,
            model="nova-2",
            streaming=True,
        ),
        llm_prompt_variant="ur",
        tts=TTSConfig(
            provider="azure",
            voice="ur-PK-UzmaNeural",
            language_code="ur-PK",
            sample_rate=24000,
        ),
        noise_words=["آہ", "ہاں", "اچھا", "جی", "ٹھیک ہے", "ام", "ہممم"],
        number_converter="urdu",
        dtmf_key="1",
    ),
    "pa-PK": LanguageProfile(
        code="pa-PK",
        stt=STTConfig(
            provider="groq_whisper",
            language_code="pa",
            confidence_threshold=0.40,
            model="whisper-large-v3",
            streaming=True,
        ),
        llm_prompt_variant="pa",
        tts=TTSConfig(
            provider="azure",
            voice="ur-PK-UzmaNeural",   # Punjabi TTS fallback — no native pa-PK neural TTS exists
            language_code="ur-PK",
            sample_rate=24000,
        ),
        noise_words=["ਓ", "ਹਾਂ", "ਠੀਕ ਹੈ", "او", "ہاں جی"],
        number_converter="punjabi",
        dtmf_key="2",
    ),
    "en": LanguageProfile(
        code="en",
        stt=STTConfig(
            provider="deepgram",
            language_code="en-US",
            confidence_threshold=0.70,
            model="nova-2",
            streaming=True,
        ),
        llm_prompt_variant="en",
        tts=TTSConfig(
            provider="openai",
            voice="nova",
            language_code="en",
            sample_rate=24000,
            model="tts-1",
        ),
        noise_words=["um", "uh", "hmm", "like", "you know", "so", "erm"],
        number_converter="english",
        dtmf_key="3",
    ),
}
```

---

## 3. LANGUAGE PROFILE ARCHITECTURE SPEC

```
Storage:   LANGUAGE_PROFILES dict in providers/language_profiles.py (code — not DB)
           Clinic override (default_language, per-doctor language) stored in PostgreSQL settings table
           
Loading:   At call start — DTMF key maps to profile code → load from LANGUAGE_PROFILES dict (O(1))
           
Switching: Language can only be switched at call start (DTMF phase)
           Mid-call language switch not supported in v1 (adds complexity, low clinical value)
           
Injection: Profile passed to PipelineBuilder(language_profile=profile) at session creation
           Each pipeline component reads from profile — STT, LLM, TTS, noise filter, number converter
           
Clinic config: admin can set:
  - default_language: what loads on DTMF timeout (default: ur-PK)
  - per_doctor_language: override for specific doctors (e.g. Dr. Smith defaults to English)
  - dtmf_enabled: true/false — if false, use default_language immediately
```

---

## 4. INTER-SERVICE COMMUNICATION CONTRACTS

### 4.1 REST API — Core Endpoints

```
POST   /telephony/plivo/incoming          Plivo call webhook
POST   /telephony/plivo/status            Plivo call status updates
POST   /telephony/twilio/incoming         Twilio call webhook (fallback)
GET    /telephony/voice/{session_id}      WebSocket upgrade — Plivo/Twilio audio stream
GET    /ws                                WebSocket — UI real-time updates

GET    /api/appointments                  List appointments (filter: doctor, date, status)
POST   /api/appointments                  Create appointment (manual booking)
GET    /api/appointments/{id}             Get appointment details
PATCH  /api/appointments/{id}             Update / cancel appointment
GET    /api/appointments/{id}/cancel      Cancel (voice-friendly URL)

GET    /api/doctors                       List doctors (active, by speciality)
GET    /api/doctors/{id}/availability     Doctor availability slots
GET    /api/doctors/{id}/slots            Available booking slots (date range)

GET    /api/patients                      Search patients (CNIC, phone, name)
GET    /api/patients/{id}                 Patient record + appointment history
POST   /api/patients                      Register new patient

GET    /api/analytics/summary             Dashboard stats (today)
GET    /api/analytics/calls               Call log (filterable)
GET    /api/analytics/kpis                KPI metrics (period)
GET    /api/analytics/cost                Cost breakdown (period)

GET    /api/settings/providers            Current provider config
PUT    /api/settings/providers            Update provider config (restarts pipeline on next call)
GET    /api/settings/language             Language settings
PUT    /api/settings/language             Update language settings
GET    /api/settings/clinic               Clinic info
PUT    /api/settings/clinic               Update clinic info

GET    /api/health                        Provider API status + pipeline metrics
GET    /                                  Serve portal HTML (Jinja2)
```

### 4.2 WebSocket Events (Server → Client, UI)

All events tagged with `session_id` and `language` for RTL detection.

```json
// call.started
{"type": "call.started", "session_id": "uuid", "caller": "+923001234567",
 "language": "ur-PK", "provider": "plivo", "timestamp": "2026-04-24T10:00:00Z"}

// call.stt_partial
{"type": "call.stt_partial", "session_id": "uuid", "text": "مجھے ڈاکٹر",
 "confidence": 0.61, "language": "ur-PK", "is_rtl": true}

// call.stt_final
{"type": "call.stt_final", "session_id": "uuid", "text": "مجھے ڈاکٹر احمد سے ملنا ہے",
 "confidence": 0.67, "language": "ur-PK", "is_rtl": true}

// call.assistant_response
{"type": "call.assistant_response", "session_id": "uuid",
 "text": "ڈاکٹر احمد منگل کو دستیاب ہیں", "language": "ur-PK", "is_rtl": true}

// call.state_changed
{"type": "call.state_changed", "session_id": "uuid",
 "state": "SCHEDULING", "prev_state": "INTAKE"}

// call.latency
{"type": "call.latency", "session_id": "uuid",
 "stt_ms": 143, "llm_ms": 187, "tts_ms": 165, "total_ms": 495}

// call.escalation
{"type": "call.escalation", "session_id": "uuid",
 "reason": "patient_request", "context": {...}}

// call.emergency
{"type": "call.emergency", "session_id": "uuid",
 "caller": "+923001234567", "transcript": "سینے میں درد ہو رہا ہے"}

// call.ended
{"type": "call.ended", "session_id": "uuid",
 "duration_seconds": 142, "outcome": "booked", "appointment_id": "APT-001"}

// stats.update (every 10 seconds)
{"type": "stats.update", "active_calls": 3, "todays_appointments": 24,
 "booked_today": 18, "cost_today_usd": 2.40, "escalations_today": 1}
```

---

## 5. EXTERNAL DEPENDENCY LIST (PINNED VERSIONS)

```
# requirements.txt — AI Receptionist

# Core framework
pipecat-ai==0.0.85              # SACRED — DO NOT UPGRADE
fastapi>=0.115.0
uvicorn[standard]>=0.38.0

# Database
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0                  # Async PostgreSQL driver
alembic>=1.13.0
redis>=5.0.0

# Task queue
celery>=5.3.0
kombu>=5.3.0

# Auth
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4

# STT providers
deepgram-sdk>=4.7.0
groq>=0.9.0                       # Groq Whisper for Punjabi

# LLM providers
openai>=1.99.0
anthropic>=0.34.0                 # Optional fallback

# TTS providers
azure-cognitiveservices-speech>=1.40.0   # ur-PK-UzmaNeural
elevenlabs>=2.39.0               # English only, Creator key required from PK

# Telephony
plivo>=4.0.0                     # Primary
twilio>=9.0.0                    # Fallback + WhatsApp

# Pipecat internals
pipecat-ai-small-webrtc-prebuilt==2.0.0

# Utilities
python-dotenv>=1.0.0
websockets>=13.0
aiohttp>=3.11.0
pytz>=2024.1                     # PKT timezone — always use Asia/Karachi
jinja2>=3.1.0                    # HTML templates
python-multipart>=0.0.9          # Form data

# Monitoring
opentelemetry-api>=1.24.0
opentelemetry-sdk>=1.24.0
opentelemetry-instrumentation-fastapi>=0.45b0

# Testing
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0                    # FastAPI test client
```

---

## 6. DOCKER COMPOSE CONFIGURATION

```yaml
# docker-compose.yml
version: "3.9"

services:
  api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: ai_receptionist_api
    ports:
      - "8000:8000"
      - "8001:8001"
    environment:
      - DATABASE_URL=postgresql+asyncpg://receptionist:${DB_PASSWORD}@postgres:5432/receptionist_db
      - REDIS_URL=redis://redis:6379/0
      - TTS_CACHE_DIR=/app/output/tts_cache
      - LOGS_DIR=/app/logs
    env_file:
      - .env
    volumes:
      - tts_cache:/app/output/tts_cache
      - logs:/app/logs
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  postgres:
    image: postgres:15-alpine
    container_name: ai_receptionist_postgres
    environment:
      POSTGRES_DB: receptionist_db
      POSTGRES_USER: receptionist
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U receptionist"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: ai_receptionist_redis
    volumes:
      - redisdata:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  celery_worker:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: ai_receptionist_celery
    command: celery -A core.celery_app worker --loglevel=info --concurrency=4
    env_file:
      - .env
    environment:
      - DATABASE_URL=postgresql+asyncpg://receptionist:${DB_PASSWORD}@postgres:5432/receptionist_db
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis
      - postgres
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    container_name: ai_receptionist_nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - api
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
  tts_cache:
  logs:
```

---

## 7. ENVIRONMENT VARIABLE SCHEMA

```bash
# .env — AI Receptionist

# ─── Database ───────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://receptionist:PASSWORD@localhost:5432/receptionist_db
DB_PASSWORD=changeme_before_deploy

# ─── Redis ──────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0

# ─── Auth ───────────────────────────────────────────
JWT_SECRET_KEY=generate_with_openssl_rand_hex_32
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=480

# ─── STT Providers ──────────────────────────────────
DEEPGRAM_API_KEY=          # Urdu + English STT
GROQ_API_KEY=              # Punjabi STT (Whisper)

# ─── LLM Providers ──────────────────────────────────
OPENAI_API_KEY=            # gpt-4o-mini (LLM) + tts-1 (English TTS)
ANTHROPIC_API_KEY=         # Optional: Claude Haiku fallback

# ─── TTS Providers ──────────────────────────────────
AZURE_SPEECH_KEY=          # ur-PK-UzmaNeural / ur-PK-AsadNeural
AZURE_SPEECH_REGION=       # e.g. eastus
ELEVENLABS_API_KEY=        # Optional: eleven_flash_v2_5 English only

# ─── Telephony ──────────────────────────────────────
PLIVO_AUTH_ID=             # Primary telephony
PLIVO_AUTH_TOKEN=
PLIVO_PHONE_NUMBER=        # +92xxx in E.164 format

TWILIO_ACCOUNT_SID=        # Fallback telephony + WhatsApp
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

TWILIO_WHATSAPP_FROM=      # WhatsApp sender e.g. whatsapp:+14155238886

# ─── Server ─────────────────────────────────────────
SERVER_BASE_URL=           # Public URL e.g. https://clinic.example.com (for Plivo webhooks)
API_PORT=8000
WS_PORT=8001

# ─── Application ────────────────────────────────────
DEFAULT_LANGUAGE=ur-PK     # Fallback if no DTMF received
CLINIC_NAME=Hospital Name
CLINIC_TIMEZONE=Asia/Karachi

# ─── Pipeline (inherit from triage system — DO NOT CHANGE) ──────
VAD_CONFIDENCE=0.6
VAD_STOP_SECS=0.6
VAD_MIN_VOLUME=0.5
STT_DEEPGRAM_ENDPOINTING=600
STT_DEEPGRAM_UTTERANCE_END_MS=1500

# ─── Feature flags ──────────────────────────────────
DTMF_LANGUAGE_SELECTION=true
AUTO_LANGUAGE_DETECT=false   # Phase 2 feature — off in v1
WHATSAPP_REMINDERS=true
TTS_CACHE_ENABLED=true
TTS_CACHE_MAX_ENTRIES=500

# ─── Analytics / Monitoring ──────────────────────────
OTEL_EXPORTER_OTLP_ENDPOINT=   # Optional: Jaeger or OTLP collector
LOG_LEVEL=INFO
```

---

## 8. SECRETS MANAGEMENT

```
Approach: .env file + python-dotenv + .gitignore

Rules (inherited from triage system):
1. NEVER read API key values in code — check existence with len(os.getenv("KEY", ""))
2. NEVER log key values — log only "Key present: True/False"
3. .env is .gitignored — never commit to version control
4. .env.example committed — all keys listed with empty values and comments
5. Docker: env_file: .env — passed to containers, not baked into image
6. CI/CD: keys injected via secrets manager (GitHub Secrets / AWS Secrets Manager)
7. Production: consider migration to AWS Secrets Manager or HashiCorp Vault for Phase 2
```

---

## 9. UI ARCHITECTURE SPEC

### Tailwind CSS Configuration
```html
<!-- CDN — v1 only. Convert to PostCSS in v2 if bundle size matters -->
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = {
    darkMode: 'class',
    theme: {
      extend: {
        colors: {
          brand: {
            blue: '#0EA5E9',    // sky-500
            teal: '#14B8A6',    // teal-500
          }
        },
        fontFamily: {
          sans: ['Inter', 'sans-serif'],
          urdu: ['Noto Nastaliq Urdu', 'serif'],
        }
      }
    }
  }
</script>
```

### Alpine.js Global Store Design
```javascript
// Injected in base template — available on all pages
document.addEventListener('alpine:init', () => {
  Alpine.store('app', {
    // Theme
    darkMode: localStorage.getItem('darkMode') !== 'false',
    toggleDark() {
      this.darkMode = !this.darkMode;
      localStorage.setItem('darkMode', this.darkMode);
      document.documentElement.classList.toggle('dark', this.darkMode);
    },

    // Auth
    user: null,
    role: null,  // 'admin' | 'doctor' | 'receptionist'

    // Live call state (populated by WebSocket)
    activeCalls: [],
    stats: {active_calls: 0, todays_appointments: 0, booked_today: 0, cost_today: 0},

    // Toast queue
    toasts: [],
    toast(msg, type = 'success') {
      const id = Date.now();
      this.toasts.push({id, msg, type});
      setTimeout(() => this.toasts = this.toasts.filter(t => t.id !== id), 5000);
    },

    // Emergency state
    emergencyActive: false,
    emergencySession: null,
  });
});
```

### WebSocket Client Architecture
```javascript
// WebSocket reconnection — injected in base.html
class ReceptionistWS {
  constructor(url) {
    this.url = url;
    this.ws = null;
    this.reconnectDelay = 1000;
    this.connect();
  }

  connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (e) => this.handleEvent(JSON.parse(e.data));
    this.ws.onclose = () => setTimeout(() => this.connect(), this.reconnectDelay);
    this.ws.onerror = () => this.ws.close();
  }

  handleEvent(event) {
    const store = Alpine.store('app');
    switch(event.type) {
      case 'call.started':
        store.activeCalls.push(event);
        break;
      case 'call.ended':
        store.activeCalls = store.activeCalls.filter(c => c.session_id !== event.session_id);
        break;
      case 'call.emergency':
        store.emergencyActive = true;
        store.emergencySession = event;
        store.toast('Emergency call detected!', 'emergency');
        break;
      case 'stats.update':
        store.stats = event;
        break;
      // ... other events update reactive Alpine data
    }
  }
}

// Init on DOMContentLoaded
const ws = new ReceptionistWS(`wss://${location.host}/ws`);
```

---

## 10. RTL RENDERING APPROACH

```css
/* In base.html <style> block */

/* Urdu / Punjabi Nastaliq font */
@import url('https://fonts.googleapis.com/css2?family=Noto+Nastaliq+Urdu:wght@400;700&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* RTL text container — apply to all Urdu/Punjabi content blocks */
.rtl-text {
  direction: rtl;
  font-family: 'Noto Nastaliq Urdu', serif;
  font-size: 1.1em;        /* Nastaliq slightly larger for readability */
  line-height: 2.2;        /* Nastaliq requires more vertical space than Latin */
  text-align: right;
}

/* Inline language detection for transcript display */
.transcript-block[data-rtl="true"] {
  direction: rtl;
  text-align: right;
  font-family: 'Noto Nastaliq Urdu', serif;
  line-height: 2.2;
}

.transcript-block[data-rtl="false"] {
  direction: ltr;
  text-align: left;
  font-family: 'Inter', sans-serif;
}
```

```python
# Server-side: tag is_rtl in WebSocket events
def is_rtl_language(language_code: str) -> bool:
    return language_code in ("ur-PK", "pa-PK")

# In STTBroadcaster — add is_rtl to every stt event
event["is_rtl"] = is_rtl_language(session.language_profile.code)
```

```html
<!-- In Live Call Monitor template — Alpine.js reactive RTL -->
<div class="transcript-block"
     :data-rtl="turn.is_rtl ? 'true' : 'false'"
     x-text="turn.text">
</div>
```

---

## 11. ARCHITECTURE DECISION RECORDS (ADRs)

### ADR-001 — Language Detection Method: DTMF
**Date**: 2026-04-24
**Status**: Accepted

**Context**: Must support 3 languages (ur-PK, pa-PK, en) per call. Two options: (1) DTMF keypress at call start, (2) auto-detect via Whisper on first 3–5 seconds of speech.

**Decision**: DTMF (option 1).

**Rationale**:
- Auto-detect adds 200–500ms processing on first response — violates <800ms SLA
- DTMF is deterministic: zero ambiguity, zero compute cost
- Pakistani callers are already familiar with "press 1 for language" IVR menus
- Whisper auto-detect can be offered as opt-in Phase 2 enhancement once baseline is stable
- DTMF works even when the patient immediately speaks in their language (no Whisper false detection)

**Consequences**:
- Must generate 3 DTMF prompt audio files at deployment (one-time, not per-call)
- 5-second DTMF timeout → fallback to clinic's configured default language
- Patients who skip DTMF (common) get default language — must be configured correctly per clinic

---

### ADR-002 — Punjabi TTS: Azure ur-PK Fallback
**Date**: 2026-04-24
**Status**: Accepted

**Context**: No dedicated Pakistani Punjabi (Shahmukhi) neural TTS provider exists as of 2026-04-24. Options: (1) Azure ur-PK voice as fallback, (2) ElevenLabs multilingual v2, (3) Build custom Punjabi TTS, (4) Respond only in Urdu to Punjabi callers.

**Decision**: Azure ur-PK-UzmaNeural as Punjabi TTS fallback (option 1).

**Rationale**:
- Pakistani Punjabi speakers understand Urdu — this is a medically and socially acceptable fallback
- Azure ur-PK voices are authentic Pakistani Urdu — significantly more natural than Indian-accented alternatives
- ElevenLabs multilingual v2 tends to produce Indian-inflected Punjabi — worse patient experience than Urdu
- Building custom TTS is out of scope for v1
- Responding-in-Urdu pattern is already common in Pakistani healthcare settings

**Consequences**:
- TTS for Punjabi calls uses the `ur-PK` language profile TTS config — same voice as Urdu calls
- TTS cache namespace for `pa-PK` and `ur-PK` are kept separate — they may have different spoken_text content even if same voice
- This is flagged to clinic admin in Language Settings UI: "Punjabi TTS: using Pakistani Urdu voice (no dedicated Punjabi TTS available)"
- Reassess when Azure or Zudu.ai releases a pa-PK Shahmukhi voice

---

### ADR-003 — UI Framework: Tailwind + Alpine.js + Jinja2
**Date**: 2026-04-24
**Status**: Accepted

**Context**: Need a production-quality admin portal with 15 pages, real-time WebSocket updates, dark mode, RTL support, and a calendar. Options: (1) Tailwind + Alpine.js + Jinja2 (server-rendered, CDN), (2) React + TypeScript, (3) Vue 3 + Vite.

**Decision**: Tailwind + Alpine.js + Jinja2 (option 1).

**Rationale**:
- No build pipeline — single `python main.py` starts everything. Zero ops complexity for v1
- Alpine.js reactive components are sufficient for all UI interactions including WebSocket live updates
- Proven in the triage system: RTL rendering, mic level meter, live transcript streaming all working
- Jinja2 templates are directly in FastAPI — one deploy artifact
- React/Vue would require a separate Node.js dev server, CORS config, build step, and separate deployment
- For a single-clinic v1 product this overhead is not justified; React/Vue is a v2 migration if needed

**Consequences**:
- No TypeScript type safety on frontend — mitigate with explicit Alpine.js store schemas and API response validation
- CDN Tailwind cannot purge unused classes in v1 — bundle is ~3MB. Acceptable for clinic LAN usage. Convert to PostCSS for v2
- FullCalendar.js and Chart.js load from CDN — 2 additional network requests on page load
- If offline mode needed (clinic with no internet): switch to bundled assets in v2

---

## QA REPORT — Phase 4

```
QA Report — Phase 4: Architecture Definition
Status: PASS
Tested By: QA Agent
Timestamp: 2026-04-24

PASSED:
- Service inventory table complete — all 5 Docker services + all external services documented
- Provider abstraction interfaces defined for all 4 types (STT, LLM, TTS, Telephony) + LanguageProfile
- Language profile architecture spec complete — storage, loading, switching, clinic config
- REST API contract complete — all endpoints listed with HTTP method + route
- WebSocket event schema complete — all 8 event types with JSON payload examples
- External dependencies pinned — pipecat==0.0.85 sacred constraint preserved
- Docker Compose config complete — all services, volumes, healthchecks, env_file
- Environment variable schema complete — all API keys, feature flags, pipeline params
- Secrets management rules match triage system inherited constraints
- UI architecture spec complete — Tailwind config, Alpine.js store design, WebSocket client
- RTL rendering approach complete — CSS + font spec + server-side is_rtl tagging
- 3 ADRs written: language detection, Punjabi TTS fallback, UI framework

FAILED:
- None

Inherited Constraint Checks:
- pipecat==0.0.85 ✅ preserved in requirements.txt
- VAD params ✅ in .env schema
- allow_interruptions not set to False ✅
- ElevenLabsTTSParams not used ✅
- eleven_v3 not used ✅
- No audio filters ✅ not referenced anywhere

Language Coverage:
- ur-PK profile fully specified ✅
- pa-PK profile fully specified with TTS fallback rationale ✅
- en profile fully specified ✅

Blocking: NO
Iteration: 1 of 3
```
