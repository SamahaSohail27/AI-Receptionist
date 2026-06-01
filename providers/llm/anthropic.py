from __future__ import annotations

import os
import time
from typing import AsyncIterator

import anthropic as _anthropic
from anthropic import AsyncAnthropic

from providers.base import BaseLLM, LLMMessage, LLMResponse

_FAST_MODEL = "claude-haiku-4-5-20251001"
_QUALITY_MODEL = "claude-sonnet-4-6"

_CACHE_PREFIX_LEN = 1024


class AnthropicLLMAdapter(BaseLLM):
    def __init__(self, model_tier: str = "fast") -> None:
        super().__init__(model_tier)
        api_key = os.environ["ANTHROPIC_API_KEY"]
        self._client = AsyncAnthropic(api_key=api_key)
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
        effective_system = system_prompt or self._language_system_prompt

        # Anthropic prompt caching: mark system prompt block as ephemeral
        cached_system: list[dict] | str
        if effective_system:
            cached_system = [
                {
                    "type": "text",
                    "text": effective_system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            cached_system = []

        anthropic_messages: list[dict] = []
        for msg in messages:
            if msg.role == "system":
                # system messages are passed separately; skip here
                continue
            elif msg.role == "tool":
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content,
                            }
                        ],
                    }
                )
            elif msg.role == "assistant" and msg.tool_calls:
                content_blocks: list[dict] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    import json as _json
                    raw_input = tc.get("function", {}).get("arguments", "{}")
                    try:
                        parsed_input = _json.loads(raw_input)
                    except Exception:
                        parsed_input = {}
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["function"]["name"],
                            "input": parsed_input,
                        }
                    )
                anthropic_messages.append({"role": "assistant", "content": content_blocks})
            else:
                anthropic_messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict = {
            "model": self._model,
            "max_tokens": 1024,
            "temperature": temperature,
            "messages": anthropic_messages,
        }
        if cached_system:
            kwargs["system"] = cached_system

        if tools:
            # Convert OpenAI-style tool dicts to Anthropic format if needed
            anthropic_tools = _convert_tools_to_anthropic(tools)
            kwargs["tools"] = anthropic_tools
            kwargs["tool_choice"] = {"type": "auto"}
        else:
            kwargs["tool_choice"] = {"type": "none"}

        start_ms = time.monotonic()

        collected_text = ""
        collected_tool_calls: list[dict] = []
        finish_reason = "stop"
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0

        # tool_use blocks accumulate across events
        current_tool_use: dict | None = None
        tool_input_json = ""

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                event_type = type(event).__name__

                if event_type == "RawMessageStartEvent":
                    usage = getattr(event.message, "usage", None)
                    if usage:
                        input_tokens = getattr(usage, "input_tokens", 0)
                        cached_tokens = getattr(usage, "cache_read_input_tokens", 0)

                elif event_type == "RawMessageDeltaEvent":
                    delta = getattr(event, "delta", None)
                    if delta:
                        finish_reason = getattr(delta, "stop_reason", finish_reason) or finish_reason
                    usage = getattr(event, "usage", None)
                    if usage:
                        output_tokens = getattr(usage, "output_tokens", 0)

                elif event_type == "RawContentBlockStartEvent":
                    block = getattr(event, "content_block", None)
                    if block and getattr(block, "type", None) == "tool_use":
                        current_tool_use = {"id": block.id, "name": block.name}
                        tool_input_json = ""

                elif event_type == "RawContentBlockDeltaEvent":
                    delta = getattr(event, "delta", None)
                    if delta:
                        delta_type = getattr(delta, "type", None)
                        if delta_type == "text_delta":
                            text = getattr(delta, "text", "")
                            if text:
                                collected_text += text
                                yield text
                        elif delta_type == "input_json_delta":
                            tool_input_json += getattr(delta, "partial_json", "")

                elif event_type == "RawContentBlockStopEvent":
                    if current_tool_use is not None:
                        import json as _json
                        try:
                            parsed = _json.loads(tool_input_json)
                        except Exception:
                            parsed = {}
                        collected_tool_calls.append(
                            {
                                "id": current_tool_use["id"],
                                "type": "function",
                                "function": {
                                    "name": current_tool_use["name"],
                                    "arguments": tool_input_json,
                                },
                            }
                        )
                        current_tool_use = None
                        tool_input_json = ""

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
            cached_tokens=cached_tokens,
        )

    # ------------------------------------------------------------------
    # Language profile
    # ------------------------------------------------------------------

    def set_language_profile(self, language_code: str, system_prompt: str) -> None:
        self._language_system_prompt = system_prompt

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    def cache_prefix(self) -> str | None:
        if self._language_system_prompt:
            return self._language_system_prompt[:_CACHE_PREFIX_LEN]
        return None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _convert_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-style function tool dicts to Anthropic tool format."""
    result = []
    for tool in tools:
        if tool.get("type") == "function":
            fn = tool["function"]
            result.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        else:
            # Already in Anthropic format
            result.append(tool)
    return result
