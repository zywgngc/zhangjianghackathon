"""Explicit, reproducible download of a small CC-BY-3.0 demo icon collection.

Normal retrieval and delivery never contact these remote sources. Run this
utility only to recreate the already bundled catalog from the pinned upstream.
"""

import argparse
import concurrent.futures
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen


COMMIT = "82d948812bfe3f269ef8f731dcdb07b08160edc4"
BASE = "https://raw.githubusercontent.com/game-icons/icons/" + COMMIT + "/"
LICENSE_URL = "https://creativecommons.org/licenses/by/3.0/"
SPECS = [
    ("guard13007/pause-button.svg", "暂停 Pause", ["pause", "暂停", "暂停比赛"], "Pause the current game"),
    ("guard13007/play-button.svg", "开始 Play", ["play", "resume", "开始", "继续"], "Start or resume a game"),
    ("delapouite/heart-beats.svg", "生命值 Health heart", ["heart", "health", "hp", "生命值", "血量"], "Heart health points indicator"),
    ("delapouite/coins.svg", "金币 Coins", ["coin", "coins", "currency", "gold", "金币", "货币"], "Collectible coins and currency counter"),
    ("lorc/heavy-lightning.svg", "氮气加速 Nitro boost", ["boost", "nitro", "lightning", "加速", "氮气", "闪电"], "Lightning symbol for a speed boost or nitro action"),
    ("delapouite/crosshair.svg", "瞄准 Crosshair", ["aim", "crosshair", "shoot", "瞄准", "准星"], "Aiming reticle for a shooting game"),
    ("lorc/bullets.svg", "弹药 Bullets", ["bullet", "ammo", "ammunition", "射击", "弹药", "子弹"], "Ammunition counter or firing action"),
    ("delapouite/steering-wheel.svg", "方向盘 Steering wheel", ["steer", "steering", "turn", "方向盘", "转向"], "Steering control for a driving game"),
    ("delapouite/speedometer.svg", "速度表 Speedometer", ["speed", "speedometer", "速度", "时速", "仪表"], "Vehicle speed and dashboard indicator"),
    ("delapouite/car-wheel.svg", "车轮 Car wheel", ["wheel", "car", "vehicle", "车轮", "赛车"], "Vehicle wheel or driving equipment"),
    ("delapouite/jump-across.svg", "跳跃 Jump", ["jump", "leap", "跳跃", "跨越"], "Jump over an obstacle"),
    ("delapouite/double-fish.svg", "鱼 Fish", ["fish", "fishing", "鱼", "抓鱼"], "Fish collection or fishing reward"),
    ("delapouite/settings-knobs.svg", "设置 Settings", ["settings", "options", "配置", "设置"], "Game options and settings"),
    ("delapouite/sound-on.svg", "开启声音 Sound on", ["sound", "audio", "unmute", "声音", "开启声音"], "Enable game sound"),
    ("delapouite/sound-off.svg", "静音 Sound off", ["mute", "silent", "静音", "关闭声音"], "Mute game sound"),
    ("lorc/run.svg", "奔跑 Run", ["run", "running", "sprint", "奔跑", "冲刺"], "Running movement or sprint action"),
    ("delapouite/plain-arrow.svg", "方向箭头 Arrow", ["arrow", "direction", "方向", "箭头"], "Directional navigation arrow"),
    ("delapouite/health-potion.svg", "治疗药水 Health potion", ["potion", "heal", "health", "治疗", "药水"], "Healing potion item"),
]
AUTHORS = {"guard13007": "Guard13007", "delapouite": "Delapouite", "lorc": "Lorc"}


def fetch(relative):
    request = Request(BASE + relative, headers={"User-Agent": "ZhangjiangHackathon-AssetAgent/0.1"})
    with urlopen(request, timeout=35) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Demo asset exceeds the download limit")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="catalogs/demo")
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new empty destination; existing catalogs are preserved")
    # Do not publish a partial catalog if a source file is missing.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {source: executor.submit(fetch, source) for source, *_ in SPECS}
        license_future = executor.submit(fetch, "license.txt")
        payloads = {source: future.result() for source, future in futures.items()}
        license_text = license_future.result()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from asset_agent.ingest import _svg_details
    assets = []
    for source, name, tags, description in SPECS:
        data = payloads[source]
        dimensions = _svg_details(data)
        author = AUTHORS[source.split("/")[0]]
        assets.append({
            "id": "game-icons/" + source[:-4], "type": "icon", "name": name,
            "description": description, "tags": tags, "style": "silhouette", "pack": "game-icons-silhouette",
            "path": "assets/" + source, "format": "svg", **dimensions,
            "license": "CC-BY-3.0", "author": author,
            "source_url": "https://github.com/game-icons/icons/blob/" + COMMIT + "/" + source,
            "license_url": LICENSE_URL,
            "attribution": "Icons made by " + author + ". Source: game-icons.net. Unmodified SVG.",
            "sha256": hashlib.sha256(data).hexdigest(), "source_revision": COMMIT,
        })
    for source, data in payloads.items():
        target = output / "assets" / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / "UPSTREAM-LICENSE.txt").write_bytes(license_text)
    (output / "catalog.json").write_text(json.dumps({"schema_version": "1.0", "source_revision": COMMIT, "assets": assets}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"assets": len(assets), "source_revision": COMMIT, "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
