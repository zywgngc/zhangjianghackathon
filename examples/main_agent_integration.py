"""Run from the repository root: python -m examples.main_agent_integration.

The callback is deliberately not implemented with a particular vendor SDK.
Pass your existing main-agent LLM function as ``selector`` below; never call
an LLM or paid embedding service implicitly just by importing this module.
"""

import json
from pathlib import Path

from asset_agent import AssetAgent


def select_with_main_agent(context):
    """Integration contract, to be wired to the caller's existing LLM.

    Send context (layout, game prompt, per-slot grounded candidate records)
    to a model using its JSON/structured-output mode. Parse its response and
    return {"assignments": [{"slot_id": ..., "asset_id": ..., "reason": ...}]}.
    The AssetAgent revalidates all IDs and budgets after the callback.
    """
    raise NotImplementedError("Connect this callback to your main agent's existing model client")


def main():
    root = Path(__file__).resolve().parents[1]
    request = json.loads((root / "examples/racing-ui.json").read_text(encoding="utf-8"))
    # Offline runnable baseline. For LLM-augmented selection add:
    # selector=select_with_main_agent
    with AssetAgent(root / "catalogs/demo") as agent:
        handoff = agent.run(request, root / "output/main-agent-demo")
    print(json.dumps({"status": handoff["status"], "bundle": handoff["bundle"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
