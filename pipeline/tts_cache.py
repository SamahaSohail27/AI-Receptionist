"""
Language-namespaced disk-backed LRU TTS cache.

Cache layout:
    {cache_dir}/{language_code}/{sha256_of_text}.wav

In-memory LRU keeps the most recently used 1000 entries for fast hits.
Each language lives in its own sub-directory, preventing cross-language
hash collisions and making per-language eviction trivial.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pre-warm phrase registry
# ---------------------------------------------------------------------------

PREWARM_PHRASES: dict[str, list[str]] = {
    "ur-PK": [
        "السلام علیکم، میں ڈاکٹر صاحب کا AI ریسپشنسٹ ہوں",
        "ایک لمحہ، میں چیک کر رہا ہوں",
        "آپ کا وقت بک ہو گیا ہے",
        "شکریہ، خدا حافظ",
    ],
    "pa-PK": [
        "السلام علیکم، ہن دسو کی مدد کراں",
        "اک پل، میں ویکھدا ہاں",
        "تہاڈا ویلہ بک ہو گیا",
    ],
    "en": [
        "Thank you for calling. How can I help you today?",
        "One moment, let me check that for you.",
        "Your appointment has been booked.",
        "Thank you. Goodbye.",
    ],
}

# In-memory LRU capacity
_LRU_MAX = 1000


class TTSAudioCache:
    """
    Language-namespaced disk-backed LRU cache for TTS audio.

    Thread safety: all public methods are safe to call from a single asyncio
    event loop. Do not share across processes.
    """

    def __init__(
        self,
        cache_dir: str = "tts_cache",
        max_size_mb: int = 500,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._max_size_mb = max_size_mb
        # LRU dict: key=(language, text_hash) → filepath str
        self._lru: OrderedDict[tuple[str, str], str] = OrderedDict()

        # Ensure root cache directory exists
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("TTSAudioCache initialised at %s (max %dMB)", self._cache_dir, max_size_mb)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _text_hash(text: str) -> str:
        """Return sha256 hex digest of the input text."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _lang_dir(self, language: str) -> Path:
        """Return (and create if needed) the directory for a language."""
        d = self._cache_dir / language
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _filepath(self, language: str, text_hash: str) -> Path:
        return self._lang_dir(language) / f"{text_hash}.wav"

    def _evict_lru(self) -> None:
        """Evict the oldest entry if LRU is over capacity."""
        while len(self._lru) > _LRU_MAX:
            evicted_key, _ = self._lru.popitem(last=False)
            logger.debug("TTSAudioCache: evicted LRU key %s/%s", *evicted_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, language: str, text: str) -> bytes | None:
        """
        Return cached audio bytes for (language, text), or None on miss.
        Promotes the entry to MRU position on hit.
        """
        text_hash = self._text_hash(text)
        key = (language, text_hash)

        # 1. Fast path: in-memory LRU
        if key in self._lru:
            self._lru.move_to_end(key)
            filepath = self._lru[key]
            try:
                with open(filepath, "rb") as f:
                    return f.read()
            except OSError:
                # File was deleted externally — remove from LRU
                del self._lru[key]
                logger.debug("TTSAudioCache: LRU entry missing on disk, removed: %s", filepath)
                return None

        # 2. Disk fallback
        filepath = self._filepath(language, text_hash)
        if filepath.exists():
            try:
                data = filepath.read_bytes()
                # Warm in-memory LRU
                self._lru[key] = str(filepath)
                self._lru.move_to_end(key)
                self._evict_lru()
                return data
            except OSError as exc:
                logger.warning("TTSAudioCache: disk read error %s: %s", filepath, exc)
                return None

        return None

    def put(self, language: str, text: str, audio: bytes) -> None:
        """
        Persist audio bytes to disk and add to in-memory LRU.
        Silently overwrites an existing entry.
        """
        text_hash = self._text_hash(text)
        filepath = self._filepath(language, text_hash)

        try:
            filepath.write_bytes(audio)
        except OSError as exc:
            logger.error("TTSAudioCache: failed to write %s: %s", filepath, exc)
            return

        key = (language, text_hash)
        self._lru[key] = str(filepath)
        self._lru.move_to_end(key)
        self._evict_lru()
        logger.debug("TTSAudioCache: stored %s/%s (%d bytes)", language, text_hash[:8], len(audio))

    def clear_language(self, language: str) -> int:
        """
        Delete all cached files for one language. Returns count of files deleted.
        Also purges matching keys from the in-memory LRU.
        """
        lang_dir = self._cache_dir / language
        count = 0

        if lang_dir.exists():
            for wav_file in lang_dir.glob("*.wav"):
                try:
                    wav_file.unlink()
                    count += 1
                except OSError as exc:
                    logger.warning("TTSAudioCache: could not delete %s: %s", wav_file, exc)
            try:
                lang_dir.rmdir()
            except OSError:
                pass  # Non-empty — leave the directory

        # Purge in-memory LRU entries for this language
        stale_keys = [k for k in self._lru if k[0] == language]
        for k in stale_keys:
            del self._lru[k]

        logger.info("TTSAudioCache: cleared %d files for language %r", count, language)
        return count

    def stats(self) -> dict:
        """Return cache statistics including per-language file counts and disk usage."""
        lang_counts: dict[str, int] = {}
        total_bytes = 0

        if self._cache_dir.exists():
            for lang_dir in self._cache_dir.iterdir():
                if lang_dir.is_dir():
                    wav_files = list(lang_dir.glob("*.wav"))
                    lang_counts[lang_dir.name] = len(wav_files)
                    for f in wav_files:
                        try:
                            total_bytes += f.stat().st_size
                        except OSError:
                            pass

        return {
            "total_entries": sum(lang_counts.values()),
            "languages": lang_counts,
            "cache_dir_mb": round(total_bytes / (1024 * 1024), 2),
        }


