# Production Obstacles: AI Voice Agents for Hospitals

> Research compiled 2026-04-24. Focus: Pakistani hospital deployment context.
> Sources: AssemblyAI, Hamming AI, Retell AI, LiveKit, Infinitus, PMC/Nature, LinkedIn industry posts.

---

## 1. Voice Latency

**Obstacle:** Human conversation expects <300ms response. Production median is **1.4–1.7 seconds** (P95 hits 4–5s). Every pipeline stage adds latency sequentially: VAD → STT → LLM → TTS → network.

| Stage | Typical | Optimized |
|---|---|---|
| VAD + turn detection | 200–800ms | 200–400ms |
| STT | 200–400ms | 100–200ms |
| LLM inference | 300–1000ms | 200–400ms |
| TTS synthesis | 150–500ms | 100–250ms |
| Network round-trips | 100–300ms | 50–150ms |
| **Total end-to-end** | **1000–3200ms** | **670–1450ms** |

LLM is 40–70% of total latency and the main bottleneck.

**Why it matters:** At >1500ms, patients talk over the agent, causing cascading misdetection. Urdu/Punjabi code-switching adds STT processing time. Karachi/Lahore to US cloud endpoints add 150–250ms baseline.

**Solutions:**
- Streaming STT (saves 100–200ms) + streaming TTS (saves 200–400ms) — mandatory, not optional
- Switch to fast LLM tier for routine tasks: GPT-4o-mini, Gemini 2.5 Flash, Claude 3.5 Haiku (~360ms TTFT)
- Reduce VAD silence threshold from 800ms to 400–500ms — alone recovers ~300ms perceived latency
- Cache TTS for high-frequency phrases (greetings, hold messages, confirmations)
- Deploy inference in Middle East / South Asia region, not US-East
- Target **<800ms end-to-end** as production SLA; monitor P95, not averages

---

## 2. STT/TTS Quality

**Obstacle:** Real-world WER runs 2–3x worse than benchmark scores. Standard models fail on Pakistani accents, medical terminology (drug names, procedures), and hospital background noise (PA announcements, equipment beeps).

**Why it matters:** A STT misread of "Lasix" fed to the LLM generates a confident wrong answer. General TTS mispronounces Urdu names and drug terms, immediately breaking patient trust.

**Solutions:**
- Inject custom vocabulary (drug names, doctor names, procedure names) via Deepgram or AssemblyAI hotword boosting
- For Urdu STT: Soniox, Bolna.ai, or Zudu.ai are purpose-built; Whisper large-v3 supports Urdu but needs noise-robustness testing
- Test STT against audio recorded in your actual hospital environment before selecting a vendor — OPD noise levels will surprise you
- Add a post-STT correction layer: regex or lightweight LLM to normalize common medical misrecognitions
- For TTS: Zudu.ai for Urdu neural TTS; ElevenLabs multilingual v2 for mixed-language output
- Never go to production with a generic STT model and no medical custom vocabulary — WER gap on medical terms is 30–50%

---

## 3. Multilingual Handling

**Obstacle:** Pakistani patients switch between Urdu, Punjabi, English, and regional dialects mid-sentence. Standard multilingual ASR treats each language independently and breaks on code-switched input. No single off-the-shelf model covers Urdu + Punjabi + English code-switching well in a medical context.

**Why it matters:** "Mujhe doctor sahab se appointment leni hai, Friday ko available hai?" spans three language layers. Mono-lingual ASR drops one of them. LLMs prompted only in English misread Urdu intent signals (negations, politeness markers).

**Solutions:**
- Enable `code_switching` mode in Deepgram/AssemblyAI; Bolna.ai and Zudu.ai are purpose-built; Ringg.ai supports 16+ South Asian accents at sub-400ms
- Add utterance-level language detection; route to appropriate LLM prompt variant dynamically
- Use bilingual system prompts (Urdu + English); GPT-4o and Claude 3.5 Sonnet handle multilingual context substantially better than smaller models
- Build a phonetic dictionary for common Pakistani names (Muhammad, Khadija) and inject into TTS pronunciation guides
- Graceful Punjabi fallback: "Kya aap Urdu mein baat kar sakte hain?" — not a crash or silence
- QA must include code-switched test utterances, not just pure Urdu or pure English samples

