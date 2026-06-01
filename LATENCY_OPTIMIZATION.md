# Latency Optimization Guide
### AI Receptionist — Voice Pipeline Performance

> These are the 3 most impactful things you can do right now to make the
> conversation feel fast and natural. Read top to bottom — they're ordered
> by how much difference they'll actually make.

---

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 01 — Play Filler Audio During Tool Calls
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**The Problem**
When the AI needs to fetch data (look up an appointment, check a slot,
pull patient records), it goes completely silent. The user hears dead air
for 500ms–2000ms and thinks the system has frozen.

**The Fix**
The moment the AI decides to call a tool, immediately play a short
pre-recorded phrase — before the tool even finishes running.

**Examples of filler phrases:**
```
"Let me check that for you..."
"One moment please..."
"Looking that up now..."
"Sure, pulling up your details..."
```

**Why pre-record them?**
If you generate the filler phrase via TTS at runtime, you're adding TTS
latency on top of tool latency — defeating the purpose. Record them once
at startup, store as raw audio, play instantly.

**Expected improvement:** Hides 500ms–2000ms of dead air completely.
User perceives zero waiting.

---

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 02 — Stream Everything: Don't Wait to Finish
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**The Problem**
Most naive implementations wait for each stage to fully complete before
moving to the next one. This stacks all the latencies on top of each other.

```
❌  Slow (Sequential):
    STT finishes → LLM finishes → TTS starts → Audio plays
    Total: 300ms + 800ms + 400ms = 1,500ms of silence

✅  Fast (Streaming):
    STT streams partials → LLM starts immediately
    LLM streams tokens  → TTS starts at first word
    Total: ~600ms (stages overlap)
```

**The Fix — 3 things to check:**

1. **STT → LLM**: Don't wait for the "final" transcript. Send partial
   transcripts to the LLM as they arrive. The LLM can start forming a
   response before the user even finishes speaking.

2. **LLM → TTS**: Don't wait for the full LLM response. The moment the
   first few tokens arrive, send them to TTS and start generating audio.

3. **TTS → Speaker**: Don't buffer the full audio clip. Stream audio
   chunks to the speaker as each one is ready.

**Expected improvement:** Cuts total pipeline latency by 40–60%.
Conversation feels live and responsive instead of sluggish.

---

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 03 — Pre-Cache Common Responses as Audio
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**The Problem**
A clinical receptionist AI says the same things over and over:
greetings, confirmations, hold messages, error responses. Every time,
the system wastes 200–400ms generating TTS audio it has already
generated a hundred times before.

**The Fix**
At startup, pre-generate audio for all predictable phrases and store them
as ready-to-play audio files. When the AI needs to say one, skip TTS
entirely and play the file directly.

**Phrases worth pre-caching:**
```
Greetings        →  "Hello, welcome to the clinic. How can I help you?"
Confirmations    →  "Your appointment has been booked successfully."
Hold messages    →  "Please hold while I check availability."
Apologies        →  "I'm sorry, I didn't catch that. Could you repeat?"
Farewells        →  "Thank you for calling. Have a great day!"
Errors           →  "I'm having trouble accessing that right now."
```

**How to implement:**
- Generate these once when the server starts (or pre-generate offline)
- Store as `.wav` or `.pcm` files named clearly (e.g. `greeting_en.wav`)
- Before calling TTS, check if a cached version exists — if yes, play it

**Expected improvement:** 0ms latency for cached phrases (vs 200–400ms
for live TTS). Greetings and confirmations feel instant.

---

## Quick Reference

| # | Optimization | Effort | Latency Saved |
|---|-------------|--------|---------------|
| 1 | Filler audio during tool calls | Low | 500–2000ms hidden |
| 2 | Full streaming pipeline | Medium | 40–60% reduction |
| 3 | Pre-cache common responses | Low | 200–400ms per phrase |

---

> Start with **#1 and #3** — they're both low effort and give immediate
> results. Tackle **#2** once the basics are working.
