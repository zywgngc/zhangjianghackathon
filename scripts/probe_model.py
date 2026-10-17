"""Reproducible text/vision smoke probe. Reads credentials only from server .env."""
import asyncio
import base64
import json
import time
from pathlib import Path

from game_harness.agents import AgentClient
from game_harness.config import load_settings


async def main():
    settings = load_settings()
    agents = AgentClient(settings, None)
    outputs = {}
    try:
        for mode in ("text", "vision"):
            content = "只回复：连接成功"
            if mode == "vision":
                encoded = base64.b64encode((Path(__file__).resolve().parents[1] / "tests/fixtures/vision-probe.png").read_bytes()).decode()
                content = [{"type": "text", "text": "请根据图片输出JSON：{shapes:[{color,shape,position}],number_in_red_shape:字符串,total_shapes:整数,chinese_text:[字符串]}。用中文，区分正方形与长方形，读出全部文字与数字。"},
                           {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}}]
            start = time.monotonic()
            response = await agents.client.chat.completions.create(model=settings.model, messages=[{"role": "user", "content": content}], max_tokens=1536)
            outputs[mode] = {"model": response.model, "content": response.choices[0].message.content,
                             "finish_reason": response.choices[0].finish_reason,
                             "usage": response.usage.model_dump() if response.usage else None,
                             "seconds": round(time.monotonic() - start, 2)}
        path = settings.data_dir / "diagnostics"
        path.mkdir(parents=True, exist_ok=True)
        (path / "model-probe.json").write_text(json.dumps(outputs, ensure_ascii=False, indent=2))
        print(json.dumps(outputs, ensure_ascii=False, indent=2))
    finally:
        await agents.close()


if __name__ == "__main__":
    asyncio.run(main())
