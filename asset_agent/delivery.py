"""Deliver a retrieval plan as a self-contained, source-preserving asset bundle.

The catalog is read-only.  A completed bundle is installed by renaming a sibling
staging directory, so callers never observe a partially written manifest.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Dict, Union

from .transforms import normalize_resize, transform_asset

if TYPE_CHECKING:
    from .catalog import Catalog


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_output_links(path: Path) -> None:
    # Path.is_symlink() on Python 3.9 does not identify Windows junctions.
    # Inspect reparse points as well, including existing parent directories.
    absolute = Path(os.path.abspath(str(path)))
    for component in (absolute,) + tuple(absolute.parents):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or attributes & reparse_flag:
            raise ValueError("The output path must not traverse a symlink or junction")


def _safe_source(catalog: Catalog, record: dict, root: Path) -> Path:
    """Validate the record independently of the catalog's path resolver."""
    raw = record.get("path")
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise ValueError("An asset must have a nonempty relative path")
    portable = PurePosixPath(raw.replace("\\", "/"))
    windows = PureWindowsPath(raw)
    if portable.is_absolute() or windows.drive or windows.root:
        raise ValueError("Absolute asset paths are forbidden: {!r}".format(raw))
    if ".." in portable.parts:
        raise ValueError("Asset path traversal is forbidden: {!r}".format(raw))

    # Use the catalog interface, but do not accept a resolver redirecting this
    # record to a different file or outside the catalog.
    provided = Path(catalog.resolve_path(record))
    if not provided.is_absolute():
        provided = root / provided
    resolved = provided.resolve(strict=True)
    expected = root.joinpath(*portable.parts).resolve(strict=True)
    if not _within(resolved, root) or not _within(expected, root):
        raise ValueError("An asset path or symlink escapes the catalog")
    if resolved != expected:
        raise ValueError("The catalog resolver returned a different asset path")
    if not stat.S_ISREG(resolved.stat().st_mode):
        raise ValueError("Asset sources must be regular files")
    return resolved


def _extension(record: dict, source: Path) -> str:
    extension = record.get("format") or source.suffix.lstrip(".") or "bin"
    if not isinstance(extension, str):
        raise ValueError("Asset format must be a file extension")
    extension = extension.lower().lstrip(".")
    if re.fullmatch(r"[a-z0-9]{1,16}", extension) is None:
        raise ValueError("Invalid asset file extension: {!r}".format(extension))
    return extension


def _check_embedded_model(source: Path, extension: str) -> None:
    """Single-file delivery requires embedded glTF buffers and images."""
    model_format = source.suffix.lower().lstrip(".")
    if model_format not in {"gltf", "glb"}:
        model_format = extension
    if model_format not in {"gltf", "glb"}:
        return
    if model_format == "gltf":
        with source.open("r", encoding="utf-8-sig") as stream:
            document = json.load(stream)
    else:
        with source.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12:
                raise ValueError("Invalid GLB header")
            magic, version, length = struct.unpack("<4sII", header)
            if magic != b"glTF" or version != 2 or length != source.stat().st_size:
                raise ValueError("Invalid GLB header or length")
            chunk_header = stream.read(8)
            if len(chunk_header) != 8:
                raise ValueError("GLB has no JSON chunk")
            chunk_length, chunk_type = struct.unpack("<II", chunk_header)
            if chunk_type != 0x4E4F534A or chunk_length > length - 20:
                raise ValueError("Invalid GLB JSON chunk")
            document = json.loads(
                stream.read(chunk_length).decode("utf-8").rstrip("\x00 \r\n\t")
            )
    if not isinstance(document, dict):
        raise ValueError("A glTF document must be an object")
    pending = [document]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key == "uri":
                    if not isinstance(value, str) or not value.lower().startswith("data:"):
                        raise ValueError(
                            "External glTF/GLB resources are unsupported; embed buffers and images"
                        )
                elif isinstance(value, (dict, list)):
                    pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)


