import json

import httpx
from openai import AsyncOpenAI

from app.agent.llm import GatewayLLM


def sse(*chunks) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


def chunk(delta=None, finish=None, usage=None):
    c = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m", "choices": []}
    if delta is not None:
        c["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish}]
    if usage:
        c["usage"] = usage
    return c


async def test_stream_assembles_text_tool_calls_and_usage():
    body = sse(
        chunk({"role": "assistant", "content": "Смотрю "}),
        chunk({"content": "код"}),
        chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "read_", "arguments": ""}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": '{"path":'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "a.py"}'}}]}),
        chunk({"tool_calls": [{"index": 1, "id": "call_2", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]}),
        chunk({}, finish="tool_calls"),
        chunk(usage={"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
                     "prompt_tokens_details": {"cached_tokens": 100}}),
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    llm = GatewayLLM()
    llm.client = AsyncOpenAI(api_key="k", base_url="http://gw/v1", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    deltas = []

    async def on_delta(t):
        deltas.append(t)

    res = await llm.complete("anthropic/claude-sonnet-5", [{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "bash", "parameters": {}}}], on_delta)

    assert captured["stream"] is True and captured["model"] == "anthropic/claude-sonnet-5"
    assert res.content == "Смотрю код" and deltas == ["Смотрю ", "код"]
    assert [c["function"]["name"] for c in res.tool_calls] == ["read_file", "bash"]
    assert json.loads(res.tool_calls[0]["function"]["arguments"]) == {"path": "a.py"}
    assert (res.usage.prompt_tokens, res.usage.completion_tokens, res.usage.cached_tokens) == (120, 30, 100)
    assert res.as_message()["tool_calls"][1]["id"] == "call_2"
