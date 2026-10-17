"""Small local CLI around the same Python interface used by the main agent."""

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Game asset retrieval sub-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = sub.add_parser(name)
        command.add_argument("request", help="Upstream layout + prompt JSON file")
        command.add_argument("--catalog", default="catalogs/demo")
        if name == "run":
            command.add_argument("--output", required=True)
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--catalog", default="catalogs/demo")
    search.add_argument("--type", choices=["icon", "sprite", "tileset", "model", "audio", "font"])
    search.add_argument("--top-k", type=int, default=5)
    research = sub.add_parser("research", help="Webmcp v0.2 material repository/code/document search")
    research.add_argument("request", help="Request JSON file, or - for standard input")
    research.add_argument("--catalog", default="catalogs/references")
    research.add_argument("--cache", default=".asset-agent/research-requests.sqlite3")
    research.add_argument("--timeout", type=float, default=10)
    research.add_argument("--allow-mock", action="store_true", help="Explicit fixture-only development mode")
    research.add_argument("--require-parent", action="store_true")
    check = sub.add_parser("validate")
    check.add_argument("--catalog", default="catalogs/demo")
    ingest = sub.add_parser("import-pack")
    ingest.add_argument("source")
    ingest.add_argument("--catalog", required=True)
    for arg in ("pack", "style", "license-id", "author", "source-url", "license-url"):
        ingest.add_argument("--" + arg, required=True)
    ingest.add_argument("--tag", action="append", default=[])
    ingest.add_argument("--type", choices=["icon", "sprite", "tileset", "model", "audio", "font"])
    args = parser.parse_args(argv)
    try:
        if args.command == "research":
            from .research_portal import ResearchMaterialPortal
            if args.request == "-":
                request = json.load(sys.stdin)
            else:
                with Path(args.request).open(encoding="utf-8-sig") as handle:
                    request = json.load(handle)
            with ResearchMaterialPortal(args.catalog, cache_path=args.cache, timeout_seconds=args.timeout,
                                        allow_mock=args.allow_mock, require_parent=args.require_parent) as portal:
                result = portal.search_sync(request)
        elif args.command == "import-pack":
            from .ingest import import_pack
            result = import_pack(args.source, args.catalog, pack=args.pack, style=args.style, license_id=args.license_id, author=args.author, source_url=args.source_url, license_url=args.license_url, tags=args.tag, asset_type=args.type)
        elif args.command == "validate":
            from .catalog import Catalog
            catalog = Catalog(args.catalog)
            issues = catalog.validate_files()
            result = {"asset_count": len(catalog.assets), "issues": issues, "valid": not issues}
        else:
            from .agent import AssetAgent
            with AssetAgent(args.catalog) as agent:
                if args.command == "search":
                    result = {"hits": agent.search(args.query, filters={"type": args.type} if args.type else None, top_k=args.top_k), "retrieval": agent.search_engine.report}
                else:
                    with Path(args.request).open(encoding="utf-8-sig") as handle:
                        request = json.load(handle)
                    result = agent.run(request, args.output) if args.command == "run" else agent.plan(request)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if args.command == "validate" and result["issues"] else 0
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
