"""Run real, pinned-reference discovery through the formal Webmcp v0.2 port."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="examples/research-cases.json")
    parser.add_argument("--catalog", default="catalogs/references")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from asset_agent import MaterialPortal
    from asset_agent.research_contracts import validate_search_request, validate_search_result
    from tools.bootstrap_reference_catalog import download_sources

    def local(value):
        path = Path(value)
        return path if path.is_absolute() else root / path
    cases = json.loads(local(args.cases).read_text(encoding="utf-8-sig"))
    catalog = local(args.catalog)
    sources = download_sources(catalog, offline=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output = local(args.output) if args.output else root / "output" / ("research-live-" + stamp)
    if output.exists():
        raise ValueError("Use a new evaluation output directory")
    (output / "requests").mkdir(parents=True)
    (output / "responses").mkdir()
    rows = []
    with MaterialPortal(reference_catalog_dir=catalog, research_cache_path=output / "requests.sqlite3") as portal:
        for case in cases["cases"]:
            request = {
                "contractVersion": "0.2", "requestId": "live-" + stamp + "-" + case["id"],
                "discussion": {"runId": "live-" + stamp, "threadId": case["id"], "round": 0,
                               "inReplyToRequestId": None, "followUp": None},
                "projectRevision": 0, "purpose": case["purpose"], "description": case["description"],
                "images": [], "constraints": {"engine": case["engine"], "engineVersion": None,
                                               "platform": "web", "language": "any", "inputModes": ["pointer", "touch"]},
                "materialKinds": case["materialKinds"], "limit": case["limit"],
            }
            if case["purpose"] == "mechanic":
                request["mechanicId"] = case["id"]
                request["requiredBehavior"] = case["requiredBehavior"]
            validate_search_request(request)
            result = portal.search_sync(request)
            validate_search_result(result, request)
            if result["isMock"]:
                raise ValueError("A real-catalog evaluation cannot use mock results")
            candidates = result.get("candidates", [])
            pinned = sum(bool(candidate["source"]["revision"] and re.fullmatch(r"[a-f0-9]{40}", candidate["source"]["revision"])) for candidate in candidates)
            row = {"id": case["id"], "source": case["source"], "purpose": case["purpose"],
                   "status": result["status"], "candidate_count": len(candidates), "requested_limit": case["limit"],
                   "candidate_ids": [candidate["candidateId"] for candidate in candidates],
                   "pinned_revision_count": pinned, "behavior_verified": False}
            rows.append(row)
            for folder, value in (("requests", request), ("responses", result)):
                (output / folder / (case["id"] + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "contractVersion": "0.2", "contractRevision": cases["contractRevision"],
        "testSpecRevision": cases["testSpecRevision"], "isMock": False,
        "verified_cached_source_files": len(sources["files"]), "queries": rows,
        "scope": [
            "The catalog contains real, pinned upstream references. Retrieval was local and did not execute those projects.",
            "Candidate discovery is not proof that required behavior, licensing for reuse, engine versions or runtime compatibility passed review.",
            "No image search, online live-search provider, LLM reranking, full MAS orchestration or game build was performed.",
            "A completed zero-candidate search is a visible source gap; it is not a finished implementation or an error fallback.",
        ],
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output / "report.json"), "verified_source_files": len(sources["files"]), "queries": rows}, ensure_ascii=True, indent=2))
    return 1 if any(row["status"] == "failed" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
