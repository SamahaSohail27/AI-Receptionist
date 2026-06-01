# AI Receptionist — Conversational Scenarios

This is the authoritative map of how the LLM handles each real-world request.
Tools are defined in [`api/test_session.py`](../api/test_session.py); both the
text-mode chat and the browser realtime session share the same tool surface
(via `_REALTIME_TOOLS` in [`api/realtime_session.py`](../api/realtime_session.py)).

For every scenario the system prompt instructs the LLM to:

- acknowledge the caller's feeling before asking a question,
- ask **one** question per turn, never two,
- vary phrasing across turns,
- keep replies under 30 words,
- stick to Pakistani Urdu (no Indian-Urdu vocabulary, no Hindi loanwords).

---

## Tool surface (10 tools)

| Tool | Purpose |
| --- | --- |
| `list_doctors(specialty?)` | All active doctors. |
| `search_doctors(specialty?, gender?, name?)` | Gender-aware filter — handles "female doctor". |
| `find_available_slots(doctor_id, date_pkt)` | Per-day slot list. Required before promising a time. |
| `find_earliest_slot(doctor_id?, specialty?, gender?, urgency=normal\|urgent)` | Soonest open slot. `urgency=urgent` caps to today + tomorrow. |
| `triage_severity(symptom_text, language)` | Returns `low \| medium \| high \| emergency` plus suggested action. |
| `find_or_create_patient(name, phone, preferred_language?)` | Phone in +92 E.164. |
| `find_existing_appointment(phone)` | Most recent active appointment. |
| `book_appointment(patient_id, doctor_id, date_pkt, time_pkt, notes?)` | Create. |
| `reschedule_appointment(appointment_id, date_pkt, time_pkt)` | Move slot. |
| `cancel_appointment_tool(appointment_id, reason?)` | Cancel. |

---

## Scenario → tool sequence → example

### 1) General booking
**Caller:** "I'd like to book an appointment with a doctor."
**Sequence:** `search_doctors` → ask preference → `find_available_slots(doctor_id, date)` → `find_or_create_patient(name, phone)` → `book_appointment`.
**Reply (en):** "Of course. Do you prefer a specific doctor or specialty?"
**Reply (ur):** "ضرور۔ کیا آپ کسی خاص ڈاکٹر یا شعبے سے ملنا چاہتے ہیں؟"

### 2) Urgent — "earliest available"
**Caller:** "I have a severe headache, please book the earliest appointment."
**Sequence:** `triage_severity("severe headache")` → severity = `high` → `find_earliest_slot(urgency='urgent')` → `find_or_create_patient` → `book_appointment(notes='urgent')`.
**Reply:** "I'm sorry to hear that. Let me find the soonest slot — please share your name and phone."

### 3) Severe symptom — empathy first
**Caller:** "I have very bad stomach pain."
**Sequence:** `triage_severity("very bad stomach pain")` → `high` → `find_earliest_slot(urgency='urgent', specialty='gastro')` → book.
**Reply:** "That sounds painful — I'll find you the earliest doctor today."

### 4) Emergency — do NOT book, transfer to 1122
**Caller:** "I have chest pain and I can't breathe."
**Sequence:** `triage_severity` → `emergency` → reply with 1122 + offer triage nurse transfer (clinic config).
**Reply:** "Please call 1122 right now. Do you want me to also alert our triage nurse?"

### 5) Reschedule
**Caller:** "I want to reschedule my appointment to tomorrow morning."
**Sequence:** `find_existing_appointment(phone)` → confirm with caller → `find_available_slots(doctor_id, tomorrow)` → `reschedule_appointment(appointment_id, ...)`.

### 6) Doctor preference — female / specialist
**Caller:** "Book with a female doctor."
**Sequence:** `search_doctors(gender='F')` → present options → continue normal booking.

**Caller:** "I want a cardiologist."
**Sequence:** `search_doctors(specialty='cardio')` → continue.

### 7) Time-specific request
**Caller:** "Book for tomorrow evening."
**Sequence:** Convert to `date_pkt` (tomorrow) → `find_available_slots(doctor_id, date_pkt)` → filter to evening times → confirm one with caller → book.

### 8) Confused / incomplete
**Caller:** "I'm not feeling well."
**Reply:** ONE clarifying question — "I'm sorry. Can you describe what symptom is bothering you?" — then proceed by symptom severity.

### 9) Cancel
**Caller:** "I need to cancel my appointment."
**Sequence:** `find_existing_appointment(phone)` → confirm details → `cancel_appointment_tool(appointment_id, reason='caller-requested')`.

---

## Severity classifier (rule-based, pre-LLM)

`pipeline/emergency_detector.py` exposes `severity(text, language) -> low|high|emergency` for the PSTN/pipecat path so urgency is recognized even before the LLM tool call.

| Tier | Trigger phrases (en) | Trigger phrases (ur-PK) | Action |
| --- | --- | --- | --- |
| **emergency** | chest pain · can't breathe · unconscious · heart attack · stroke · seizure · choking | سینے میں درد · سانس نہیں آ رہا · بے ہوش · دل کا دورہ · فالج | Transfer to 1122 + triage nurse. **No booking.** |
| **high** | severe · very bad · unbearable · excruciating · won't stop · hours of pain | شدید · بہت زیادہ · ناقابل برداشت · بہت تکلیف | Empathise, then `find_earliest_slot(urgency='urgent')`. |
| **low** | (none of the above) | (none of the above) | Normal booking flow. |

---

## Voice-quality controls (Phase 11.1–11.5)

- `tts_provider = elevenlabs` → realtime proxy switches OpenAI to `modalities=['text']` and synthesizes audio via ElevenLabs (PCM 24 kHz) using the admin's selected female / male voice and model (`eleven_flash_v2_5` default).
- `tts_provider = openai_tts` → OpenAI Realtime emits audio directly using the female/male voice picked in `/settings/providers`.
- Settings cache invalidates immediately on save (`core.runtime_config.invalidate_voice_settings`); next session start picks up the change with no process restart.

---

## What still needs human verification

- **Live audio quality on actual phone calls** — automated tests don't cover voice naturalness.
- **Native Pakistani Urdu / Punjabi speaker** review of new prompts (purity check is automated; naturalness is not).
- **ElevenLabs latency budget** — adds ~150–300 ms per turn versus OpenAI native audio. Acceptable for a clinic line; flag if perceived as sluggish.
