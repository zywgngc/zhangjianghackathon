"""Reproducible text/vision smoke probe. Reads credentials only from server .env."""
import asyncio
import base64
import json
import re
import time
from pathlib import Path

from game_harness.agents import AgentClient
from game_harness.config import load_settings


def grade_vision(content):
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        result = json.loads(content)
        shapes = result["shapes"]
        text = " ".join(result["chinese_text"])
        checks = {
            "red_circle_top_left": any("红" in s["color"] and "圆" in s["shape"] and "左" in s["position"] and "上" in s["position"] for s in shapes),
            "blue_triangle_top_right": any("蓝" in s["color"] and "三角" in s["shape"] and "右" in s["position"] and "上" in s["position"] for s in shapes),
            "green_rectangle": any("绿" in s["color"] and any(t in s["shape"] for t in ["长方", "矩形"]) for s in shapes),
            "number_47": str(result["number_in_red_shape"]) == "47",
            "shape_count_3": result["total_shapes"] == 3 and len(shapes) == 3,
            "chinese_start": "开始游戏" in text,
            "score_128": "得分" in text and "128" in text,
            "lives_3": "生命" in text and "3" in text,
        }
        return {"passed": all(checks.values()), "checks": checks}
    except (ValueError, KeyError, TypeError):
        return {"passed": False, "error": "视觉输出未满足探针 JSON 契约"}


async def main():
    settings = load_settings()
    agents = AgentClient(settings, None)
    outputs = {}
    model_options = {"reasoning_effort": settings.reasoning_effort} if settings.reasoning_effort else {}
    try:
        for mode in ("text", "vision"):
            content = "只回复：连接成功"
            if mode == "vision":
                encoded = base64.b64encode((Path(__file__).resolve().parents[1] / "tests/fixtures/vision-probe.png").read_bytes()).decode()
                content = [{"type": "text", "text": "请根据图片输出JSON：{shapes:[{color,shape,position}],number_in_red_shape:字符串,total_shapes:整数,chinese_text:[字符串]}。用中文，区分正方形与长方形，读出全部文字与数字。"},
                           {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}}]
            start = time.monotonic()
            response = await agents.client.chat.completions.create(model=settings.model, messages=[{"role": "user", "content": content}], max_tokens=1536, **model_options)
            outputs[mode] = {"model": response.model, "content": response.choices[0].message.content,
                             "finish_reason": response.choices[0].finish_reason,
                             "usage": response.usage.model_dump() if response.usage else None,
                             "seconds": round(time.monotonic() - start, 2)}
        start = time.monotonic()
        stream = await agents.client.chat.completions.create(model=settings.model,
            messages=[{"role": "user", "content": "只回复：流式接口正常"}], max_tokens=128,
            stream=True, stream_options={"include_usage": True}, **model_options)
        chunks, usage = [], None
        try:
            async for chunk in stream:
                if chunk.usage:
                    usage = chunk.usage.total_tokens
                for choice in chunk.choices:
                    if choice.delta.content:
                        chunks.append(choice.delta.content)
        finally:
            await stream.close()
        outputs["stream"] = {"content": "".join(chunks), "tokens": usage, "seconds": round(time.monotonic() - start, 2)}
        outputs["checks"] = {"text_passed": outputs["text"]["content"].strip() == "连接成功",
                             "stream_passed": outputs["stream"]["content"].strip() == "流式接口正常" and usage is not None,
                             "vision": grade_vision(outputs["vision"]["content"])}
        path = settings.data_dir / "diagnostics"
        path.mkdir(parents=True, exist_ok=True)
        (path / "model-probe.json").write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(outputs, ensure_ascii=False, indent=2))
        if not outputs["checks"]["text_passed"] or not outputs["checks"]["stream_passed"] or not outputs["checks"]["vision"]["passed"]:
            raise SystemExit(1)
    finally:
        await agents.close()


if __name__ == "__main__":
    asyncio.run(main())
