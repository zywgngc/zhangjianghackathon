"""Controlled React/TypeScript build workspaces for long-running game projects.

Generated source is untrusted data. Only the bundled, pinned toolchain is executed;
model-written package scripts, Vite plugins, and configuration are never loaded.
This is filesystem confinement plus a credential-free build process, not a VM.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
from html.parser import HTMLParser
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import time
import uuid
from typing import Any
from urllib.parse import unquote

from .locking import exclusive_lock, LockUnavailable


TEMPLATES = Path(__file__).with_name("project_templates")
ENGINES = frozenset({"phaser", "babylon"})
MAX_FILE_BYTES = 512_000
MAX_SOURCE_BYTES = 12_000_000
MAX_FILES = 500
EDITABLE_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".css", ".json", ".html", ".svg", ".md", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".ogg", ".mp3", ".wav"})
PROTECTED = ("package.json", "package-lock.json", "tsconfig.json", "vite.config.mjs")


class WorkspaceError(ValueError):
    """An operation violates a project boundary or stale-file precondition."""


class _ResourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"src", "href", "poster"} and value:
                self.references.append(value)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ProjectWorkspace:
    def __init__(self, root: Path, engine: str, *, cache_dir: Path | None = None):
        if engine not in ENGINES:
            raise WorkspaceError(f"Unsupported project engine: {engine}")
        root = Path(root).absolute()
        # A run workspace may sit below a trusted NFS mount symlink, but the
        # workspace itself must never redirect to another run's directory.
        if root.is_symlink():
            raise WorkspaceError("Workspace root must not be a symlink")
        self.root = root.resolve()
        self.engine = engine
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else self.root.parent / ".npm-cache"
        self.template_dir = TEMPLATES / engine

    def _inside(self, relative: str, *, base: Path | None = None) -> Path:
        if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
            raise WorkspaceError("A nonempty POSIX relative path is required")
        parts = PurePosixPath(relative).parts
        if PurePosixPath(relative).is_absolute() or any(p in {"..", "."} or p.startswith(".") for p in parts):
            raise WorkspaceError("Absolute, parent, and hidden paths are forbidden")
        base = base or self.root
        target = base.joinpath(*parts)
        current = base
        if base.is_symlink():
            raise WorkspaceError("Symlink directories are forbidden")
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise WorkspaceError(f"Symlink paths are forbidden: {relative}")
        if not target.resolve().is_relative_to(base.resolve()):
            raise WorkspaceError("Path escapes the workspace")
        return target

    def resolve(self, relative: str, *, writable: bool = False) -> Path:
        target = self._inside(relative)
        parts = PurePosixPath(relative).parts
        allowed = relative == "index.html" or (parts[0] in {"src", "public"} and len(parts) > 1)
        if not allowed:
            if not writable and relative in PROTECTED:
                return target
            raise WorkspaceError("Only src/**, public/**, and index.html are model editable")
        if target.suffix.lower() not in EDITABLE_SUFFIXES:
            raise WorkspaceError(f"Unsupported source or asset extension: {target.suffix}")
        return target

    def _internal(self, name: str) -> Path:
        folder = self.root / ".harness"
        if folder.is_symlink():
            raise WorkspaceError("Internal metadata must not be a symlink")
        path = folder / name
        if path.is_symlink():
            raise WorkspaceError("Internal metadata must not be a symlink")
        folder.mkdir(parents=True, exist_ok=True)
        return path

    def initialize(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        state = self._internal("workspace.json")
        if state.exists() and json.loads(state.read_text())["engine"] != self.engine:
            raise WorkspaceError("An initialized workspace cannot change engines")
        for source in sorted((TEMPLATES / "common").rglob("*")) + sorted(self.template_dir.rglob("*")):
            if not source.is_file():
                continue
            origin = self.template_dir if source.is_relative_to(self.template_dir) else TEMPLATES / "common"
            relative = source.relative_to(origin).as_posix()
            destination = self._inside(relative)
            if relative in PROTECTED and destination.exists() and destination.read_bytes() != source.read_bytes():
                raise WorkspaceError(f"Trusted configuration was modified: {relative}")
            if not destination.exists():
                _atomic(destination, source.read_bytes())
        _atomic(state, json.dumps({"engine": self.engine, "template_version": 1}).encode())
        return self.snapshot()

    def list_files(self) -> list[str]:
        paths = ["index.html", *PROTECTED]
        for prefix in ("src", "public"):
            folder = self.root / prefix
            if folder.is_symlink():
                raise WorkspaceError("Source directories must not be symlinks")
            if folder.exists():
                for path in folder.rglob("*"):
                    relative = path.relative_to(self.root).as_posix()
                    self._inside(relative)
                    if path.is_file():
                        paths.append(relative)
        return sorted(p for p in paths if (self.root / p).is_file())

    def read_file(self, relative: str) -> str:
        path = self.resolve(relative)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceError("File exceeds the read limit")
        return path.read_text(encoding="utf-8")

    def write_file(self, relative: str, content: str | bytes) -> None:
        path = self.resolve(relative, writable=True)
        raw = content.encode("utf-8") if isinstance(content, str) else content
        if not isinstance(raw, bytes) or len(raw) > MAX_FILE_BYTES:
            raise WorkspaceError("File exceeds the write limit")
        if not isinstance(content, str) and PurePosixPath(relative).parts[0] != "public":
            raise WorkspaceError("Binary assets belong under public/")
        files = self.list_files()
        previous = path.stat().st_size if path.exists() else 0
        if len(files) + (relative not in files) > MAX_FILES or sum((self.root / p).stat().st_size for p in files) - previous + len(raw) > MAX_SOURCE_BYTES:
            raise WorkspaceError("Project file or byte quota exceeded")
        _atomic(path, raw)

    def apply_patch(self, relative: str, old: str, new: str, expected_sha256: str | None = None) -> None:
        self.resolve(relative, writable=True)
        content = self.read_file(relative)
        if expected_sha256 is not None and _digest(content.encode()) != expected_sha256:
            raise WorkspaceError("Patch precondition failed: source hash changed")
        if not old or content.count(old) != 1:
            raise WorkspaceError("Patch must match exactly one nonempty source span")
        self.write_file(relative, content.replace(old, new, 1))

    def delete_file(self, relative: str, expected_sha256: str | None = None) -> None:
        path = self.resolve(relative, writable=True)
        if expected_sha256 and _digest(path.read_bytes()) != expected_sha256:
            raise WorkspaceError("Delete precondition failed: source hash changed")
        path.unlink()

    def snapshot(self) -> dict[str, Any]:
        files = []
        for relative in self.list_files():
            raw = self._inside(relative).read_bytes()
            files.append({"path": relative, "sha256": _digest(raw), "bytes": len(raw)})
        serialized = json.dumps({"engine": self.engine, "files": files}, sort_keys=True, separators=(",", ":")).encode()
        return {"engine": self.engine, "files": files, "sha256": _digest(serialized)}

    def _validate_source(self) -> None:
        # Prevent generated source from asking Vite to bundle host files. Browser
        # URLs are relative and local; the preview additionally denies network.
        dependencies = set(json.loads((self.template_dir / "package.json").read_text())["dependencies"])
        pattern = re.compile(r"(?:\b(?:from|import)\s*(?:\(\s*)?|\brequire\s*\(\s*)['\"]([^'\"]+)['\"]|(?:@import\s+|url\(\s*)['\"]?([^'\"\s;)]+)|\b(?:src|href|poster)\s*=\s*['\"]([^'\"]+)['\"]")
        for relative in self.list_files():
            if relative != "index.html" and (not relative.startswith(("src/", "public/")) or Path(relative).suffix not in {".ts", ".tsx", ".js", ".jsx", ".css", ".html"}):
                continue
            source = self._inside(relative)
            text = source.read_text()
            references = [(next(s for s in match.groups() if s is not None), match.group(1) is None) for match in pattern.finditer(text)]
            if source.suffix == ".html":
                parser = _ResourceParser()
                parser.feed(text)
                references.extend((reference, True) for reference in parser.references)
            for specifier, local_asset in references:
                if specifier.startswith("#"):
                    continue
                if specifier.startswith("data:"):
                    if local_asset:
                        continue
                    raise WorkspaceError("Data URL JavaScript imports are forbidden")
                for _ in range(3):
                    decoded = unquote(specifier)
                    if decoded == specifier:
                        break
                    specifier = decoded
                if "\\" in specifier:
                    raise WorkspaceError(f"Backslash import paths are forbidden in {relative}")
                if specifier.startswith(("//", "/@")):
                    raise WorkspaceError(f"Protocol-relative and Vite host paths are forbidden in {relative}")
                if specifier.startswith(".") or (local_asset and not specifier.startswith(("/", "http:", "https:", "file:"))):
                    location = (source.parent / specifier.split("?")[0]).resolve()
                    if not any(location.is_relative_to(self.root / folder) for folder in ("src", "public")):
                        raise WorkspaceError(f"Import escapes workspace in {relative}: {specifier}")
                elif specifier.startswith("/"):
                    # /assets/foo is a runtime public asset, never a host import.
                    asset_relative = specifier.lstrip("/").split("?")[0]
                    local = self._inside(asset_relative)
                    public = self._inside(asset_relative, base=self.root / "public")
                    if not (public.is_file() or (local.is_file() and any(local.resolve().is_relative_to(self.root / folder) for folder in ("src", "public")))):
                        raise WorkspaceError(f"Absolute imports are forbidden in {relative}: {specifier}")
                elif not any(specifier == name or specifier.startswith(name + "/") for name in dependencies):
                    raise WorkspaceError(f"Dependency is not in the pinned template in {relative}: {specifier}")

    def _environment(self) -> dict[str, str]:
        home = self._internal("home")
        home.mkdir(exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Deliberately do not forward OPENAI_*, provider credentials, NODE_OPTIONS,
        # SSH_AUTH_SOCK, npm tokens, proxy credentials, or a user's npmrc.
        environment = {"PATH": os.defpath + ":/usr/local/bin:/opt/homebrew/bin", "HOME": str(home), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "CI": "1", "NO_COLOR": "1", "npm_config_cache": str(self.cache_dir), "npm_config_userconfig": str(self._internal("empty.npmrc")), "npm_config_ignore_scripts": "true", "npm_config_update_notifier": "false"}
        if os.name == "nt":
            node = shutil.which("node")
            if node is None:
                raise WorkspaceError("Node.js is required for project builds")
            system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            temporary = self._internal("tmp")
            temporary.mkdir(exist_ok=True)
            environment.update(PATH=os.pathsep.join([str(Path(node).parent), str(system_root / "System32"), str(system_root)]),
                               SystemRoot=str(system_root), USERPROFILE=str(home), TEMP=str(temporary), TMP=str(temporary))
        return environment

    async def _command(self, name: str, command: list[str], deadline: float, environment: dict[str, str]) -> dict[str, Any]:
        started = time.monotonic()
        execution = command
        if os.name == "nt":
            # Execute only the host's installed Node and npm CLI, never cmd.exe,
            # an npm.cmd shell string, or a model-written package script.
            node = shutil.which("node")
            if node is None:
                raise WorkspaceError("Node.js is required for project builds")
            if command[0] == "npm":
                npm = shutil.which("npm.cmd")
                cli = Path(npm).parent / "node_modules/npm/bin/npm-cli.js" if npm else None
                if cli is None or not cli.is_file():
                    raise WorkspaceError("The installed Node.js npm CLI is required for project builds")
                execution = [node, str(cli), *command[1:]]
            elif command[0] == "node":
                execution = [node, *command[1:]]
            else:
                raise WorkspaceError("Only the pinned Node.js toolchain may execute")
        process = await asyncio.create_subprocess_exec(*execution, cwd=self.root, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=os.name != "nt")
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), max(0.01, deadline - started))
            code, timed_out = process.returncode, False
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            try:
                if os.name == "nt":
                    # Vite may start esbuild; terminate the owned process tree.
                    taskkill = str(Path(environment["SystemRoot"]) / "System32/taskkill.exe")
                    killer = await asyncio.create_subprocess_exec(taskkill, "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await killer.wait()
                    if process.returncode is None:
                        process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = await process.communicate()
            if isinstance(error, asyncio.CancelledError):
                raise
            code, timed_out = -9, True
        output = stdout.decode(errors="replace")[-64_000:]
        errors = stderr.decode(errors="replace")[-64_000:]
        _atomic(self._internal(f"{name}.log"), (output + "\n" + errors).encode())
        return {"name": name, "command": command, "exit_code": code, "passed": code == 0, "timed_out": timed_out, "duration_seconds": round(time.monotonic() - started, 3), "stdout": output, "stderr": errors}

    async def build(self, *, install: bool = True, timeout: float = 180, offline: bool = False) -> dict[str, Any]:
        report: dict[str, Any] = {"passed": False, "engine": self.engine, "stages": [], "errors": [], "stdout": "", "stderr": "", "artifact_manifest": [], "entrypoint": None}
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with exclusive_lock(self._internal("build.lock")):
                try:
                    return await self._build_locked(report, install=install, timeout=timeout, offline=offline)
                except (WorkspaceError, OSError, json.JSONDecodeError) as error:
                    report["errors"].append(str(error))
                    return self._finish(report)
        except LockUnavailable:
            report["errors"].append("Another build owns this workspace")
            return report
        except (WorkspaceError, OSError, json.JSONDecodeError) as error:
            report["errors"].append(str(error))
            return self._finish(report)

    async def _build_locked(self, report: dict[str, Any], *, install: bool, timeout: float, offline: bool) -> dict[str, Any]:
        self.initialize()
        self._validate_source()
        if any((self.root / folder).is_symlink() for folder in ("node_modules", "dist")):
            raise WorkspaceError("Dependencies and build output must not be symlinks")
        before = self.snapshot()
        report["source_sha256"] = before["sha256"]
        environment = self._environment()
        deadline = time.monotonic() + timeout
        dependency_stamp = self._internal("dependencies.json")
        lock_sha = _digest((self.root / "package-lock.json").read_bytes())
        installed = dependency_stamp.exists() and json.loads(dependency_stamp.read_text()).get("lock_sha256") == lock_sha and all((self.root / p).is_file() for p in ("node_modules/typescript/bin/tsc", "node_modules/vite/bin/vite.js"))
        if not installed:
            if not install:
                raise WorkspaceError("Pinned dependencies have not been installed")
            command = ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--prefer-offline"]
            if offline:
                command.append("--offline")
            stage = await self._command("install", command, deadline, environment)
            report["stages"].append(stage)
            if not stage["passed"]:
                report["errors"].append("Pinned npm dependency installation failed")
                return self._finish(report)
            _atomic(dependency_stamp, json.dumps({"lock_sha256": lock_sha}).encode())
        for name, command in [("typecheck", ["node", "node_modules/typescript/bin/tsc", "--project", "tsconfig.json", "--noEmit"]), ("bundle", ["node", "node_modules/vite/bin/vite.js", "build", "--config", "vite.config.mjs", "--base", "./"] )]:
            stage = await self._command(name, command, deadline, environment)
            report["stages"].append(stage)
            if not stage["passed"]:
                report["errors"].append(f"{name} failed; repair the source using the recorded diagnostics")
                return self._finish(report)
        if self.snapshot()["sha256"] != before["sha256"]:
            raise WorkspaceError("Source changed during build; result cannot be published")
        report["artifact_manifest"] = self._artifact_manifest()
        if not any(item["path"] == "index.html" for item in report["artifact_manifest"]):
            raise WorkspaceError("Build produced no index.html")
        report.update(passed=True, entrypoint="index.html", completed_at=time.time())
        return self._finish(report)

    def _finish(self, report: dict[str, Any]) -> dict[str, Any]:
        report["stdout"] = "\n".join(stage["stdout"] for stage in report["stages"])
        report["stderr"] = "\n".join(stage["stderr"] for stage in report["stages"])
        _atomic(self._internal("build.json"), json.dumps(report, ensure_ascii=False, indent=2).encode())
        return report

    def _artifact_manifest(self) -> list[dict[str, Any]]:
        folder = self.root / "dist"
        if folder.is_symlink():
            raise WorkspaceError("Build output must not be a symlink")
        manifest = []
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder).as_posix()
            self._inside(relative, base=folder)
            if path.is_file():
                raw = path.read_bytes()
                manifest.append({"path": relative, "sha256": _digest(raw), "bytes": len(raw)})
        return manifest

    def read_asset(self, relative: str) -> tuple[bytes, str]:
        report_path = self._internal("build.json")
        if not report_path.exists():
            raise WorkspaceError("Project has no completed build")
        report = json.loads(report_path.read_text())
        if not report.get("passed") or report.get("source_sha256") != self.snapshot()["sha256"]:
            raise WorkspaceError("Project build is failed or stale")
        relative = relative or "index.html"
        path = self._inside(relative, base=self.root / "dist")
        expected = next((item for item in report["artifact_manifest"] if item["path"] == relative), None)
        if expected is None:
            raise WorkspaceError("Asset is not in the verified build manifest")
        raw = path.read_bytes()
        if _digest(raw) != expected["sha256"]:
            raise WorkspaceError("Build artifact hash changed")
        mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        if relative.endswith(".js"):
            mime = "text/javascript"
        return raw, mime

    def inline_preview(self) -> str:
        """Package verified output for an opaque-origin sandboxed srcdoc iframe.

        A CSP denies network and nested frames even if generated runtime code
        attempts a fetch. The original multi-file dist remains the deliverable.
        """
        document = self.read_asset("index.html")[0].decode("utf-8")
        report = json.loads(self._internal("build.json").read_text())
        data_urls: dict[str, str] = {}
        for artifact in report["artifact_manifest"]:
            relative = artifact["path"]
            if relative == "index.html" or relative.endswith((".js", ".css")):
                continue
            raw, mime = self.read_asset(relative)
            value = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
            for candidate in (relative, "./" + relative, "/" + relative):
                data_urls[candidate] = value

        def replace_assets(source: str) -> str:
            for name in sorted(data_urls, key=len, reverse=True):
                for quote in ('"', "'"):
                    source = source.replace(quote + name + quote, quote + data_urls[name] + quote)
                source = re.sub(r"url\(\s*" + re.escape(name) + r"\s*\)", lambda _match: 'url("' + data_urls[name] + '")', source)
            return source

        def asset_path(value: str) -> str:
            value = html.unescape(value)
            if value.startswith(("http:", "https:", "//", "data:", "blob:")) or "?" in value or "#" in value:
                raise WorkspaceError("Preview resource must be a manifest-local file")
            return value.removeprefix("./").lstrip("/")

        def inline_script(match: re.Match[str]) -> str:
            attributes = match.group(1)
            src = re.search(r"\bsrc\s*=\s*['\"]([^'\"]+)['\"]", attributes, re.I)
            if src is None:
                return match.group(0)
            relative = asset_path(src.group(1))
            if not relative.endswith(".js"):
                raise WorkspaceError("Preview script is not a bundled JavaScript artifact")
            script = replace_assets(self.read_asset(relative)[0].decode("utf-8"))
            script = re.sub(r"</script", r"<\\/script", script, flags=re.I)
            return '<script type="module">' + script + "</script>"

        def inline_style(match: re.Match[str]) -> str:
            tag = match.group(0)
            if not re.search(r"\brel\s*=\s*['\"]stylesheet['\"]", tag, re.I):
                return tag
            href = re.search(r"\bhref\s*=\s*['\"]([^'\"]+)['\"]", tag, re.I)
            if href is None:
                raise WorkspaceError("Stylesheet has no manifest resource")
            stylesheet = replace_assets(self.read_asset(asset_path(href.group(1)))[0].decode("utf-8"))
            for match_url in re.finditer(r"url\(\s*['\"]?([^'\"\s)]+)", stylesheet):
                if not match_url.group(1).startswith(("data:", "#")):
                    raise WorkspaceError("Preview stylesheet contains an unresolved resource")
            if re.search(r"@import\s", stylesheet):
                raise WorkspaceError("Preview stylesheet contains an external import")
            stylesheet = re.sub(r"</style", r"<\\/style", stylesheet, flags=re.I)
            return "<style>" + stylesheet + "</style>"

        document = re.sub(r"<script\b([^>]*)>\s*</script\s*>", inline_script, document, flags=re.I)
        document = re.sub(r"<link\b[^>]*>", inline_style, document, flags=re.I)
        document = replace_assets(document)
        # Inspect resource attributes outside inline JS/CSS so quoted source code
        # or engine diagnostic strings cannot be mistaken for DOM references.
        stripped = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", "", document, flags=re.I | re.S)
        for resource in re.finditer(r"<(?:script|link|img|audio|video|source|iframe|object|embed)\b[^>]*>", stripped, re.I):
            for reference in re.finditer(r"\b(?:src|href|poster|srcset)\s*=\s*['\"]([^'\"]*)['\"]", resource.group(0), re.I):
                if not reference.group(1).startswith(("data:", "#")):
                    raise WorkspaceError("Preview HTML contains an unresolved resource")
        csp = "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob:; style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; worker-src blob:; object-src 'none'; base-uri 'none'; form-action 'none'"
        meta = '<meta http-equiv="Content-Security-Policy" content="' + html.escape(csp, quote=True) + '">'
        if re.search(r"<head\b[^>]*>", document, re.I):
            document = re.sub(r"(<head\b[^>]*>)", lambda match: match.group(1) + meta, document, count=1, flags=re.I)
        else:
            raise WorkspaceError("Preview build has no HTML head")
        return document
