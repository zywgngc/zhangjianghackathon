"""Validated, local-only asset catalog loading (Python 3.9+)."""

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


ASSET_TYPES = frozenset({"icon", "sprite", "tileset", "model", "audio", "font"})
_RASTER_FORMATS = frozenset({"png", "webp", "jpg", "jpeg"})
_TYPE_FORMATS = {
    "icon": _RASTER_FORMATS | {"svg"},
    "sprite": _RASTER_FORMATS | {"svg"},
    "tileset": _RASTER_FORMATS,
    "model": frozenset({"glb", "gltf"}),
    "audio": frozenset({"wav", "ogg", "mp3"}),
    "font": frozenset({"woff", "woff2", "ttf", "otf"}),
}
ASSET_FORMATS = frozenset().union(*_TYPE_FORMATS.values())


def canonical_format(value):
    """Normalize extension spellings for comparisons, not arbitrary formats."""
    value = value.casefold().lstrip(".")
    return "jpg" if value == "jpeg" else value


_REQUIRED_STRINGS = (
    "id", "type", "name", "description", "style", "pack", "path", "format",
    "license", "author", "source_url", "license_url",
)


class Catalog:
    """Load ``catalog.json``; resolve every asset inside the catalog directory.

    File errors are available through ``validate_files`` so a damaged asset does
    not prevent inspection of the remaining catalog. Schema errors fail early.
    """

    def __init__(self, catalog_dir):
        self.root = Path(catalog_dir).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("Catalog directory does not exist: {}".format(self.root))
        manifest = self.root / "catalog.json"
        try:
            manifest.resolve(strict=True).relative_to(self.root)
            with manifest.open("r", encoding="utf-8-sig") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ValueError("Cannot read an in-directory catalog.json: {}".format(exc)) from exc
        if not isinstance(document, dict) or document.get("schema_version") != "1.0":
            raise ValueError("Unsupported catalog schema_version; expected '1.0'")
        if not isinstance(document.get("assets"), list):
            raise ValueError("Catalog assets must be a list")
        self.assets = document["assets"]
        self._by_id = {}
        for record in self.assets:
            self._validate_record(record)
            asset_id = record["id"]
            if asset_id in self._by_id:
                raise ValueError("Duplicate asset ID: {}".format(asset_id))
            self._by_id[asset_id] = record

    @staticmethod
    def _validate_record(record):
        if not isinstance(record, dict):
            raise ValueError("Every asset record must be an object")
        label = record.get("id", "<unknown>")
        for field in _REQUIRED_STRINGS:
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Asset {} needs a nonempty string {}".format(label, field))
        if record["type"] not in ASSET_TYPES:
            raise ValueError("Unknown asset type for {}: {}".format(label, record["type"]))
        if record["format"] not in ASSET_FORMATS:
            raise ValueError("Unsupported asset format for {}: {}".format(label, record["format"]))
        if record["format"] not in _TYPE_FORMATS[record["type"]]:
            raise ValueError("Asset {} format {} is incompatible with type {}".format(label, record["format"], record["type"]))
        suffix = PurePosixPath(record["path"].replace("\\", "/")).suffix
        if canonical_format(suffix) != canonical_format(record["format"]):
            raise ValueError("Asset {} format does not match its path suffix".format(label))
        for field in ("tags", "animations"):
            if field == "animations" and field not in record:
                continue
            values = record.get(field)
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError("Asset {} {} must be a list of strings".format(label, field))
        for field in ("width", "height", "triangles"):
            if field not in record:
                continue
            value = record[field]
            minimum = 0 if field == "triangles" else 1
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError("Asset {} has invalid {}".format(label, field))
        if "attribution" in record and not isinstance(record["attribution"], str):
            raise ValueError("Asset {} attribution must be a string".format(label))
        if "sha256" in record and (not isinstance(record["sha256"], str) or re.fullmatch(r"[0-9a-fA-F]{64}", record["sha256"]) is None):
            raise ValueError("Asset {} sha256 must contain 64 hexadecimal characters".format(label))

    def get(self, asset_id):
        return self._by_id.get(asset_id)

    def resolve_path(self, record):
        """Reject absolute paths, traversal, alternate streams and escaping links."""
        raw = record.get("path") if isinstance(record, dict) else None
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            raise ValueError("Asset path must be a nonempty relative file path")
        windows = PureWindowsPath(raw)
        portable = PurePosixPath(raw.replace("\\", "/"))
        if windows.drive or windows.root or portable.is_absolute():
            raise ValueError("Absolute asset paths are forbidden")
        if ".." in portable.parts or not portable.parts or portable == PurePosixPath("."):
            raise ValueError("Asset path traversal is forbidden")
        if any(":" in component for component in portable.parts):
            raise ValueError("Asset paths cannot contain alternate streams")
        candidate = self.root.joinpath(*portable.parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError("Asset file missing or outside catalog: {}".format(raw)) from exc
        if not resolved.is_file():
            raise ValueError("Asset path is not a file: {}".format(raw))
        return resolved

    def validate_files(self):
        issues = []
        for record in self.assets:
            try:
                path = self.resolve_path(record)
                if "sha256" in record:
                    digest = hashlib.sha256()
                    with path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != record["sha256"].casefold():
                        raise ValueError("Asset SHA-256 checksum does not match catalog")
            except (ValueError, OSError) as exc:
                issues.append({"asset_id": record["id"], "reason": str(exc)})
        return issues
