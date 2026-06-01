from __future__ import annotations

import logging
import os
from datetime import datetime

import aiohttp

from providers.base import BaseTelephony, CallTransfer

logger = logging.getLogger(__name__)


class PlivoAdapter(BaseTelephony):
    def __init__(self) -> None:
        self._auth_id: str = os.environ["PLIVO_AUTH_ID"]
        self._auth_token: str = os.environ["PLIVO_AUTH_TOKEN"]
        self._base_url: str = f"https://api.plivo.com/v1/Account/{self._auth_id}/"
        self._dtmf_buffer: dict[str, str] = {}

    @property
    def provider_name(self) -> str:
        return "plivo"

    async def handle_inbound(self, webhook_data: dict) -> dict:
        return {
            "session_id": webhook_data["CallUUID"],
            "caller_number": webhook_data["From"],
            "called_number": webhook_data["To"],
            "provider": "plivo",
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def handle_dtmf(self, session_id: str, digit: str) -> None:
        self._dtmf_buffer[session_id] = digit

    async def transfer_call(self, transfer: CallTransfer) -> bool:
        transfer_url = (
            f"{self._base_url}Call/{transfer.target_number}/"
        )
        payload = {
            "action": "transfer",
            "aleg_url": transfer_url,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base_url}Call/{transfer.session_id}/",
                json=payload,
                auth=aiohttp.BasicAuth(self._auth_id, self._auth_token),
            ) as resp:
                success = resp.status // 100 == 2
                if not success:
                    body = await resp.text()
                    logger.error(
                        "Plivo transfer_call failed status=%s body=%s",
                        resp.status,
                        body,
                    )
                return success

    async def end_call(self, session_id: str, reason: str = "completed") -> None:
        payload = {"action": "hangup"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base_url}Call/{session_id}/",
                json=payload,
                auth=aiohttp.BasicAuth(self._auth_id, self._auth_token),
            ) as resp:
                if resp.status // 100 != 2:
                    body = await resp.text()
                    logger.error(
                        "Plivo end_call failed status=%s body=%s",
                        resp.status,
                        body,
                    )

    async def send_dtmf_prompt(self, session_id: str, audio_url: str) -> None:
        payload = {"urls": audio_url}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base_url}Call/{session_id}/Play/",
                json=payload,
                auth=aiohttp.BasicAuth(self._auth_id, self._auth_token),
            ) as resp:
                if resp.status // 100 != 2:
                    body = await resp.text()
                    logger.error(
                        "Plivo send_dtmf_prompt failed status=%s body=%s",
                        resp.status,
                        body,
                    )
