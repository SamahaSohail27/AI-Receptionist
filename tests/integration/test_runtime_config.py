"""Integration tests for the runtime voice settings cache.

Hits the real DB (uses the AsyncSessionLocal from core.database). The dummy
data seeder must have been run at least once. Skips cleanly when DB isn't up.
"""
from __future__ import annotations

import asyncio
import pytest


pytestmark = pytest.mark.asyncio


async def _db_available() -> bool:
    try:
        from sqlalchemy import select
        from core.database import AsyncSessionLocal
        from models.clinic import ProviderConfig
        async with AsyncSessionLocal() as db:
            await db.execute(select(ProviderConfig).limit(1))
        return True
    except Exception:
        return False


async def test_load_voice_settings_round_trip_through_save():
    """Save → invalidate → reload returns the saved values."""
    if not await _db_available():
        pytest.skip("DB not reachable — run alembic + seeder first")

    from sqlalchemy import select
    from core.database import AsyncSessionLocal
    from core.runtime_config import load_voice_settings, invalidate_voice_settings
    from models.clinic import ProviderConfig

    invalidate_voice_settings()
    before = await load_voice_settings()

    new_female = "coral" if before.openai_realtime_female_voice != "coral" else "shimmer"
    new_male = "verse" if before.openai_realtime_male_voice != "verse" else "echo"

    async with AsyncSessionLocal() as db:
        config = (await db.execute(
            select(ProviderConfig).where(ProviderConfig.is_active.is_(True)).limit(1)
        )).scalar_one_or_none()
        assert config is not None, "active ProviderConfig row missing"
        merged = dict(config.language_providers or {})
        ui = dict(merged.get("_ui") or {})
        ui["openai_realtime_voice_female"] = new_female
        ui["openai_realtime_voice_male"] = new_male
        merged["_ui"] = ui
        config.language_providers = merged
        await db.commit()

    invalidate_voice_settings()
    after = await load_voice_settings()
    assert after.openai_realtime_female_voice == new_female
    assert after.openai_realtime_male_voice == new_male

    # Cleanup: revert to original values.
    async with AsyncSessionLocal() as db:
        config = (await db.execute(
            select(ProviderConfig).where(ProviderConfig.is_active.is_(True)).limit(1)
        )).scalar_one_or_none()
        merged = dict(config.language_providers or {})
        ui = dict(merged.get("_ui") or {})
        ui["openai_realtime_voice_female"] = before.openai_realtime_female_voice
        ui["openai_realtime_voice_male"] = before.openai_realtime_male_voice
        merged["_ui"] = ui
        config.language_providers = merged
        await db.commit()
    invalidate_voice_settings()


async def test_voice_for_gender_picks_correct_voice():
    if not await _db_available():
        pytest.skip("DB not reachable")
    from core.runtime_config import load_voice_settings
    s = await load_voice_settings()
    f = s.voice_for_gender("female")
    m = s.voice_for_gender("male")
    assert f
    assert m
    assert f != m, "female and male voices should differ"


async def test_invalidate_clears_cache():
    if not await _db_available():
        pytest.skip("DB not reachable")
    from core.runtime_config import _cache, load_voice_settings, invalidate_voice_settings
    await load_voice_settings()
    assert _cache._cached is not None
    invalidate_voice_settings()
    assert _cache._cached is None