---

## 4. Interruptions and Turn-Taking

**Obstacle:** VAD is the most unreliable component in most production pipelines. It fails in both directions: agent talks over patient (barge-in failure) or agent cuts off patient mid-sentence (false positive).

**Why it matters:** Elderly, anxious, or second-language patients pause frequently within sentences. Cutting off a patient describing symptoms is a clinical safety failure. Urdu fillers ("haan ji," "achha," "theek hai") must not trigger new speaker turns.

**Solutions:**
- Augment energy-based VAD with semantic end-of-utterance prediction (Silero VAD + intent-completion classifier, or Deepgram's endpointing model)
- Tune silence thresholds per conversation state: 700–800ms during symptom description, 300–400ms during yes/no confirmations
- Strip common Urdu acknowledgment fillers from triggering new response generation
- Barge-in response latency must be <200ms — immediately stop TTS and open new STT window
- Log barge-in failures as a first-class production metric; alert if >5% of turns

---

## 5. Appointment Booking Workflows

**Obstacle:** Pakistani hospitals use Shifa, iMedics, HospitalOS, or custom HIS — most with no modern REST/FHIR APIs. They rely on HL7 v2 messages, SOAP endpoints, or direct DB access. Token/OPD queue systems, insurance panel checks (Sehat Sahulat, corporate), and overbooking policies all add conditional logic that breaks simple demo-style booking flows.

**Why it matters:** A "confirmation" that doesn't write to the HIS creates double-booking, patient confusion, and broken staff trust. This kills deployments within weeks.

**Solutions:**
- **Audit the HIS API before any development** — budget 4–8 weeks; this is the longest lead-time item
- If SOAP-only or HL7 v2: build a thin REST adapter layer first before building agent logic
- Two-phase commit pattern: reserve slot → confirm with patient → commit to HIS. If HIS write fails, re-offer alternatives within the same call
- Implement 30–60 second slot reservation lock to prevent race conditions
- For OPD token systems: integrate with the queue management system to issue a token, not a time slot
- Use CNIC as the universal patient identifier — not name-based lookup
- Build an independent booking audit log (slot ID, status, timestamp) separate from the HIS
- After successful booking, trigger WhatsApp confirmation (higher engagement than SMS/email in Pakistan)
- If HIS is down: escalate to human agent immediately — never confirm a phantom slot

---

## 6. EHR/HIS Integration

**Obstacle:** Pakistani hospitals span a spectrum from modern cloud HIS to on-premise with no external API. HL7 v2 needs an interface engine. FHIR implementation is inconsistent across vendors. Most HIS systems are batch-optimized, not designed for concurrent real-time API calls.

**Solutions:**
- Deploy a FHIR facade (Medplum, Smile CDR, or custom FastAPI service) that normalizes HIS-specific APIs into consistent FHIR R4 calls
- Maintain a Redis-backed appointment availability cache (TTL 60–120s); voice agent reads from cache, not live HIS
- All HIS write operations must be idempotent with a unique request ID to handle retries after network failures
- FHIR R4 `Schedule`, `Slot`, `Appointment`, `Patient` — implement read + write for these four first
- Never integrate at the database level — HIS schema changes will silently break the agent
- Treat the FHIR adapter as a production microservice with its own monitoring and error budget

---

## 7. Reminders and Follow-Up Calls

**Obstacle:** Voicemail detection is unreliable. Consent management is legally required. Patients who pick up may want to reschedule, requiring a full inbound-style interaction mid-outbound call.

**Solutions:**
- Use Plivo/Twilio AMD (Answering Machine Detection) before speaking; if voicemail, leave pre-recorded message or terminate and log for human callback
- Pull appointment details, doctor name, prep instructions, and fee from HIS dynamically at call time — no hardcoded templates
- After reminder delivery, listen for patient response: confirm / cancel / question — route accordingly
- **WhatsApp-first strategy**: for Pakistani demographics, WhatsApp messages with confirm/cancel buttons outperform voice calls significantly
- Track consent (patient ID, timestamp, channel, language) before any outbound communication
- Maximum 2–3 retry attempts at different times of day; log final status back to HIS
- Use a job queue (Celery, BullMQ) with rate limiting per SIP trunk capacity; outbound scheduling must be architecturally separate from inbound pipeline

---

## 8. Human Handoff

**Obstacle:** The most dangerous moment is when the agent fails silently and the patient doesn't get transferred. Common failures: agent loops without escalating, handoff triggers but no human is available, context is not passed to the human agent.

**Why it matters:** A failed handoff on a patient describing chest pain is a patient safety incident, not a UX issue.

**Mandatory escalation triggers:**
- Confidence below 60% for two consecutive turns
- Patient explicitly requests a human: "mujhe receptionist se baat karni hai"
- Emergency symptoms detected: chest pain, severe shortness of breath, loss of consciousness
- Psychiatric emergency / suicidal ideation keywords
- Same intent misunderstood 2+ consecutive times
- HIS API failure during a booking operation
- After-hours call that cannot be self-served

**Solutions:**
- Warm transfer: agent packages structured context `{ patient_id, intent, verified, sentiment, conversation_summary, open_question }` and briefs the human before the caller connects — patient does not repeat themselves
- If no human available: "Kya aap chahenge ke hum aapko callback karen?" — never drop the call
- Emergency keywords: hard transfer to triage nurse in <10 seconds, bypassing all queue logic
- Define maximum hold time (90 seconds recommended) with supervisor alert if exceeded
- Log every handoff: trigger reason, agent availability status, outcome
- In early deployment: escalate more, not less. Tighten thresholds as monitoring data accumulates

---

## 9. Privacy and Compliance (HIPAA / PDPA)

**Obstacle:** HIPAA compliance is **behavioral, not just architectural**. Infrastructure can be perfectly encrypted while the agent's conversational behavior exposes PHI — reading appointment details to an unverified caller, repeating a diagnosis in a confirmation, asking about medication before verifying identity.

The 2025 HIPAA Security Rule update eliminates the "addressable vs. required" distinction — nearly everything is now mandatory. Pakistan's PDPA governs local data handling.

**Solutions:**
- Execute BAAs with every vendor that touches patient audio: Plivo (telephony), STT provider, LLM provider (OpenAI/Anthropic), TTS provider, cloud storage
- Identity-first conversational design: every flow that could expose PHI must begin with identity verification
- Never include raw PHI in LLM prompts where avoidable — use patient IDs, not names + DOBs
- Automated PHI redaction from transcripts before storage
- Append-only audit log (write-once S3 or CloudTrail equivalent) for all PHI access events
- Automated red-team test suite: can a caller get appointment details without verifying identity? Run on every deployment
- For PDPA data residency: evaluate Azure Pakistan North or local colocation if data cannot leave Pakistan
- On-premise stack option for strict data residency: Whisper.cpp + self-hosted Llama 3 70B (vLLM) + Kokoro/Coqui TTS

---

## 10. Hallucination and Safety Risk

**Obstacle:** LLMs generate confident, fluent, and factually wrong responses. Voice delivery makes hallucinations more dangerous than text — patients cannot re-read or verify. A confident voice saying the wrong medication dosage or fabricated insurance coverage causes direct harm.

Root causes: outdated knowledge base, ambiguous patient input, context window overflow in long calls, over-generalization from Western medical training data.

**Solutions:**
- **Constrained action space** (Infinitus approach): define a finite set of actions the agent can take — book, check availability, escalate, read policy, confirm. LLM selects and executes actions; it cannot say something outside the defined set
- RAG over a curated, clinic-specific knowledge base — if retrieval fails, escalate, do not generate
- System prompt must explicitly list what the agent is NOT allowed to discuss: diagnoses, clinical advice, medications, prognosis. Any such question triggers immediate escalation
- Secondary LLM or rule-based classifier as a post-generation filter — checks output for clinical claims before TTS speaks it
- Log every hallucination instance; build a hallucination regression test suite from real incidents
- **Most effective single architectural decision**: do not use a free-response LLM for a medical receptionist. Use a workflow engine with LLM-powered intent classification + slot-filling, executing only predefined actions

---

## 11. Observability, Testing, and Reliability

**Obstacle:** A broken voice agent doesn't return a 500 error — it has a plausible-sounding conversation that books the wrong slot, misunderstands intent, or silently fails to escalate.

**4-Layer QA Framework:**

| Layer | What to Measure | Target |
|---|---|---|
| Infrastructure | STT WER, TTS MOS, TTFW, network jitter | WER <5%, MOS >4.0, TTFW <400ms |
| Agent Execution | Intent accuracy, instruction adherence, knowledge accuracy | >95% task completion |
| User Reaction | Barge-in failures, reprompt rate, frustration signals | Reprompt rate <10% |
| Business Outcome | Booking success, escalation rate, deflection | Booking success >95%, deflection >70% |

**Solutions:**
- Unique session ID per call; trace every pipeline stage (STT duration, LLM time, TTS time, tool calls) through it via OpenTelemetry or Langsmith
- Structured log per turn: `{ session_id, turn_id, stt_transcript, stt_confidence, llm_response, tts_latency, tool_calls, escalation_triggered }`
- 50+ golden call scenarios covering happy path, edge cases, Urdu code-switching, noise conditions, emergency escalation — run on every deployment (Hamming, Cekura, or Bluejay for Pipecat pipelines)
- LLM-as-judge scoring on recorded calls for factual accuracy and escalation-timing review
- Canary deployments for prompt/model changes: 5–10% of traffic, 24-hour monitoring window before full rollout
- Drift alerts: if intent accuracy, WER, or task success deviates >10% from the 7-day rolling baseline
- Load test at 2–3x peak concurrent call volume before go-live
- Monitor P95/P99 latency — the worst 5% of calls drive patient complaints

---

## 12. Scalability and Maintainability

**Obstacle:** A pipeline that works at 10 concurrent calls often fails at 100 due to LLM API rate limits, STT WebSocket connection caps, TTS queue buildup, or telephony trunk exhaustion. Pakistani hospital call volume is highly peaky (OPD opening rushes, post-holiday surges).

**Solutions:**
- Design the pipeline stateless per call; use a message queue (Redis Streams, RabbitMQ) between pipeline stages for independent scaling
- Multi-agent architecture: narrow front-door orchestrator + specialized agents (scheduling, FAQ, emergency triage, billing) — each independently testable and deployable
- Separate HIS adapter, booking logic, patient lookup, and reminder scheduler into independent services
- Use LiteLLM or a thin wrapper so you can swap LLM providers without rewriting pipeline code
- Treat conversation graphs and prompts as versioned artifacts with changelogs and feature flags for A/B testing
- Build a CMS-like interface for hospital admin staff to update doctor schedules, fees, and policies without developer intervention — this is a live operational database, not a static file
- Pre-provision Plivo trunk capacity for peak load; define runbooks for both LLM provider outage and HIS downtime
- Quarterly architecture review — voice AI tooling changes faster than most domains

---

## Pakistan-Specific Priority Stack

| Priority | Item | Why |
|---|---|---|
| 1 | Urdu + Punjabi + English code-switching STT/LLM | Most unique technical challenge |
| 2 | HIS API audit + adapter build | Longest lead-time item (4–8 weeks); most deployment-killing risk |
| 3 | WhatsApp-first for confirmations and reminders | Dominant patient channel in Pakistan |
| 4 | Token/OPD queue system support | Western slot booking does not map to Pakistani OPD workflows |
| 5 | PDPA data residency decision | Determines entire cloud architecture before any infra is provisioned |
| 6 | Conservative LLM scope | Administrative tasks only; all clinical questions escalate |
| 7 | Generous escalation thresholds early | Build confidence from real data, tighten over time |
