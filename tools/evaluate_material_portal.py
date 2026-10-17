"""Evaluate representative art requests extracted from the remote tests specs.

This is a catalog-coverage probe, not a benchmark of full-game recreation or
an automated visual/style judgment. The original specs remain in an isolated,
hash-verified branch snapshot rather than replacing the development checkout.
"""

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys


def evaluate(catalog_dir, cases_path, snapshot):
    from asset_agent import MaterialPortal

    cases = json.loads(cases_path.read_text(encoding="utf-8-sig"))
    provenance = json.loads((snapshot / "_snapshot_manifest.json").read_text(encoding="utf-8"))
    if provenance["commit"] != cases["source_commit"] or provenance["branch"] != cases["source_branch"]:
        raise ValueError("Case definitions and local snapshot must identify the same branch version")
    for entry in provenance["files"]:
        local = (snapshot / entry["local_path"]).resolve()
        local.relative_to(snapshot.resolve())
        if hashlib.sha256(local.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Snapshot file changed: " + entry["local_path"])

    portal = MaterialPortal(catalog_dir)
    rows, summaries = [], []
    for game in cases["games"]:
        counts = Counter()
        for requirement in game["requirements"]:
            request = {
                "purpose": requirement["purpose"],
                "description": requirement["description"],
                "imageRefs": [str((snapshot / game["image"]).resolve())],
                "constraints": {"engine": game["engine"], "platform": "web", "language": "TypeScript"},
                "limit": 5,
            }
            response = portal.search_sync(request)
            candidates = response["candidates"]
            expected_type = requirement["expected_type"]
            wrong_types = [item["id"] for item in candidates if item["assetType"] != expected_type]
            references = requirement.get("acceptable_ids", [])
            matched = [item["id"] for item in candidates if item["id"] in references]
            reference_rank = next((index + 1 for index, item in enumerate(candidates) if item["id"] in references), None)
            outcome = "wrong_type" if wrong_types else "missing" if not candidates else "reference_found" if matched else "needs_manual_review"
            group = requirement["group"]
            counts[group + "_requests"] += 1
            counts[group + "_with_candidates"] += bool(candidates)
            counts["wrong_type_requests"] += bool(wrong_types)
            counts["ui_reference_found"] += bool(matched)
            counts["ui_top1_reference_found"] += reference_rank == 1
            rows.append({
                "case_id": game["id"], "game": game["name"], "requirement_id": requirement["id"],
                "group": group, "source_spec": game["spec"],
                "source_lines": requirement["source_lines"],
                "notes": requirement.get("notes", []),
                "request": request, "expected_type": expected_type,
                "acceptable_ids": references, "outcome": outcome,
                "reference_rank": reference_rank,
                "matched_reference_ids": matched, "wrong_type_ids": wrong_types,
                "response": response,
            })
        summaries.append({"case_id": game["id"], "game": game["name"], **dict(counts)})
    return {
        "source_branch": cases["source_branch"], "source_commit": cases["source_commit"],
        "source_transport": provenance["transport"], "snapshot_files_verified": len(provenance["files"]),
        "catalog": str(Path(catalog_dir).resolve()), "catalog_records": len(portal.catalog.assets),
        "request_count": len(rows), "summaries": summaries,
        "unique_ui_reference_ids": sorted({asset_id for row in rows for asset_id in row["matched_reference_ids"]}),
        "scope": [
            "Requests are a manually selected sample from three game specs, not an exhaustive asset inventory.",
            "UI reference IDs assess functional icon relevance; matching style, color, animation, and actual engine behavior have not been approved.",
            "archetype and mechanic label art-retrieval stages; neither requests executable game templates.",
            "imageRefs are passed through the interface but not visually analyzed by the current portal.",
            "No online provider, real text-embedding model, selection LLM, or generation service was used.",
        ],
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="catalogs/demo")
    parser.add_argument("--cases", default="examples/material-portal-cases.json")
    parser.add_argument("--snapshot", required=True,
                        help="Explicit path to the historical hash-verified tests-branch snapshot; not required by the main agent")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    def local(value):
        value = Path(value)
        return value if value.is_absolute() else root / value
    report = evaluate(local(args.catalog), local(args.cases), local(args.snapshot))
    output = local(args.output) if args.output else root / "output" / ("material-portal-evaluation-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"report": str(output), "request_count": report["request_count"], "summaries": report["summaries"], "unique_ui_reference_count": len(report["unique_ui_reference_ids"])}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
