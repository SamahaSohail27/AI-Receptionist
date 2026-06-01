from __future__ import annotations

import os
import time
from typing import AsyncIterator

import google.generativeai as genai
from google.generativeai import GenerativeModel
from google.generativeai.types import GenerationConfig

from providers.base import BaseLLM, LLMMessage, LLMResponse

_FAST_MODEL = "gemini-2.0-flash"
_QUALITY_MODEL = "gemini-1.5-pro"


class GeminiLLMAdapter(BaseLLM):
    def __init__(self, model_tier: str = "fast") -> None:
        super().__init__(model_tier)
        api_key = os.environ["GOOGLE_API_KEY"]
        genai.configure(api_key=api_key)
        self._model_name = _FAST_MODEL if model_tier == "fast" else _QUALITY_MODEL
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

        model = GenerativeModel(
            model_name=self._model_name,
            system_instruction=effective_system,
        )

        # Convert messages to Gemini contents format
        contents = _build_gemini_contents(messages)

        gen_config = GenerationConfig(temperature=temperature)

        kwargs: dict = {
            "contents": contents,
            "generation_config": gen_config,
            "stream": True,
        }

        # Gemini tool_choice is always auto by default; pass tools if present
        if tools:
            gemini_tools = _convert_tools_to_gemini(tools)
            kwargs["tools"] = gemini_tools

        start_ms = time.monotonic()
        collected_text = ""
        finish_reason = "stop"
        input_tokens = 0
        output_tokens = 0
        collected_tool_calls: list[dict] = []

        response = await model.generate_content_async(**kwargs)

        # generate_content_async with stream=True returns an async generator
        async for chunk in response:
            if not chunk.candidates:
                continue

            candidate = chunk.candidates[0]

            # Collect text parts
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    collected_text += part.text
                    yield part.text
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    import json as _json
                    args_dict = dict(fc.args) if fc.args else {}
                    collected_tool_calls.append(
                        {
                            "id": f"gemini_{fc.name}_{int(time.monotonic()*1000)}",
                            "type": "function",
                            "function": {
                                "name": fc.name,
                                "arguments": _json.dumps(args_dict),
                            },
                        }
                    )

            if candidate.finish_reason:
                finish_reason = str(candidate.finish_reason)

        # Usage metadata is on the final response object
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0)
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0)

        latency_ms = int((time.monotonic() - start_ms) * 1000)

        yield LLMResponse(
            text=collected_text,
            tool_calls=collected_tool_calls,
            finish_reason=finish_reason,
            provider=self.provider_name,
            model=self._model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Language profile
    # ------------------------------------------------------------------

    def set_language_profile(self, language_code: str, system_prompt: str) -> None:
        self._language_system_prompt = system_prompt

    def cache_prefix(self) -> str | None:
        return None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_name


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_gemini_contents(messages: list[LLMMessage]) -> list[dict]:
    """Convert LLMMessage list to Gemini contents format."""
    contents = []
    for msg in messages:
        if msg.role == "system":
            # system is passed via system_instruction; skip here
            continue
        elif msg.role == "tool":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "name": msg.tool_call_id or "tool",
                                "response": {"result": msg.content},
                            }
                        }
                    ],
                }
            )
        elif msg.role == "assistant":
            parts: list[dict] = []
            if msg.content:
                parts.append({"text": msg.content})
            if msg.tool_calls:
                import json as _json
                for tc in msg.tool_calls:
                    try:
                        args = _json.loads(tc["function"]["arguments"])
                    except Exception:
                        args = {}
                    parts.append(
                        {
                            "function_call": {
                                "name": tc["function"]["name"],
                                "args": args,
                            }
                        }
                    )
            contents.append({"role": "model", "parts": parts})
        else:
            contents.append({"role": "user", "parts": [{"text": msg.content}]})
    return contents


def _convert_tools_to_gemini(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-style function tool dicts to Gemini function declarations."""
    function_declarations = []
    for tool in tools:
        if tool.get("type") == "function":
            fn = tool["function"]
            function_declarations.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
    if function_declarations:
        return [{"function_declarations": function_declarations}]
    return []
