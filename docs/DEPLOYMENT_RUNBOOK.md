# AI Medical Receptionist — Deployment Runbook
**Version**: 1.0
**Last Updated**: 2026-04-25
**Environment**: Ubuntu 22.04 LTS on AWS/GCP East US (required for <800ms latency SLA)

---

## Prerequisites

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| Server RAM | 4 GB | 8 GB recommended for concurrent calls |
| Server CPU | 2 vCPUs | 4 recommended |
| OS | Ubuntu 22.04 LTS | Other Linux distros OK |
| Docker | 24.x+ | `docker compose` plugin required |
| Python | 3.10 | Only if running without Docker |
| PostgreSQL | 15+ | Provided by Docker Compose |
| Redis | 7+ | Provided by Docker Compose |
| **Region** | **AWS/GCP East US** | MANDATORY — providers (Deepgram, Azure, OpenAI) all have East US endpoints |

### Required API Keys (minimum set for Urdu + English calls)

| Key | Provider | Get at | Required |
|-----|----------|--------|---------|
| `OPENAI_API_KEY` | OpenAI | platform.openai.com | YES |
| `DEEPGRAM_API_KEY` | Deepgram | deepgram.com/console | YES |
| `AZURE_SPEECH_KEY` | Azure | portal.azure.com → Cognitive Services | YES |
| `PLIVO_AUTH_ID` + `PLIVO_AUTH_TOKEN` | Plivo | plivo.com/console | YES (telephony) |
| `GROQ_API_KEY` | Groq | console.groq.com | YES (Punjabi STT) |
| `SECRET_KEY` | — | Generate with `openssl rand -hex 32` | YES |

---

## Part 1 — First Deployment (Docker Compose)

### Step 1 — Clone / Upload Code

```bash
# On the server
git clone <repo-url> /opt/ai-receptionist
cd /opt/ai-receptionist
```

### Step 2 — Configure Environment

```bash
cp .env.example .env
nano .env   # fill in all required API keys
```

Minimum required fields:

```bash
SECRET_KEY=<output of: openssl rand -hex 32>
OPENAI_API_KEY=sk-...
DEEPGRAM_API_KEY=...
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=eastus
PLIVO_AUTH_ID=...
PLIVO_AUTH_TOKEN=...
GROQ_API_KEY=...
ALLOWED_ORIGINS=https://your-domain.com
```

### Step 3 — Build and Start Services

```bash
docker compose up --build -d
```

Expected output: four containers start (`api`, `worker`, `db`, `redis`).

Verify all healthy:

```bash
docker compose ps
```

All containers should show `healthy` or `running`. Wait ~30 seconds for `db` health check to pass before `api` starts.

### Step 4 — Run Database Migrations

```bash
docker compose exec api alembic upgrade head
```

Expected: `Running upgrade -> 0001_initial_schema, Create initial schema`.

### Step 5 — Create First Admin User

```bash
docker compose exec api python -c "
import asyncio
from core.database import AsyncSessionLocal
from models.auth import User
from core.auth import hash_password
from sqlalchemy import insert

async def create_admin():
    async with AsyncSessionLocal() as db:
        stmt = insert(User).values(
            name='Clinic Admin',
            email='admin@clinic.pk',
            hashed_password=hash_password('change-me-now'),
            role='admin',
            is_active=True,
        )
        await db.execute(stmt)
        await db.commit()
        print('Admin created: admin@clinic.pk / change-me-now')

asyncio.run(create_admin())
"
```

**Immediately** log in at `http://localhost:8000/login` and change the password.

### Step 6 — Generate DTMF Audio Files

```bash
docker compose exec api python scripts/generate_dtmf_audio.py
```

This generates `tts_cache/dtmf_prompt_ur.wav` and `tts_cache/dtmf_prompt_en.wav`
used at the start of every inbound call.

### Step 7 — Verify Health

```bash
curl http://localhost:8000/api/v1/health
# Expected: {"status": "ok", ...}
```

Log in to the admin UI at `http://localhost:8000`.
Navigate to **Settings → System Health** — all provider indicators should show green.

### Step 8 — Configure Plivo Webhook

In the Plivo console, set the answer URL for your +92 phone number to:

```
https://your-domain.com/api/v1/telephony/inbound
```

Method: `POST`

---

## Part 2 — Verification Checklist (run after every deployment)

```
[ ] docker compose ps  — all 4 containers healthy
[ ] GET /api/v1/health returns 200
[ ] Login page loads at /login
[ ] Dashboard loads (authenticated)
[ ] Provider Settings page: at least one STT/LLM/TTS green
[ ] DTMF audio files present in tts_cache/
[ ] Alembic migrations current: `docker compose exec api alembic current`
[ ] Test call: dial the Plivo +92 number, hear DTMF prompt
[ ] Redis reachable: `docker compose exec redis redis-cli ping` → PONG
[ ] No ERROR lines in logs: `docker compose logs api --since 5m | grep ERROR`
```

