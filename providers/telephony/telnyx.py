from __future__ import annotations

import logging
import os
from datetime import datetime

import aiohttp

from providers.base import BaseTelephony, CallTransfer

logger = logging.getLogger(__name__)


class TelnyxAdapter(BaseTelephony):
    def __init__(self) -> None:
        self._api_key: str = os.environ["TELNYX_API_KEY"]
        self._base_url: str = "https://api.telnyx.com/v2/"
        self._dtmf_buffer: dict[str, str] = {}

    @property
    def provider_name(self) -> str:
        return "telnyx"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def handle_inbound(self, webhook_data: dict) -> dict:
        payload = webhook_data["data"]["payload"]
        return {
            "session_id": payload["call_control_id"],
            "caller_number": payload["from"],
            "called_number": payload["to"],
            "provider": "telnyx",
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def handle_dtmf(self, session_id: str, digit: str) -> None:
        self._dtmf_buffer[session_id] = digit

    async def transfer_call(self, transfer: CallTransfer) -> bool:
        payload = {"to": transfer.target_number}
        async with aiohttp.ClientSession(headers=self._auth_headers()) as session:
            async with session.post(
                f"{self._base_url}calls/{transfer.session_id}/actions/transfer",
                json=payload,
            ) as resp:
                success = resp.status // 100 == 2
                if not success:
                    body = await resp.text()
                    logger.error(
                        "Telnyx transfer_call failed status=%s body=%s",
                        resp.status,
                        body,
                    )
                return success

    async def end_call(self, session_id: str, reason: str = "completed") -> None:
        async with aiohttp.ClientSession(headers=self._auth_headers()) as session:
            async with session.post(
                f"{self._base_url}calls/{session_id}/actions/hangup",
            ) as resp:
                if resp.status // 100 != 2:
                    body = await resp.text()
                    logger.error(
                        "Telnyx end_call failed status=%s body=%s",
                        resp.status,
                        body,
                    )

    async def send_dtmf_prompt(self, session_id: str, audio_url: str) -> None:
        payload = {"audio_url": audio_url}
        async with aiohttp.ClientSession(headers=self._auth_headers()) as session:
            async with session.post(
                f"{self._base_url}calls/{session_id}/actions/playback_start",
                json=payload,
            ) as resp:
                if resp.status // 100 != 2:
                    body = await resp.text()
                    logger.error(
                        "Telnyx send_dtmf_prompt failed status=%s body=%s",
                        resp.status,
                        body,
                    )
