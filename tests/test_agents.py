import json

import httpx
import pytest
from openai import AsyncOpenAI

from game_harness.agents import AgentClient
from game_harness.schemas import Brief


async def test_bad_json_retries_and_accounts_usage(settings, store, request_data, contracts):
    rid = store.create(request_data)["id"]
    store.claim()
    responses = iter(["this is not JSON", "Here is the result:\n```json\n" + json.dumps(contracts["brief"], ensure_ascii=False) + "\n```"])

    def respond(request):
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.8-max"
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
            "model": "qwen3.8-max", "choices": [{"index": 0, "message": {"role": "assistant", "content": next(responses)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100}})

    agent = AgentClient(settings, store)
    await agent.client.close()
    agent.client = AsyncOpenAI(api_key="test", base_url="https://mock.invalid/v1", max_retries=0,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        result = await agent.structured(rid, "brief", "rewrite", "改写需求", request_data, Brief)
        assert result.title == "接星星"
        assert store.get(rid)["calls"] == 2
        assert store.get(rid)["tokens"] == 200
        assert any(e["kind"] == "schema_retry" for e in store.events(rid))
    finally:
        await agent.close()


@pytest.mark.parametrize("complete", [True, False])
async def test_stream_requires_terminal_marker_and_preserves_usage(settings, store, request_data, complete):
    rid = store.create(request_data)["id"]
    store.claim()

    def respond(request):
        assert json.loads(request.content)["stream"] is True
        chunks = [{"id": "stream", "object": "chat.completion.chunk", "created": 0, "model": settings.model,
                   "choices": [{"index": 0, "delta": {"content": "<html>游戏</html>"}, "finish_reason": "stop" if complete else None}]},
                  {"id": "stream", "object": "chat.completion.chunk", "created": 0, "model": settings.model,
                   "choices": [], "usage": {"prompt_tokens": 90, "completion_tokens": 33, "total_tokens": 123}}]
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=body)

    agent = AgentClient(settings, store)
    await agent.client.close()
    agent.client = AsyncOpenAI(api_key="test", base_url="https://mock.invalid/v1", max_retries=0,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        if complete:
            assert await agent.complete(rid, "build", "build", "生成游戏", request_data) == "<html>游戏</html>"
        else:
            with pytest.raises(RuntimeError, match="未完整结束"):
                await agent.complete(rid, "build", "build", "生成游戏", request_data)
        assert store.get(rid)["tokens"] == 123
    finally:
        await agent.close()


async def test_actual_provider_usage_over_budget_stops_delivery(settings, store, request_data):
    rid = store.create(request_data)["id"]
    store.claim()
    def respond(request):
        return httpx.Response(200, json={"id": "over-budget", "object": "chat.completion", "created": 0,
            "model": settings.model, "choices": [{"index": 0, "message": {"role": "assistant", "content": "结果"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100000, "completion_tokens": 1, "total_tokens": 100001}})
    agent = AgentClient(settings, store)
    await agent.client.close()
    agent.client = AsyncOpenAI(api_key="test", base_url="https://mock.invalid/v1", max_retries=0,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        with pytest.raises(RuntimeError, match="预算"):
            await agent.complete(rid, "brief", "rewrite", "输出结果", request_data)
        assert store.get(rid)["tokens"] == 100001
    finally:
        await agent.close()


@pytest.mark.parametrize("wrapper", ["Based on the review:\n\n{}", "```json\n{}\n```", "{}"])
async def test_complete_json_with_optional_preamble_needs_no_extra_call(settings, store, request_data, contracts, wrapper):
    rid = store.create(request_data)["id"]
    store.claim()
    agent = AgentClient(settings, store)
    calls = []
    async def complete(*args, **kwargs):
        calls.append(args)
        return wrapper.format(json.dumps(contracts["brief"], ensure_ascii=False))
    agent.complete = complete
    try:
        result = await agent.structured(rid, "brief", "rewrite", "改写需求", request_data, Brief)
        assert result.title == "接星星"
        assert len(calls) == 1
    finally:
        await agent.close()


@pytest.mark.parametrize("suffix", ['\n{"another_answer":true}', '\ntrailing explanation', ''])
async def test_ambiguous_or_incomplete_preamble_output_is_rejected(settings, store, request_data, contracts, suffix):
    rid = store.create(request_data)["id"]
    store.claim()
    agent = AgentClient(settings, store)
    payload = json.dumps(contracts["brief"], ensure_ascii=False)
    content = "Here is JSON:\n" + (payload + suffix if suffix else payload[:-1])
    async def complete(*args, **kwargs):
        return content
    agent.complete = complete
    try:
        with pytest.raises(RuntimeError, match="结构化输出"):
            await agent.structured(rid, "brief", "rewrite", "改写需求", request_data, Brief)
    finally:
        await agent.close()
