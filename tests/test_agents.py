import json

import httpx
from openai import AsyncOpenAI

from game_harness.agents import AgentClient
from game_harness.schemas import Brief


async def test_bad_json_retries_and_accounts_usage(settings, store, request_data, contracts):
    rid = store.create(request_data)["id"]
    store.claim()
    responses = iter(["this is not JSON", json.dumps(contracts["brief"], ensure_ascii=False)])

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
