from __future__ import annotations

import logging
import os
from datetime import datetime

import aiohttp

from providers.base import BaseTelephony, CallTransfer

logger = logging.getLogger(__name__)


class TwilioAdapter(BaseTelephony):
    def __init__(self) -> None:
        self._account_sid: str = os.environ["TWILIO_ACCOUNT_SID"]
        self._auth_token: str = os.environ["TWILIO_AUTH_TOKEN"]
        self._base_url: str = (
            f"https://api.twilio.com/2010-04-01/Accounts/{self._account_sid}/"
        )
        self._dtmf_buffer: dict[str, str] = {}

    @property
    def provider_name(self) -> str:
        return "twilio"

    async def handle_inbound(self, webhook_data: dict) -> dict:
        return {
            "session_id": webhook_data["CallSid"],
            "caller_number": webhook_data["From"],
            "called_number": webhook_data["To"],
            "provider": "twilio",
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def handle_dtmf(self, session_id: str, digit: str) -> None:
        self._dtmf_buffer[session_id] = digit

    async def transfer_call(self, transfer: CallTransfer) -> bool:
        twiml_transfer_url = (
            f"https://handler.twilio.com/twiml/dial?number={transfer.target_number}"
        )
        payload = {
            "Url": twiml_transfer_url,
            "Method": "POST",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base_url}Calls/{transfer.session_id}.json",
                data=payload,
                auth=aiohttp.BasicAuth(self._account_sid, self._auth_token),
            ) as resp:
                success = resp.status // 100 == 2
                if not success:
                    body = await resp.text()
                    logger.error(
                        "Twilio transfer_call failed status=%s body=%s",
                        resp.status,
                        body,
                    )
                return success

    async def end_call(self, session_id: str, reason: str = "completed") -> None:
        payload = {"Status": "completed"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base_url}Calls/{session_id}.json",
                data=payload,
                auth=aiohttp.BasicAuth(self._account_sid, self._auth_token),
            ) as resp:
                if resp.status // 100 != 2:
                    body = await resp.text()
                    logger.error(
                        "Twilio end_call failed status=%s body=%s",
                        resp.status,
                        body,
                    )

    async def send_dtmf_prompt(self, session_id: str, audio_url: str) -> None:
        twiml_url = f"https://handler.twilio.com/twiml/play?url={audio_url}"
        payload = {"Url": twiml_url}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base_url}Calls/{session_id}.json",
                data=payload,
                auth=aiohttp.BasicAuth(self._account_sid, self._auth_token),
            ) as resp:
                if resp.status // 100 != 2:
                    body = await resp.text()
                    logger.error(
                        "Twilio send_dtmf_prompt failed status=%s body=%s",
                        resp.status,
                        body,
                    )
