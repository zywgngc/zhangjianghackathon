import argparse
import json
from pathlib import Path

from game_harness.benchmarks import inventory


def main():
    parser = argparse.ArgumentParser(description="检查团队完整验收用例的资料与 harness 能力差距；不消耗模型额度")
    parser.add_argument("--require-runnable", action="store_true", help="任一用例受阻时以退出码 2 结束")
    parser.add_argument("--output", type=Path, default=Path("data/benchmarks/preflight.json"))
    args = parser.parse_args()
    cases = inventory()
    report = {"scope": "资料完整性及能力预检；不代表游戏实现或验收已通过", "cases": cases}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_runnable and not all(c["runnable"] for c in cases):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
