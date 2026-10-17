"""Lossless long-spec intake and source-linked implementation requirements.

Specifications are immutable task data, never instructions to the host agent. Nothing
in this module truncates a specification or infers that a feature is implemented.
Line numbers are one based; character spans are half-open Python string offsets.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


CASES = {
    "GAME-TEST-001": ("case1:Number_Gate_Shooter", "001数字门枪手.txt"),
    "GAME-TEST-002": ("case2:Wuxia_Survivors", "002武林幸存者.txt"),
    "GAME-TEST-003": ("case3:Power_Hunter", "003百炼英雄.txt"),
}
SECTION = re.compile(r"^(\d{2})\. ([A-Z][A-Z /()\-]+)$", re.M)
SUBSECTION = re.compile(r"^(\d{2}\.\d+) ([^\n]+)$", re.M)
TEST = re.compile(r"^(T\d{2,}):\s*\n", re.M)
NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:\.\d+)?(?:%|\s*(?:seconds?|minutes?|HP|XP|pixels?|gold|world units))?")


def sha256(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


@dataclass(frozen=True)
class SourceSpan:
    path: str
    start: int
    end: int
    line_start: int
    line_end: int
    sha256: str


@dataclass(frozen=True)
class SpecDocument:
    case_id: str
    text: str
    sha256: str
    source_path: str
    names: list[str]
    engine: str
    stack: list[str]
    sections: list[dict[str, Any]]
    chunks: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    acceptance_tests: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    reference_images: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def reconstruct(self) -> str:
        return "".join(chunk["text"] for chunk in self.chunks)


def _span(text: str, path: str, start: int, end: int) -> dict[str, Any]:
    return asdict(SourceSpan(path, start, end, text.count("\n", 0, start) + 1,
                            text.count("\n", 0, max(start, end - 1)) + 1,
                            sha256(text[start:end])))


def _chunks(text: str, path: str, limit: int) -> list[dict[str, Any]]:
    """Prefer chapter/paragraph boundaries while retaining every byte of text."""
    if limit < 128:
        raise ValueError("chunk_chars must be at least 128")
    boundaries = sorted(set([0, len(text)] + [m.end() for m in re.finditer(r"\n\s*\n", text)]
                            + [m.start() for m in SECTION.finditer(text)]))
    result, cursor = [], 0
    while cursor < len(text):
        candidates = [position for position in boundaries if cursor < position <= cursor + limit]
        end = max(candidates) if candidates else min(len(text), cursor + limit)
        span = _span(text, path, cursor, end)
        result.append({"id": "chunk-" + str(len(result) + 1).zfill(4),
                       "text": text[cursor:end], "source": span})
        cursor = end
    return result


def _strategy(text: str, section: str = "") -> list[str]:
    value = text.lower()
    if section.startswith("12") or any(word in value for word in ("readable", "visual theme", "smooth on mobile", "warfare theme", "coherent")):
        return ["browser", "manual"]
    if any(word in value for word in ("build", "typescript", "react", "vite", "modules", "source code", "licensed", "attribution")):
        return ["build"]
    if any(word in value for word in ("babylon", "phaser", "webgl", "scene resources")):
        return ["engine", "browser"]
    if any(word in value for word in ("portrait", "landscape", "orientation", "click", "tapping", "touch", "keyboard", "joystick", "menu", "screen", "chinese", "refresh", "offline", "api", "audio")):
        return ["browser"]
    return ["runtime", "gameplay"]


def _section_at(sections: list[dict[str, Any]], offset: int) -> str:
    applicable = [s for s in sections if s["source"]["start"] <= offset < s["source"]["end"]]
    return max(applicable, key=lambda s: s["source"]["start"])["number"] if applicable else "00"


def ingest_text(text: str, *, case_id: str | None = None, source_path: str = "input.txt",
                archive_dir: str | Path | None = None, chunk_chars: int = 6000,
                reference_images: list[dict[str, Any]] | None = None) -> SpecDocument:
    """Parse the complete supplied text, optionally archive source and trace data.

    No model is called, and no shortening or requirement rewriting occurs. The
    immutable source hash disambiguates changes while content-based requirement
    IDs survive insertion of unrelated paragraphs. A duplicate paragraph has an
    explicit occurrence suffix rather than overwriting an earlier requirement.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Specification text must not be empty")
    declared = re.search(r"^Test Case ID:\s*(\S+)", text, re.M)
    declared_id = declared.group(1) if declared else None
    if case_id and declared_id and case_id != declared_id:
        raise ValueError("Case ID does not match the authoritative specification")
    case_id = case_id or declared_id or "CUSTOM-" + sha256(text)[:12]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", case_id):
        raise ValueError("Invalid specification ID")
    headers = list(SECTION.finditer(text))
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        sections.append({"id": case_id + "/S" + match.group(1), "number": match.group(1),
                         "title": match.group(2), "text": text[match.start():end],
                         "source": _span(text, source_path, match.start(), end), "level": 1})
        for sub in SUBSECTION.finditer(text, match.end(), end):
            next_sub = SUBSECTION.search(text, sub.end(), end)
            sub_end = next_sub.start() if next_sub else end
            sections.append({"id": case_id + "/S" + sub.group(1), "number": sub.group(1),
                             "title": sub.group(2), "text": text[sub.start():sub_end],
                             "source": _span(text, source_path, sub.start(), sub_end), "level": 2})
    sections.sort(key=lambda item: item["source"]["start"])
    requirements, constraints, occurrences = [], [], {}
    for paragraph in re.finditer(r"\S(?:[^\n]|\n(?!\s*\n))*", text):
        raw = paragraph.group(0)
        if re.fullmatch(r"[=\-\s]+", raw) or SECTION.fullmatch(raw) or SUBSECTION.fullmatch(raw):
            continue
        number = _section_at(sections, paragraph.start())
        digest = sha256(raw)
        base = f"{case_id}/R/{number}/{digest[:12]}"
        occurrences[base] = occurrences.get(base, 0) + 1
        req_id = base + ("-" + str(occurrences[base]) if occurrences[base] > 1 else "")
        requirement = {"id": req_id, "section": number, "text": raw,
                       "source": _span(text, source_path, paragraph.start(), paragraph.end()),
                       "verification": _strategy(raw, number), "status": "not_run"}
        requirements.append(requirement)
        values = NUMBER.findall(raw)
        if values or any(mark in raw for mark in (" = ", " > ", " <= ", "×", "÷")):
            constraints.append({"id": req_id + "/constraint", "requirement_id": req_id,
                                "text": raw, "values": values, "source": requirement["source"],
                                "interpretation": "authoritative_text_including_examples"})
    acceptance_tests = []
    acceptance_section = next((s for s in sections if s["number"] == "14"), None)
    if acceptance_section:
        start, end = acceptance_section["source"]["start"], acceptance_section["source"]["end"]
        tests = list(TEST.finditer(text, start, end))
        for index, test in enumerate(tests):
            test_end = tests[index + 1].start() if index + 1 < len(tests) else end
            body = text[test.end():test_end]
            boundary = re.search(r"(?m)^(?:[A-Z][A-Z /-]+ TESTS|All applicable tests|Additionally:|={5,})", body)
            if boundary:
                test_end = test.end() + boundary.start()
            while test_end > test.end() and text[test_end - 1].isspace():
                test_end -= 1
            test_text = text[test.end():test_end]
            acceptance_tests.append({"id": case_id + "/" + test.group(1), "test_id": test.group(1),
                                     "text": test_text, "source": _span(text, source_path, test.start(), test_end),
                                     "verification": _strategy(test_text), "status": "not_run"})
    technology = re.search(r"Required Technology:\s*\n(.*?)(?:\n\s*\n)", text, re.S)
    stack = [line.strip().lstrip("- ") for line in technology.group(1).splitlines() if line.strip()] if technology else []
    engine = "babylon" if "Babylon.js" in stack else "phaser" if "Phaser" in stack else "unspecified"
    names = list(dict.fromkeys(re.findall(r"^Game Name:\s*\n?([^\n]+)", text, re.M)))
    entities = []
    # Entity boundaries retain every per-level effect and formula, not just names.
    entity_pattern = re.compile(r"(?m)^(?:Skill )?([WPE]\d{2}):\s*\n([^\n]+)")
    matches = list(entity_pattern.finditer(text))
    for match in matches:
        containing = next((s for s in sections if s["level"] == 2 and s["source"]["start"] <= match.start() < s["source"]["end"]), None)
        section_end = containing["source"]["end"] if containing else len(text)
        following = entity_pattern.search(text, match.end(), section_end)
        end = following.start() if following else section_end
        entities.append({"id": match.group(1), "name": match.group(2).strip(), "text": text[match.start():end],
                         "kind": {"W": "active", "P": "passive", "E": "ultimate"}[match.group(1)[0]],
                         "source": _span(text, source_path, match.start(), end)})
    warnings = ["Specification contains multiple names; preserve the header and in-body names: " + " / ".join(names)] if len(names) > 1 else []
    doc = SpecDocument(case_id, text, sha256(text), source_path, names, engine, stack, sections,
                       _chunks(text, source_path, chunk_chars), requirements, acceptance_tests,
                       constraints, entities, reference_images or [], warnings)
    if doc.reconstruct() != text:
        raise AssertionError("Lossless chunk reconstruction failed")
    if archive_dir is not None:
        target = Path(archive_dir) / case_id / doc.sha256
        target.mkdir(parents=True, exist_ok=True)
        source = target / "source.txt"
        if source.exists() and source.read_bytes() != text.encode("utf-8"):
            raise ValueError("Existing immutable source archive has different content")
        source.write_bytes(text.encode("utf-8"))
        (target / "specification.json").write_text(json.dumps(doc.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (target / "feature-plan.json").write_text(json.dumps(build_feature_plan(doc), ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def load_case(case_id: str, root: str | Path | None = None, *, archive_dir: str | Path | None = None) -> SpecDocument:
    if case_id not in CASES:
        raise ValueError("Unknown official test case: " + case_id)
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    directory, name = CASES[case_id]
    path = root / directory / name
    images = [{"path": str(image.relative_to(root)), "sha256": sha256(image.read_bytes())}
              for image in sorted((root / directory).glob("*.png"))]
    # newline='' is essential: CRLF is source data, not a normalization request.
    with path.open(encoding="utf-8", newline="") as handle:
        text = handle.read()
    return ingest_text(text, case_id=case_id, source_path=str(path.relative_to(root)),
                       archive_dir=archive_dir, reference_images=images)


def build_feature_plan(spec: SpecDocument | dict[str, Any]) -> dict[str, Any]:
    """Create a coverage-complete DAG; the coding planner may refine each node."""
    data = spec.to_dict() if isinstance(spec, SpecDocument) else spec
    case_id = data["case_id"]
    foundation = case_id + "/feature/foundation"
    features = [{"id": foundation, "title": "Engine, project build and test observability",
                 "depends_on": [], "requirement_ids": [r["id"] for r in data["requirements"] if r["section"].split(".")[0] in {"00", "01", "02", "03", "13"}],
                 "verification": ["build", "engine", "browser"], "status": "not_run"}]
    section_dependencies = {"04": [], "05": ["04"], "06": ["04", "05"], "07": ["06"],
                            "08": ["06", "07"], "09": ["06", "07", "08"], "10": ["09"],
                            "11": ["04", "06", "10"], "12": ["05", "11"], "14": ["09", "11", "12"], "15": ["14"]}
    for section in data["sections"]:
        if section["level"] != 1 or section["number"] in {"01", "02", "03", "13"}:
            continue
        number = section["number"]
        requirements = [r for r in data["requirements"] if r["section"].split(".")[0] == number]
        features.append({"id": f"{case_id}/feature/S{number}", "title": section["title"],
                         "depends_on": [foundation] + [f"{case_id}/feature/S{n}" for n in section_dependencies.get(number, [])],
                         "requirement_ids": [r["id"] for r in requirements],
                         "acceptance_ids": [t["id"] for t in data["acceptance_tests"]] if number == "14" else [],
                         "source": section["source"], "verification": sorted({v for r in requirements for v in r["verification"]}),
                         "status": "not_run"})
    # Custom text without chapter headings still has complete requirements.
    covered = {r for feature in features for r in feature["requirement_ids"]}
    features[0]["requirement_ids"].extend(r["id"] for r in data["requirements"] if r["id"] not in covered)
    return {"version": 1, "case_id": case_id, "spec_sha256": data["sha256"],
            "engine_lock": data["engine"], "required_stack": data["stack"], "features": features,
            "requirement_count": len(data["requirements"]), "acceptance_count": len(data["acceptance_tests"]),
            "all_requirements_covered": len({r for f in features for r in f["requirement_ids"]}) == len(data["requirements"]),
            "runtime_protocol": runtime_protocol(data["case_id"])}


def runtime_protocol(case_id: str) -> dict[str, Any]:
    """Contract for observing the production simulation, not mutating test answers."""
    return {"global": "window.__HARNESS__", "version": 1, "case_id": case_id,
            "methods": {"snapshot()": "Read real simulation state, entity positions, stats, skills, pools and update-loop count",
                        "config()": "Read the actual level/enemy/skill configuration used by the simulation",
                        "command(name,args={})": "Execute the same validated commands as UI controls; dispatch({type:name,...args}) is an equivalent alias",
                        "isolatedSimulation(params)": "Create an independent instance of the production Simulation class with a declared initial fixture; never alter live gameplay or accept expected outcomes",
                        "advance(milliseconds)": "Advance the production fixed-step simulation; test mode disables the RAF driver",
                        "events(afterSequence=0)": "Read real ordered simulation events {seq, simTime, type, data}"},
            "commands": ["start", "selectLevel", "pause", "resume", "restart", "move", "selectTarget", "chooseUpgrade", "purchase", "setAudio"],
            "forbidden_test_shortcuts": ["setPower", "setHP", "win", "kill", "spawn", "skipLevel", "grantSkill"],
            "rules": ["Seed controls only reproducible randomness, never balance or victory conditions",
                      "Snapshot values come from production systems, never constants prepared for a test",
                      "Advance and rendered gameplay run identical update, collision and damage systems",
                      "Input, pre/post snapshots, events, build hash and screenshots form an evidence trace",
                      "At least one normal-input winning trace is required for every level",
                      "A config-only assertion cannot establish gameplay functionality"]}
