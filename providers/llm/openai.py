from __future__ import annotations

import os
import time
from typing import AsyncIterator

from openai import AsyncOpenAI

from providers.base import BaseLLM, LLMMessage, LLMResponse

_FAST_MODEL = "gpt-4o-mini"
_QUALITY_MODEL = "gpt-4o"


class OpenAILLMAdapter(BaseLLM):
    def __init__(self, model_tier: str = "fast") -> None:
        super().__init__(model_tier)
        api_key = os.environ["OPENAI_API_KEY"]
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = _FAST_MODEL if model_tier == "fast" else _QUALITY_MODEL
        self._language_system_prompt: str | None = None

    # ------------------------------------------------------------------
    # stream_response
    # ------------------------------------------------------------------

    async def stream_response(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[str | LLMResponse]:
        oai_messages: list[dict] = []

        # System message: explicit arg takes priority over language profile
        effective_system = system_prompt or self._language_system_prompt
        if effective_system:
            oai_messages.append({"role": "system", "content": effective_system})

        for msg in messages:
            if msg.role == "tool":
                oai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id,
                        "content": msg.content,
                    }
                )
            elif msg.role == "assistant" and msg.tool_calls:
                oai_messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or None,
                        "tool_calls": msg.tool_calls,
                    }
                )
            else:
                oai_messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict = {
            "model": self._model,
            "messages": oai_messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        else:
            kwargs["tool_choice"] = "none"

        start_ms = time.monotonic()
        stream = await self._client.chat.completions.create(**kwargs)

        collected_text = ""
        collected_tool_calls: list[dict] = []
        finish_reason = "stop"
        input_tokens = 0
        output_tokens = 0

        # tool_calls arrive in chunks; we rebuild them by index
        tool_call_chunks: dict[int, dict] = {}

        async for chunk in stream:
            # Usage is sent in the final chunk when stream_options.include_usage=True
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            delta = choice.delta

            if delta.content:
                collected_text += delta.content
                yield delta.content

            if delta.tool_calls:
                for tc_chunk in delta.tool_calls:
                    idx = tc_chunk.index
                    if idx not in tool_call_chunks:
                        tool_call_chunks[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    entry = tool_call_chunks[idx]
                    if tc_chunk.id:
                        entry["id"] += tc_chunk.id
                    if tc_chunk.function:
                        if tc_chunk.function.name:
                            entry["function"]["name"] += tc_chunk.function.name
                        if tc_chunk.function.arguments:
                            entry["function"]["arguments"] += tc_chunk.function.arguments

        collected_tool_calls = list(tool_call_chunks.values())
        latency_ms = int((time.monotonic() - start_ms) * 1000)

        yield LLMResponse(
            text=collected_text,
            tool_calls=collected_tool_calls,
            finish_reason=finish_reason,
            provider=self.provider_name,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Language profile
    # ------------------------------------------------------------------

    def set_language_profile(self, language_code: str, system_prompt: str) -> None:
        self._language_system_prompt = system_prompt

    # ------------------------------------------------------------------
    # Caching — OpenAI caches automatically on identical prefix ≥1024 tokens
    # ------------------------------------------------------------------

    def cache_prefix(self) -> str | None:
        return None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model
