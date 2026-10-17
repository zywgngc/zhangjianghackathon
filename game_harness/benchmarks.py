"""Inventory full team acceptance specifications without silently reducing their scope."""
import hashlib
import re
import struct
from pathlib import Path


CASES = {
    "GAME-TEST-001": ("case1_Number_Gate_Shooter", "001数字门枪手.txt"),
    "GAME-TEST-002": ("case2_Wuxia_Survivors", "002武林幸存者.txt"),
    "GAME-TEST-003": ("case3_Power_Hunter", "003百炼英雄.txt"),
}


def inventory(root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    results = []
    for case_id, (directory, filename) in CASES.items():
        path = root / directory / filename
        text = path.read_text(encoding="utf-8")
        if "Test Case ID: " + case_id not in text:
            raise ValueError("测试用例 ID 与文件不匹配：" + case_id)
        technology = re.search(r"Required Technology:\s*\n(.*?)(?:\n\s*\n)", text, re.S)
        stack = [s.strip().lstrip("- ") for s in technology.group(1).splitlines() if s.strip()] if technology else []
        locked_engine = "babylon" if "Babylon.js" in stack else "phaser" if "Phaser" in stack else None
        images, warnings, hashes = [], [], set()
        for image in sorted((root / directory).glob("*.png")):
            data = image.read_bytes()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("无效 PNG：" + image.name)
            digest = hashlib.sha256(data).hexdigest()
            width, height = struct.unpack(">II", data[16:24])
            images.append({"path": str(image.relative_to(root)), "sha256": digest, "width": width, "height": height})
            if digest in hashes:
                warnings.append("存在内容相同的参考图：" + image.name)
            hashes.add(digest)
        names = list(dict.fromkeys(re.findall(r"^Game Name:\s*\n?([^\n]+)", text, re.M)))
        if len(names) > 1:
            warnings.append("规格内部存在多个游戏名，保留原文待确认：" + " / ".join(names))
        blockers = []
        if locked_engine not in {"canvas", "dom"}:
            blockers.append("engine_adapter_not_installed:" + str(locked_engine))
        if len(text) > 8000:
            blockers.append("long_spec_input_not_supported")
        if "TypeScript" in stack or "Vite" in stack:
            blockers.append("multi_file_typescript_build_not_supported")
        results.append({"case_id": case_id, "names": names, "specification": str(path.relative_to(root)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "characters": len(text),
            "required_stack": stack, "locked_engine": locked_engine,
            "sections": re.findall(r"^\d{2}\. [A-Z][A-Z /()\-]+$", text, re.M),
            "has_acceptance_tests": "ACCEPTANCE TESTS" in text, "reference_images": images,
            "runnable": not blockers, "blockers": blockers, "warnings": warnings,
            "status": "blocked" if blockers else "ready", "execution": "not_run"})
    return results
