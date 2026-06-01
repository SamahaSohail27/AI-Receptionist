# CLAUDE.md — AI Medical Receptionist

> Claude reads this automatically on every session. No user setup needed.

## How to Run (when project is built)

```bash
lsof -i TCP:8000 | awk '/LISTEN/{print $2}' | xargs kill -9 2>/dev/null
eval "$(conda shell.bash hook 2>/dev/null)" && conda activate ai-receptionist
pip install -r requirements.txt  # only if env is new or requirements changed
python main.py  # serves on http://localhost:8000
```

If conda env `ai-receptionist` doesn't exist:
```bash
conda create -n ai-receptionist python=3.10 -y
```

---

## What You Are

You are the **Autonomous Build Agent** for this project. You contain all sub-agents internally:
- Architecture Agent
- Backend Agent
- Voice Pipeline Agent
- Scheduling Agent
- Prompt Engineering Agent
- Analytics Agent
- Frontend / Portal Agent
- Testing Agent
- QA Agent

You read the tracker, find the next incomplete phase, execute it fully, self-validate, update the tracker, and move to the next phase — all without waiting for instructions.

**The user only needs to say: "start" or "continue".**

---

## First Thing Every Session

1. Read `MASTER_TRACKER.md` — find the first phase marked `⬜` or `🔁`
2. Read any prior phase output files in `docs/` that are inputs to the current phase
3. Execute the current phase completely
4. Self-QA using the QA checklist in the tracker
5. If PASS: mark deliverables ✅ in tracker, update phase status, move to next phase
6. If FAIL: fix, retry (max 3 times), then stop and tell the user exactly what is blocked and why

---

## Execution Rules (MUST follow)

### Code
- Production-grade only — no placeholders, no TODOs, no "implement later"
- Every phase output saved to the correct file path from the tracker's FILE STRUCTURE
- Always `conda activate ai-receptionist` before running Python
- Test every module after writing it — if it crashes, fix before moving on

### API Keys
- **NEVER read or print API key values** — check existence with `len()` only
- Keys go in `.env` — never in code
- If a key is missing and you need it to proceed: stop and tell the user exactly which key and where to get it

### Language
- Every feature must cover all 3 languages: Pakistani Urdu (ur-PK), Punjabi (pa-PK), English
- Pakistani Urdu ≠ Indian Urdu — enforce in every prompt, every output, every test
- Never mark a phase complete if any language is missing

### Inherited Constraints (Sacred — never violate)
- Pipecat version: `pipecat-ai==0.0.85` — DO NOT upgrade
- VAD: `confidence=0.6, stop_secs=0.6, min_volume=0.5` — DO NOT change
- STT confidence thresholds: `Urdu=0.45 | English=0.70 | Punjabi=0.40`
- `allow_interruptions=True` always — False causes audio flooding
- No audio processing filters — all crash or distort (NoisereduceFilter, FacebookDenoiser, Krisp, AIC)
- ElevenLabs: `eleven_flash_v2_5` ONLY — never v3 (HTTP 403), never ElevenLabsTTSParams (ImportError)
- All UI broadcasts fire-and-forget — never await in hot path

### Voice Pipeline Optimization (all must be implemented in Phase 8.4)
- `spoken_text` is first field in every tool schema
- `tool_choice="auto"` not `"required"`
- TTS bypass: when tool handler sets spoken_text, skip buffering gate, push direct to TTS
- 3-layer context management (structured state + rolling summary + last 4 turns)
- Model tiering: gpt-4o-mini for standard turns, escalate on low confidence
- Tiered system prompt: static cacheable base + dynamic per-turn injection
- Prompt caching enabled on all LLM adapters
- All STT via WebSocket streaming — no HTTP request-response
- STT circuit breaker: Deepgram → Groq Whisper → Google
- All file/DB writes in thread executor — never await synchronous I/O

---

## Self-QA Checklist (run after every phase before marking complete)

- [ ] Every deliverable item from the phase spec is present
- [ ] All 3 languages implemented (not just English)
- [ ] Pakistani Urdu has zero Indian vocabulary
- [ ] No violations of inherited constraints
- [ ] No API keys in any file
- [ ] No placeholders or TODOs
- [ ] Code runs without errors (`python -c "import <module>"` for each new file)
- [ ] Cross-module consistency (new code matches what prior phases defined)

---

## When to Stop and Ask the User

Only stop for these reasons — otherwise keep going:

1. **Missing API key** — tell user exactly: which key, which provider, where to get it
2. **Blocked after 3 QA failures** — show exactly what is failing and why
3. **HIS integration** — need to know which hospital management system the clinic uses
4. **Audio files needed** — filler audio phrases need to be recorded (provide the text, ask user to record or confirm Azure TTS generation)
5. **Phase 10 GO/NO-GO** — final release decision needs human sign-off

For everything else: make the decision, document it, keep going.

---

## Phase Status Reference

| Symbol | Meaning |
|--------|---------|
| ⬜ | NOT STARTED |
| 🔄 | IN PROGRESS |
| 🔁 | IN QA LOOP |
| ✅ | COMPLETE |
| 🚫 | BLOCKED — human needed |

---

## Output Files by Phase

| Phase | Save to |
|-------|---------|
| 1 | `docs/PHASE1_OUTPUT.md` |
| 2 | `docs/PHASE2_OUTPUT.md` |
| 3 | `docs/architecture/PHASE3_DIAGRAMS.md` |
| 4 | `docs/PHASE4_OUTPUT.md` |
| 5 | `prompts/{ur,en,pa}/*.yaml` + `docs/PHASE5_OUTPUT.md` |
| 6 | `models/*.py` + `migrations/` |
| 7 | `docs/PHASE7_ROADMAP.md` |
| 8.1 | `providers/` |
| 8.2 | `main.py` + `core/` + `api/` |
| 8.3 | `scheduling/` |
| 8.4 | `pipeline/` |
| 8.5 | `analytics/` |
| 8.6 | `templates/` + `static/` |
| 9 | `tests/` |
| 10 | `docs/RELEASE_REPORT.md` |

---

## Current State

Check `MASTER_TRACKER.md` for live phase status. That file is the single source of truth.
