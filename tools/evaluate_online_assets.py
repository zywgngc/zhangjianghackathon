"""Run real public-website art retrieval; never reads keys or calls a model."""

import argparse
import asyncio
import base64
import hashlib
import html
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "wuxia": "2D俯视武侠幸存者游戏，需要青衣侠客/山贼角色、竹林森林地面、飞剑和经验拾取物。美术关键词 samurai, forest, sword; 希望统一像素风格。可接受通用幻想美术作为候选，须披露与武侠题材的差异。",
    "number-gate": "2D俯视数字门枪手，需要soldier士兵玩家和敌人、自动射击弹丸、纵向road道路、沙袋障碍和装甲车。优先完整topdown shooter素材包，算术门与人数文字由游戏绘制。",
    "power-hunter": "2D俯视森林战力吞噬游戏，需要forest草地和树木、rpg英雄、monster怪物/巨魔Boss、木剑铁剑sword武器。数字战力标签由游戏绘制。希望选择可缩放的PNG精灵或有明确布局的图集。",
}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--output", default="output/online-assets-live")
    args = parser.parse_args()
    out = (ROOT / args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("online_portal_live", ROOT / "game_harness/asset_material_portal.py")
    portal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(portal)
    settings = SimpleNamespace(data_dir=out / "data", portal_url="web://assets", portal_token="",
        portal_schema=ROOT / "webmcp/material-portal.schema.json")
    client = portal.MaterialPortalClient(settings)
    reports = {}
    for name, description in CASES.items():
        if args.case and name != args.case:
            continue
        request = client.request_for(description, 4, "online-art-evaluation-" + name)
        handoff = await client.collect_assets(request)
        evidence = await client.search(description, 4, "online-art-evaluation-" + name)
        audit_file = settings.data_dir / "material-assets/searches" / (hashlib.sha256(request["requestId"].encode()).hexdigest() + ".json")
        audit = json.loads(audit_file.read_text("utf-8"))
        report = {"case": name, "request": request, **handoff,
                  "portalSha256": hashlib.sha256((ROOT / "game_harness/asset_material_portal.py").read_bytes()).hexdigest(),
                  "collectorVersion": portal._WEB_VERSION,
                  "network": {k: audit[k] for k in ("queries", "networkRequests", "networkBytes", "cacheHits")},
                  "ragDocuments": len(evidence["documents"]),
                  "ragInlineImages": sum(len(d.get("assets", [])) for d in evidence["documents"]),
                  "modelCalls": 0}
        reports[name] = report
        (out / (name + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"case": name, "status": handoff["result"]["status"],
              "candidates": [c["title"] for c in handoff["result"]["candidates"]],
              "assets": len(handoff["assets"]), "network": report["network"],
              "ragInlineImages": report["ragInlineImages"], "limitations": handoff["limitations"][:7]}, ensure_ascii=False), flush=True)
    # Include prior completed cases when a single case is resumed.
    for name in CASES:
        path = out / (name + ".json")
        if name not in reports and path.is_file():
            reports[name] = json.loads(path.read_text("utf-8"))
    cards = []
    for name, report in reports.items():
        for candidate in report["result"]["candidates"]:
            manifest_path = Path(candidate["source"]["paths"][0])
            manifest = portal._read_asset_manifest(manifest_path)
            images = []
            for asset in manifest["assets"][:6]:
                payload = Path(asset["absolutePath"]).read_bytes()
                data_uri = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
                images.append('<figure><img src="' + data_uri + '"><figcaption>'
                    + html.escape(asset["originalFilename"]) + '<br>' + str(asset["width"]) + ' × '
                    + str(asset["height"]) + '</figcaption></figure>')
            cards.append('<section><small>' + html.escape(name) + ' · CC0</small><h2>'
                + html.escape(candidate["title"]) + '</h2><a href="' + html.escape(candidate["source"]["uri"], quote=True)
                + '">查看原始来源</a><div class="assets">' + ''.join(images) + '</div></section>')
    gallery = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>联网素材检索实测</title><style>body{font:16px/1.6 system-ui;background:#eef2f5;color:#182830;margin:24px auto;padding:0 18px;max-width:1200px}h1{margin-bottom:0}section{background:white;border-radius:16px;padding:22px;margin:20px 0}h2{margin:6px 0}small{color:#536773}.assets{display:flex;flex-wrap:wrap;gap:16px}figure{margin:18px 0 0;max-width:320px}img{width:180px;height:150px;object-fit:contain;image-rendering:pixelated;background:repeating-conic-gradient(#e3e7ec 0 25%,#f5f7f9 0 50%) 50%/16px 16px;border:1px solid #d4dbe0;border-radius:8px}figcaption{font-size:11px;overflow-wrap:anywhere;max-width:210px}a{color:#246788}</style>
<h1>联网素材检索实测</h1><p>通过素材站真实搜索，下载原文件并核对尺寸、校验和及来源页 CC0 声明。下面是待选美术候选；风格贴合与图集分帧仍需审阅。</p>'''
    (out / "gallery.html").write_text(gallery + ''.join(cards) + '</html>', encoding="utf-8")
    print(json.dumps({"gallery": str(out / "gallery.html"), "cases": len(reports), "modelCalls": 0}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