def _copy_asset(source: Path, assets_dir: Path, extension: str) -> dict:
    """Hash the copied bytes; deduplicate equal content in the staging area."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=".copy-", dir=str(assets_dir))
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    copied_bytes = 0
    try:
        with os.fdopen(descriptor, "wb") as destination, source.open("rb") as original:
            before = os.fstat(original.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Asset sources must be regular files")
            while True:
                chunk = original.read(1024 * 1024)
                if not chunk:
                    break
                destination.write(chunk)
                digest.update(chunk)
                copied_bytes += len(chunk)
            after = os.fstat(original.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("An asset changed during delivery")
        checksum = digest.hexdigest()
        filename = "{}.{}".format(checksum[:16], extension)
        target = assets_dir / filename
        if target.exists():
            # A truncated filename is not sufficient to establish equality.
            if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                raise ValueError("Asset hash-prefix collision")
            temporary_path.unlink()
        else:
            temporary_path.replace(target)
        return {
            "path": "assets/" + filename,
            "sha256": checksum,
            "bytes": copied_bytes,
        }
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _source_metadata(record: dict) -> dict:
    return {
        "url": record.get("source_url", ""),
        "author": record.get("author", ""),
        "license": record.get("license", ""),
        "license_url": record.get("license_url", ""),
    }


def _store_derivative(data: bytes, assets_dir: Path, extension: str) -> dict:
    checksum = hashlib.sha256(data).hexdigest()
    filename = "{}.{}".format(checksum[:16], extension)
    target = assets_dir / filename
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
            raise ValueError("Asset hash-prefix collision")
    else:
        target.write_bytes(data)
    return {"path": "assets/" + filename, "sha256": checksum, "bytes": len(data)}


def _credits(records: list, modifications=None) -> str:
    modifications = modifications or {}
    lines = ["Asset credits", "=============", ""]
    if not records:
        lines.append("No assets were delivered.")
    for record in records:
        lines.extend(
            [
                "Asset: {}".format(record["id"]),
                "Title: {}".format(record.get("name") or record["id"]),
                "Author: {}".format(record.get("author") or "Not provided"),
                "Source: {}".format(record.get("source_url") or "Not provided"),
                "License: {}".format(record.get("license") or "Not provided"),
                "License URL: {}".format(record.get("license_url") or "Not provided"),
            ]
        )
        changes = modifications.get(record["id"], [])
        if changes:
            lines.append("Modifications by this agent (source file retained in catalog):")
            for change in changes:
                lines.append("  Slot {} -> {}".format(change["slot_id"], change["path"]))
                for operation in change["operations"]:
                    lines.append("    " + json.dumps(operation, ensure_ascii=False, sort_keys=True))
        else:
            lines.append("Modifications by this agent: none; files copied verbatim")
        if record.get("attribution"):
            lines.append("Attribution: {}".format(record["attribution"]))
        lines.append("")
    return "\n".join(lines) + "\n"


def _check_output(output: Path, overwrite: bool) -> bool:
    if output.is_symlink():
        raise ValueError("The output directory must not be a symlink")
    if not output.exists():
        return False
    if not output.is_dir():
        raise ValueError("The output path already exists and is not a directory")
    if any(output.iterdir()):
        if overwrite:
            raise ValueError(
                "Safe overwrite of nonempty bundles is unsupported; use a new output directory"
            )
        raise ValueError("Refusing to replace a nonempty output directory")
    return True


def deliver_plan(
    plan: dict,
    catalog: Catalog,
    output_dir: Union[Path, str],
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Copy or explicitly resize assets and return the written JSON manifest.

    The input plan is never mutated. Nonempty output directories are always
    protected, including with ``overwrite=True``. External glTF dependencies
    must be embedded before an asset can be delivered.
    """
    staging = None
    removed_empty_output = False
    installed = False
    output = None
    try:
        if not isinstance(plan, dict):
            raise ValueError("A plan must be a JSON object")
        result = copy.deepcopy(plan)
        if result.get("schema_version") != "1.0":
            raise ValueError("Unsupported plan schema_version")
        if result.get("status") not in {"ready", "partial", "unresolved"}:
            raise ValueError("Unsupported plan status")
        selections = result.get("selections")
        if not isinstance(selections, list):
            raise ValueError("Plan selections must be a list")
        unresolved = result.get("unresolved", [])
        if not isinstance(unresolved, list) or any(not isinstance(item, dict) for item in unresolved):
            raise ValueError("Plan unresolved must be a list of slot objects")
        if result.get("status") == "ready" and any(item.get("required", True) for item in unresolved):
            raise ValueError("A ready plan cannot contain unresolved required slots")
        constraints = result.get("constraints", {})
        if not isinstance(constraints, dict):
            raise ValueError("Plan constraints must be an object")
        for limit in ("max_asset_bytes", "max_total_bytes"):
            if limit in constraints and (
                not isinstance(constraints[limit], int)
                or isinstance(constraints[limit], bool)
                or constraints[limit] < 0
            ):
                raise ValueError("{} must be a nonnegative integer".format(limit))

        root = Path(catalog.root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("The catalog root must be a directory")
        requested_output = Path(output_dir).expanduser()
        _reject_output_links(requested_output)
        output = requested_output.resolve(strict=False)
        if _within(output, root) or _within(root, output):
            raise ValueError("Output and catalog directories must not overlap")
        _check_output(output, overwrite)

        # Reject non-JSON plans before creating any delivery artifacts.
        json.dumps(result, allow_nan=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=".{}.tmp-".format(output.name), dir=str(output.parent))
        ).resolve()
        assets_dir = staging / "assets"
        assets_dir.mkdir()
        delivered_by_id = {}
        delivered_by_path = {}
        transformed_variants = {}
        modifications = {}
        credit_records = []
        seen_slots = set()
        for selection in selections:
            if not isinstance(selection, dict):
                raise ValueError("Each selection must be an object")
            if "slot_id" in selection:
                slot_id = selection["slot_id"]
                if not isinstance(slot_id, str) or not slot_id:
                    raise ValueError("Selection slot_id must be a nonempty string")
                if slot_id in seen_slots:
                    raise ValueError("Duplicate selection slot_id: {}".format(slot_id))
                seen_slots.add(slot_id)
            asset_id = selection.get("asset_id")
            if not isinstance(asset_id, str) or not asset_id:
                raise ValueError("Each selection must have an asset_id")
            if asset_id in delivered_by_id:
                record = delivered_by_id[asset_id][1]
            else:
                record = catalog.get(asset_id)
            if not isinstance(record, dict) or record.get("id") != asset_id:
                raise ValueError("Unknown or inconsistent catalog asset: {}".format(asset_id))
            for selection_key, record_key in (("asset_type", "type"), ("format", "format")):
                if selection_key in selection and selection[selection_key] != record.get(record_key):
                    raise ValueError(
                        "Selection {} does not match catalog asset {}".format(selection_key, asset_id)
                    )
            if asset_id not in delivered_by_id:
                source = _safe_source(catalog, record, root)
                extension = _extension(record, source)
                _check_embedded_model(source, extension)
                file_info = _copy_asset(source, assets_dir, extension)
                delivered_by_id[asset_id] = (file_info, record)
                credit_records.append(record)
            file_info, record = delivered_by_id[asset_id]
            if "sha256" in record and (
                not isinstance(record["sha256"], str)
                or record["sha256"].casefold() != file_info["sha256"]
            ):
                raise ValueError("Catalog sha256 mismatch for asset: {}".format(asset_id))
            if "expected_file" in selection:
                expected = selection["expected_file"]
                if (
                    not isinstance(expected, dict)
                    or expected.get("sha256") != file_info["sha256"]
                    or not isinstance(expected.get("bytes"), int)
                    or isinstance(expected.get("bytes"), bool)
                    or expected.get("bytes") != file_info["bytes"]
                ):
                    raise ValueError("Asset changed after planning: {} (expected_file mismatch)".format(asset_id))
            if "transform_request" in selection:
                transformation = selection["transform_request"]
                if not isinstance(transformation, dict) or set(transformation) != {"resize"}:
                    raise ValueError("transform_request must contain only resize")
                resize = normalize_resize(transformation["resize"])
                selection["transform_request"] = {"resize": resize}
                variant_key = (asset_id, json.dumps(resize, sort_keys=True))
                if variant_key not in transformed_variants:
                    transformed = transform_asset(
                        staging / file_info["path"],
                        asset_type=record["type"], source_format=record["format"],
                        resize=resize, animations=record.get("animations"),
                    )
                    derivative = _store_derivative(transformed["data"], assets_dir, transformed["format"])
                    transformed_variants[variant_key] = (
                        derivative, {key: value for key, value in transformed.items() if key != "data"}
                    )
                derivative, metadata = transformed_variants[variant_key]
                selection["source_file"] = dict(file_info, format=record["format"])
                selection["source_file"].pop("path")
                selection["source_metadata"] = {
                    key: copy.deepcopy(record[key]) for key in ("width", "height", "animations", "triangles") if key in record
                }
                selection["format"] = metadata["format"]
                selection["metadata"] = dict(selection.get("metadata", {}), width=metadata["width"], height=metadata["height"])
                selection["transforms"] = copy.deepcopy(metadata["operations"])
                file_info = derivative
                modifications.setdefault(asset_id, []).append({
                    "slot_id": selection.get("slot_id", asset_id),
                    "path": derivative["path"], "operations": metadata["operations"],
                })
            if "max_asset_bytes" in constraints and file_info["bytes"] > constraints["max_asset_bytes"]:
                raise ValueError("Delivered asset {} exceeds max_asset_bytes".format(asset_id))
            usage = selection.setdefault("usage", {})
            if not isinstance(usage, dict):
                raise ValueError("Selection usage must be an object")
            selection["file"] = dict(file_info)
            selection["source"] = _source_metadata(record)
            usage["runtime_url"] = "./" + file_info["path"]
            if "transform_request" in selection:
                usage["render_as"] = "ui-svg" if record["type"] == "icon" and selection["format"] == "svg" else "texture"
            delivered_by_path[file_info["path"]] = file_info

        # Source copies are private transform inputs. Keep only files actually
        # referenced by a final selection; catalog originals are never edited.
        for raw_file, _ in delivered_by_id.values():
            if raw_file["path"] not in delivered_by_path:
                provisional = staging / raw_file["path"]
                if provisional.exists():
                    provisional.unlink()

        total_bytes = sum(item["bytes"] for item in delivered_by_path.values())
        if "max_total_bytes" in constraints and total_bytes > constraints["max_total_bytes"]:
            raise ValueError("Copied assets exceed max_total_bytes")
        result["bundle"] = {
            "manifest": "manifest.json",
            "credits": "CREDITS.txt",
            "asset_count": len(delivered_by_path),
            "total_bytes": total_bytes,
        }
        # Round-tripping makes the return value identical to JSON on disk.
        manifest_text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        result = json.loads(manifest_text)
        (staging / "manifest.json").write_text(manifest_text, encoding="utf-8")
        (staging / "CREDITS.txt").write_text(_credits(credit_records, modifications), encoding="utf-8")

        # Recheck immediately before installation, so new user files are not
        # removed. rmdir refuses a directory that became nonempty meanwhile.
        _reject_output_links(requested_output)
        if _check_output(output, overwrite):
            output.rmdir()
            removed_empty_output = True
        os.replace(str(staging), str(output))
        installed = True
        return result
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("Asset bundle delivery failed: {}".format(exc)) from exc
    finally:
        if staging is not None and staging.exists():
            # Only recursively delete the staging directory created here.
            if (
                not staging.is_symlink()
                and staging.resolve() == staging
                and output is not None
                and staging.parent == output.parent
                and staging.name.startswith(".{}.tmp-".format(output.name))
            ):
                shutil.rmtree(staging)
        if removed_empty_output and not installed and output is not None and not output.exists():
            output.mkdir()