---

## Part 3 — Routine Operations

### View Logs

```bash
docker compose logs -f api       # API + pipeline logs
docker compose logs -f worker    # Celery reminder logs
docker compose logs -f db        # PostgreSQL logs
```

### Restart a Service

```bash
docker compose restart api
```

### Apply a Code Update

```bash
git pull
docker compose up --build -d api worker
docker compose exec api alembic upgrade head   # only if new migrations exist
```

### Backup the Database

```bash
docker compose exec db pg_dump -U ai_user ai_receptionist \
  | gzip > backups/ai_receptionist_$(date +%Y%m%d_%H%M%S).sql.gz
```

### Restore from Backup

```bash
# WARNING: this drops and recreates the database
gunzip -c backups/ai_receptionist_TIMESTAMP.sql.gz \
  | docker compose exec -T db psql -U ai_user ai_receptionist
```

### Scale Workers (high call volume)

```bash
docker compose up --scale worker=3 -d
```

---

## Part 4 — Rollback Procedure

If a deployment causes failures:

```bash
# 1. Roll back to previous image
git revert HEAD --no-edit   # or specify commit
docker compose up --build -d

# 2. Roll back database migration
docker compose exec api alembic downgrade -1

# 3. Verify health
curl http://localhost:8000/api/v1/health
docker compose logs api --since 5m | grep ERROR
```

If the database is corrupt:

```bash
docker compose down
docker volume rm ai-receptionist_pg_data
docker compose up -d db
# Wait for db to be healthy, then restore from backup
```

---

## Part 5 — Environment-Specific Notes

### Latency (Critical)

Deploy in **AWS us-east-1** or **GCP us-east4** to co-locate with:
- Deepgram Nova-2 (East US endpoint)
- Azure Speech (eastus region)
- OpenAI (US endpoint)

Cross-region deployment adds 100–300ms per API call and will breach the 800ms per-turn budget.

### Security Hardening (Production)

```bash
# Remove DB and Redis port exposure from docker-compose.yml
# In the db service, remove:
#   ports:
#     - "5432:5432"
# In the redis service, remove:
#   ports:
#     - "6379:6379"

# Use a reverse proxy (nginx/Caddy) to terminate TLS on port 443
# Never expose port 8000 directly in production
```

### ElevenLabs from Pakistan

The free ElevenLabs tier is blocked for Pakistan IPs. Use a Creator key (`sk_4...`).
Set `ELEVENLABS_API_KEY` in `.env`. If no key is available, Urdu/Punjabi will use
Azure Neural TTS (which is the default and the better option for Pakistani Urdu anyway).

### Punjabi TTS Limitation

No dedicated Pakistani Punjabi TTS exists. The system uses `ur-PK-UzmaNeural` (Azure)
for Punjabi responses. This is acceptable — Punjabi-speaking patients understand Urdu
voice responses. Flagged as Phase 2 improvement.

### Sacred Pipeline Values (Never Change in Production)

```bash
VAD_CONFIDENCE=0.6       # lower → drops valid Urdu speech
VAD_STOP_SECS=0.6        # lower → cuts off mid-sentence
STT_CONFIDENCE_UR=0.45   # never raise above 0.45 — Urdu scores 0.45–0.70 normally
PIPECAT_VERSION=0.0.85   # never upgrade — breaking changes in all newer versions
```

---

## Part 6 — Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `api` container exits on start | DB not ready | `docker compose restart api` after db is healthy |
| 401 on all requests | `SECRET_KEY` mismatch between restarts | Set a fixed `SECRET_KEY` in `.env` |
| Calls drop immediately | Plivo webhook URL unreachable | Check ALLOWED_ORIGINS and server firewall |
| No Urdu transcription | Deepgram key missing/invalid | Check `DEEPGRAM_API_KEY` in .env |
| TTS silent | Azure key missing or wrong region | Check `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION=eastus` |
| Calendar not loading | DB migration not run | `docker compose exec api alembic upgrade head` |
| High latency (>1s) | Cross-region deployment | Migrate to East US region |
| Worker not sending reminders | Redis not reachable | `docker compose restart redis worker` |
| `alembic.exc.CommandError: Can't locate revision` | Corrupt migration state | `docker compose exec api alembic stamp head` |

---

*This runbook covers Docker Compose deployment (local + staging + cloud VM).*
*For Kubernetes deployment, contact the platform team — a Helm chart is a Phase 2 deliverable.*
