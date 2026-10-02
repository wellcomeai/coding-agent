"""Клиент LLM через Timeweb AI Gateway (OpenAI-совместимый chat/completions со стримингом и tool calling)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from ..config import get_settings


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    estimated: bool = False


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None

    def as_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg


class LLMClient:
    """Интерфейс, который можно подменить в тестах."""

    async def complete(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResult:
        raise NotImplementedError


class GatewayLLM(LLMClient):
    def __init__(self) -> None:
        s = get_settings()
        self.client = AsyncOpenAI(
            base_url=s.ai_gateway_base_url, api_key=s.ai_gateway_api_key or "missing", timeout=s.llm_timeout_seconds
        )

    async def complete(self, model, messages, tools, on_delta=None) -> LLMResult:
        s = get_settings()
        stream = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools or None,
            max_tokens=s.agent_max_output_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        result = LLMResult()
        calls: dict[int, dict] = {}
        async for chunk in stream:
            if chunk.usage:
                u = chunk.usage
                details = getattr(u, "prompt_tokens_details", None)
                result.usage = Usage(
                    prompt_tokens=u.prompt_tokens or 0,
                    completion_tokens=u.completion_tokens or 0,
                    cached_tokens=(getattr(details, "cached_tokens", 0) or 0) if details else 0,
                )
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if choice.finish_reason:
                result.finish_reason = choice.finish_reason
            if delta is None:
                continue
            if delta.content:
                result.content += delta.content
                if on_delta:
                    await on_delta(delta.content)
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        slot["function"]["name"] += tc.function.name
                    if tc.function.arguments:
                        slot["function"]["arguments"] += tc.function.arguments
        result.tool_calls = [calls[i] for i in sorted(calls)]
        for i, c in enumerate(result.tool_calls):
            c["id"] = c["id"] or f"call_{i}"
            c["function"]["arguments"] = c["function"]["arguments"] or "{}"
        if not result.usage.prompt_tokens and not result.usage.completion_tokens:
            # Gateway не вернул usage — оцениваем грубо (≈3.5 символа на токен), чтобы не работать бесплатно
            prompt_chars = sum(len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or "")) for m in messages)
            out_chars = len(result.content) + sum(len(c["function"]["arguments"]) for c in result.tool_calls)
            result.usage = Usage(int(prompt_chars / 3.5), int(out_chars / 3.5), estimated=True)
        return result


_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = GatewayLLM()
    return _llm


def set_llm(llm: LLMClient | None) -> None:
    global _llm
    _llm = llm