# ---------------------------------------------------------------------------
# Pre-warm helper
# ---------------------------------------------------------------------------

async def warm_tts_cache() -> None:
    """
    Pre-warm the TTS cache with common greeting and confirmation phrases.

    Uses the appropriate TTS provider per language profile:
      - ur-PK / pa-PK → Azure Neural TTS
      - en             → OpenAI TTS

    Errors are logged and skipped so startup is never blocked.
    """
    from core.config import settings
    from providers.language_profile import get_profile

    cache = TTSAudioCache(
        cache_dir=settings.tts_cache_dir,
        max_size_mb=settings.tts_cache_max_mb,
    )

    for lang_code, phrases in PREWARM_PHRASES.items():
        profile = get_profile(lang_code)

        # Lazily import and instantiate the TTS adapter for this language
        try:
            if profile.tts.provider == "azure":
                from providers.tts.azure_neural import AzureNeuralTTSAdapter
                tts_adapter = AzureNeuralTTSAdapter(
                    language=profile.tts.language_code,
                    voice=profile.tts.voice,
                )
            elif profile.tts.provider == "openai":
                from providers.tts.openai_tts import OpenAITTSAdapter
                tts_adapter = OpenAITTSAdapter(
                    language=profile.tts.language_code,
                    voice=profile.tts.voice,
                )
            else:
                logger.warning(
                    "warm_tts_cache: unknown TTS provider %r for language %r — skipping",
                    profile.tts.provider,
                    lang_code,
                )
                continue
        except Exception as exc:
            logger.warning(
                "warm_tts_cache: could not instantiate TTS adapter for %r: %s — skipping",
                lang_code,
                exc,
            )
            continue

        for phrase in phrases:
            # Skip if already cached
            if cache.get(lang_code, phrase) is not None:
                logger.debug("warm_tts_cache: already cached [%s] %r", lang_code, phrase[:40])
                continue

            try:
                result = await tts_adapter.synthesize(phrase)
                cache.put(lang_code, phrase, result.audio_bytes)
                logger.info(
                    "warm_tts_cache: cached [%s] %r (%d bytes)",
                    lang_code,
                    phrase[:40],
                    len(result.audio_bytes),
                )
            except Exception as exc:
                logger.warning(
                    "warm_tts_cache: synthesis failed for [%s] %r: %s",
                    lang_code,
                    phrase[:40],
                    exc,
                )

    stats = cache.stats()
    logger.info(
        "warm_tts_cache: complete — %d total entries, %.1fMB on disk",
        stats["total_entries"],
        stats["cache_dir_mb"],
    )
