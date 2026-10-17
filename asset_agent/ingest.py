"""Import explicitly licensed, self-contained asset packs into a local catalog.

``pack.json`` optionally contains ``defaults`` and ``assets`` dictionaries.  The
keys in ``assets`` are paths relative to the source directory, for example::

    {"defaults": {"tags": ["cartoon"]},
     "assets": {"car.png": {"name": "Blue racing car", "type": "sprite"}}}

Metadata may describe files; it cannot choose destination paths or override the
explicit license, author or source arguments. Imports never extract archives.
"""

import base64
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import struct
import tempfile
from urllib.parse import unquote_to_bytes
import xml.etree.ElementTree as ET

from .catalog import ASSET_TYPES, Catalog


_EXTENSIONS = {
    ".svg": "icon", ".png": "sprite", ".webp": "sprite",
    ".jpg": "sprite", ".jpeg": "sprite", ".glb": "model",
    ".gltf": "model", ".wav": "audio", ".ogg": "audio", ".mp3": "audio",
    ".woff": "font", ".woff2": "font", ".ttf": "font", ".otf": "font",
}
_METADATA_FIELDS = frozenset({
    "name", "description", "tags", "type", "animations", "width", "height",
    "triangles", "attribution",
})


def _nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be an explicit nonempty string".format(label))
    return value.strip()


def _link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def _check_path_chain(path):
    """Reject Windows junctions as well as symbolic links, including parents."""
    for candidate in reversed((path,) + tuple(path.parents)):
        if candidate.exists() or candidate.is_symlink():
            if _link(candidate):
                raise ValueError("Symbolic links and reparse points are forbidden: {}".format(candidate))
            if candidate != path and not candidate.is_dir():
                raise ValueError("Asset path has a non-directory parent: {}".format(candidate))


def _relative(raw):
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("Metadata asset paths must be nonempty relative paths")
    portable = PurePosixPath(raw.replace("\\", "/"))
    windows = PureWindowsPath(raw)
    if (windows.drive or windows.root or portable.is_absolute()
            or ".." in portable.parts or not portable.parts
            or any(":" in part for part in portable.parts)):
        raise ValueError("Unsafe asset path: {}".format(raw))
    return portable.as_posix()


def _metadata(source, available):
    manifest = source / "pack.json"
    if not manifest.exists():
        return {}, {}
    try:
        document = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError("Cannot read pack.json: {}".format(exc)) from exc
    if not isinstance(document, dict) or set(document) - {"defaults", "assets"}:
        raise ValueError("pack.json must contain only defaults and assets objects")
    defaults = document.get("defaults", {})
    entries = document.get("assets", {})
    if not isinstance(defaults, dict) or not isinstance(entries, dict):
        raise ValueError("pack.json defaults and assets must be objects")
    result = {}
    for raw, override in entries.items():
        relative = _relative(raw)
        if relative in result:
            raise ValueError("Duplicate normalized metadata path: {}".format(relative))
        if relative not in available:
            raise ValueError("Metadata references a missing or unsupported asset: {}".format(relative))
        result[relative] = override
    for label, values in [("defaults", defaults)] + list(result.items()):
        if not isinstance(values, dict) or set(values) - _METADATA_FIELDS:
            raise ValueError("Unknown metadata fields or invalid object: {}".format(label))
    return defaults, result


def _svg_details(data):
    if re.search(br"<!\s*(DOCTYPE|ENTITY)\b", data, re.I):
        raise ValueError("SVG DTDs and entities are forbidden")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG: {}".format(exc)) from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("SVG must have an svg root element")
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() == "script":
            raise ValueError("SVG scripts are forbidden")
        for key, value in node.attrib.items():
            attribute = key.rsplit("}", 1)[-1].lower()
            if attribute.startswith("on"):
                raise ValueError("SVG event handlers are forbidden")
            if attribute in {"href", "src"} and not value.strip().startswith(("#", "data:")):
                raise ValueError("SVG external resources are forbidden")
        text = " ".join(list(node.attrib.values()) + [node.text or ""])
        if re.search(r"@import\b", text, re.I):
            raise ValueError("SVG external stylesheets are forbidden")
        for match in re.finditer(r"url\(\s*['\"]?([^)'\"]+)", text, re.I):
            if not match.group(1).strip().startswith(("#", "data:")):
                raise ValueError("SVG external resources are forbidden")
    dimensions = {}
    viewbox = root.get("viewBox", "").replace(",", " ").split()
    for index, name in ((2, "width"), (3, "height")):
        raw = root.get(name, "")
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*", raw)
        value = float(match.group(1)) if match else None
        if value is None and len(viewbox) == 4:
            try:
                value = float(viewbox[index])
            except ValueError:
                raise ValueError("SVG has an invalid viewBox")
        if value is not None:
            if not math.isfinite(value) or value <= 0:
                raise ValueError("SVG dimensions must be positive")
            dimensions[name] = int(math.ceil(value))
    return dimensions


def _data_uri(uri):
    if not isinstance(uri, str) or not uri.startswith("data:") or "," not in uri:
        raise ValueError("3D assets must embed resources as data URIs; external URI forbidden")
    header, payload = uri.split(",", 1)
    try:
        data = base64.b64decode(payload, validate=True) if header.endswith(";base64") else unquote_to_bytes(payload)
    except ValueError as exc:
        raise ValueError("Invalid embedded data URI") from exc
    if not data:
        raise ValueError("Embedded resource is empty")
    return data


def _model_details(data, suffix):
    binary = None
    if suffix == ".glb":
        if len(data) < 20:
            raise ValueError("Invalid GLB header")
        magic, version, total = struct.unpack_from("<4sII", data)
        if magic != b"glTF" or version != 2 or total != len(data):
            raise ValueError("Only complete GLB version 2 files are supported")
        cursor, chunks = 12, []
        while cursor < len(data):
            if cursor + 8 > len(data):
                raise ValueError("Truncated GLB chunk")
            length, kind = struct.unpack_from("<II", data, cursor)
            cursor += 8
            if length % 4 or cursor + length > len(data):
                raise ValueError("Invalid GLB chunk length")
            chunks.append((kind, data[cursor:cursor + length]))
            cursor += length
        if not chunks or chunks[0][0] != 0x4E4F534A:
            raise ValueError("GLB requires a first JSON chunk")
        if len(chunks) > 2 or (len(chunks) == 2 and chunks[1][0] != 0x004E4942):
            raise ValueError("Unsupported GLB chunk arrangement")
        json_data = chunks[0][1].rstrip(b" \t\n\r\0")
        binary = chunks[1][1] if len(chunks) > 1 else None
    else:
        json_data = data
    try:
        document = json.loads(json_data)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid glTF JSON") from exc
    if (not isinstance(document, dict) or not isinstance(document.get("asset"), dict)
            or document["asset"].get("version") != "2.0"):
        raise ValueError("Only glTF version 2.0 is supported")

    def inspect_uris(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "uri":
                    _data_uri(child)
                else:
                    inspect_uris(child)
        elif isinstance(value, list):
            for child in value:
                inspect_uris(child)

    inspect_uris(document)
    buffers = document.get("buffers", [])
    if not isinstance(buffers, list):
        raise ValueError("glTF buffers must be a list")
    for index, buffer in enumerate(buffers):
        length = buffer.get("byteLength") if isinstance(buffer, dict) else None
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise ValueError("Invalid glTF buffer length")
        if "uri" in buffer:
            contents = _data_uri(buffer["uri"])
        elif suffix == ".glb" and index == 0 and binary is not None:
            contents = binary
        else:
            raise ValueError("glTF buffer has no embedded content")
        if len(contents) < length:
            raise ValueError("Embedded glTF buffer is truncated")
    views = document.get("bufferViews", [])
    if not isinstance(views, list) or any(not isinstance(view, dict) for view in views):
        raise ValueError("glTF bufferViews must be objects")
    for view in views:
        index = view.get("buffer")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(buffers):
            raise ValueError("glTF bufferView references a missing buffer")
        offset, length = view.get("byteOffset", 0), view.get("byteLength", -1)
        if (not isinstance(offset, int) or not isinstance(length, int)
                or min(offset, length) < 0 or offset + length > buffers[index]["byteLength"]):
            raise ValueError("glTF bufferView extends beyond its buffer")
    images = document.get("images", [])
    if not isinstance(images, list) or any(not isinstance(image, dict) for image in images):
        raise ValueError("glTF images must be objects")
    for image in images:
        if "uri" not in image:
            index = image.get("bufferView")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(views):
                raise ValueError("glTF image references a missing bufferView")
    accessors = document.get("accessors", [])
    if not isinstance(accessors, list) or any(not isinstance(item, dict) for item in accessors):
        raise ValueError("glTF accessors must be objects")
    for accessor in accessors:
        if "bufferView" in accessor:
            index = accessor["bufferView"]
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(views):
                raise ValueError("glTF accessor references a missing bufferView")
    animations = document.get("animations", [])
    if not isinstance(animations, list) or any(not isinstance(item, dict) for item in animations):
        raise ValueError("glTF animations must be objects")

    def objects(key):
        items = document.get(key, [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("glTF {} must be objects".format(key))
        return items

    def reference(index, items, label):
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items):
            raise ValueError("glTF {} references a missing resource".format(label))

    meshes, nodes, scenes = objects("meshes"), objects("nodes"), objects("scenes")
    materials, textures, samplers = objects("materials"), objects("textures"), objects("samplers")
    skins, cameras = objects("skins"), objects("cameras")
    for mesh in meshes:
        primitives = mesh.get("primitives")
        if not isinstance(primitives, list) or not primitives:
            raise ValueError("glTF mesh requires a primitives list")
        for primitive in primitives:
            if not isinstance(primitive, dict) or not isinstance(primitive.get("attributes"), dict):
                raise ValueError("glTF primitive requires attributes")
            for index in primitive["attributes"].values():
                reference(index, accessors, "mesh attribute accessor")
            for key, items in (("indices", accessors), ("material", materials)):
                if key in primitive:
                    reference(primitive[key], items, "primitive " + key)
            targets = primitive.get("targets", [])
            if not isinstance(targets, list) or any(not isinstance(target, dict) for target in targets):
                raise ValueError("glTF morph targets must be objects")
            for target in targets:
                for index in target.values():
                    reference(index, accessors, "morph target accessor")
    for node in nodes:
        for key, items in (("mesh", meshes), ("skin", skins), ("camera", cameras)):
            if key in node:
                reference(node[key], items, "node " + key)
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ValueError("glTF node children must be a list")
        for index in children:
            reference(index, nodes, "node child")
    for scene in scenes:
        roots = scene.get("nodes", [])
        if not isinstance(roots, list):
            raise ValueError("glTF scene nodes must be a list")
        for index in roots:
            reference(index, nodes, "scene node")
    if "scene" in document:
        reference(document["scene"], scenes, "default scene")
    for texture in textures:
        for key, items in (("source", images), ("sampler", samplers)):
            if key in texture:
                reference(texture[key], items, "texture " + key)
    for skin in skins:
        for key, items in (("inverseBindMatrices", accessors), ("skeleton", nodes)):
            if key in skin:
                reference(skin[key], items, "skin " + key)
        joints = skin.get("joints", [])
        if not isinstance(joints, list):
            raise ValueError("glTF skin joints must be a list")
        for index in joints:
            reference(index, nodes, "skin joint")
    for animation in animations:
        animation_samplers = animation.get("samplers", [])
        channels = animation.get("channels", [])
        if (not isinstance(animation_samplers, list) or not isinstance(channels, list)
                or any(not isinstance(item, dict) for item in animation_samplers + channels)):
            raise ValueError("glTF animation samplers and channels must be objects")
        for sampler in animation_samplers:
            for key in ("input", "output"):
                reference(sampler.get(key), accessors, "animation " + key)
        for channel in channels:
            reference(channel.get("sampler"), animation_samplers, "animation sampler")
            target = channel.get("target")
            if not isinstance(target, dict):
                raise ValueError("glTF animation target must be an object")
            if "node" in target:
                reference(target["node"], nodes, "animation target node")

    def inspect_resource_indices(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "extras":
                    # Application-specific metadata is not a glTF reference.
                    continue
                if key == "bufferView":
                    reference(child, views, "bufferView")
                elif key.endswith("Texture") and isinstance(child, dict) and "index" in child:
                    reference(child["index"], textures, "material texture")
                else:
                    inspect_resource_indices(child)
        elif isinstance(value, list):
            for child in value:
                inspect_resource_indices(child)

    inspect_resource_indices(document)
    named_animations = []
    for animation in animations:
        name = animation.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError("glTF animation names must be strings")
        if name and name.strip():
            named_animations.append(name)
    # Runtime loaders assign different names to unnamed clips. Only explicit
    # source names are portable references for the downstream game agent.
    return {"animations": named_animations}


def _details(data, suffix):
    if suffix == ".svg":
        return _svg_details(data)
    if suffix == ".png":
        if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise ValueError("Invalid PNG header")
        width, height = struct.unpack_from(">II", data, 16)
        if not width or not height:
            raise ValueError("PNG dimensions must be positive")
        return {"width": width, "height": height}
    if suffix in {".glb", ".gltf"}:
        return _model_details(data, suffix)
    return {}


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48] or "asset"


def import_pack(source_dir, catalog_dir, *, pack, style, license_id, author,
                source_url, license_url, tags=None, asset_type=None):
    """Validate and copy a pack; return ``{imported, skipped, ids}``.

    Reimporting identical files and metadata is a no-op. Existing files or
    records with conflicting content are never overwritten. Every source file
    is validated before publication; a failed import leaves the old manifest
    intact. Self-contained glTF/GLB files are supported; external dependencies
    must first be embedded using an authoring tool.
    """
    values = {key: _nonempty(value, key) for key, value in {
        "pack": pack, "style": style, "license": license_id, "author": author,
        "source_url": source_url, "license_url": license_url,
    }.items()}
    if tags is None:
        tags = []
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ValueError("tags must be a list of nonempty strings")
    if asset_type is not None and asset_type not in ASSET_TYPES:
        raise ValueError("Unknown asset_type: {}".format(asset_type))
    source = Path(os.path.abspath(os.path.expanduser(str(source_dir))))
    destination = Path(os.path.abspath(os.path.expanduser(str(catalog_dir))))
    _check_path_chain(source)
    _check_path_chain(destination)
    source, destination = source.resolve(), destination.resolve()
    if not source.is_dir():
        raise ValueError("Source directory does not exist")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and catalog directories must not contain one another")
    if destination.exists() and not destination.is_dir():
        raise ValueError("Catalog path must be a directory")
    files = {}
    for directory, names, filenames in os.walk(str(source), followlinks=False):
        for name in names + filenames:
            path = Path(directory) / name
            if _link(path):
                raise ValueError("Symbolic links and reparse points are forbidden: {}".format(path))
            if path.is_file() and path.suffix.lower() in _EXTENSIONS:
                files[path.relative_to(source).as_posix()] = path
    if not files:
        raise ValueError("No supported assets were found; extract archives before importing")
    defaults, overrides = _metadata(source, files)
    records, payloads = [], {}
    for relative, path in sorted(files.items()):
        data = path.read_bytes()
        if not data:
            raise ValueError("Asset is empty: {}".format(relative))
        suffix = path.suffix.lower()
        digest = hashlib.sha256((values["pack"] + "\0" + relative).encode("utf-8")).hexdigest()[:16]
        asset_id = "{}-{}-{}".format(_slug(values["pack"]), _slug(path.stem), digest)
        record = dict(values, id=asset_id, name=path.stem.replace("_", " ").replace("-", " "),
                      description="{} from {}".format(path.stem, values["pack"]),
                      tags=list(tags), type=asset_type or _EXTENSIONS[suffix],
                      path="assets/{}{}".format(asset_id, suffix), format=suffix[1:])
        details = _details(data, suffix)
        record.update(details)
        record.update(defaults)
        record.update(overrides.get(relative, {}))
        Catalog._validate_record(record)
        if record["type"] == "model" and not set(record.get("animations", [])).issubset(details["animations"]):
            raise ValueError("Model animation metadata must use real clip names with their original case: {}".format(relative))
        records.append(record)
        payloads[record["path"]] = data
    manifest = destination / "catalog.json"
    previous_bytes = None
    existing, by_id, by_path = [], {}, {}
    if manifest.exists():
        _check_path_chain(manifest)
        catalog = Catalog(destination)
        previous_bytes = manifest.read_bytes()
        existing = catalog.assets
        for record in existing:
            _check_path_chain(destination / record["path"])
            catalog.resolve_path(record)
            by_id[record["id"]] = record
            if record["path"].lower() in by_path:
                raise ValueError("Catalog contains duplicate asset paths")
            by_path[record["path"].lower()] = record
    additions = []
    for record in records:
        target = destination / record["path"]
        _check_path_chain(target)
        previous = by_id.get(record["id"])
        if previous is not None:
            if previous != record or target.read_bytes() != payloads[record["path"]]:
                raise ValueError("Import conflicts with existing asset: {}".format(record["id"]))
        elif record["path"].lower() in by_path or target.exists():
            raise ValueError("Refusing to overwrite an existing file: {}".format(target))
        else:
            additions.append(record)
    result = {"imported": len(additions), "skipped": len(records) - len(additions),
              "ids": [record["id"] for record in records]}
    if not additions:
        return result
    document = {"schema_version": "1.0", "assets": existing + additions}
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    created, directories, temporary, lock = [], [], None, None

    def mkdir(path):
        if path.exists():
            return
        mkdir(path.parent)
        path.mkdir()
        directories.append(path)

    try:
        mkdir(destination)
        lock = destination / ".asset-import.lock"
        try:
            lock_handle = lock.open("xb")
        except FileExistsError as exc:
            lock = None
            raise ValueError("Another import is active or .asset-import.lock already exists") from exc
        lock_handle.close()
        current = manifest.read_bytes() if manifest.exists() else None
        if current != previous_bytes:
            raise ValueError("Catalog changed during import; retry the operation")
        for record in additions:
            target = destination / record["path"]
            _check_path_chain(target)
            mkdir(target.parent)
            with target.open("xb") as handle:
                created.append(target)
                handle.write(payloads[record["path"]])
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".catalog-", suffix=".tmp",
                                         dir=str(destination), delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(manifest))
        temporary = None
    except Exception:
        for target in reversed(created):
            target.unlink()
        raise
    finally:
        if temporary is not None:
            temporary.unlink()
        if lock is not None:
            lock.unlink()
        for directory in reversed(directories):
            try:
                directory.rmdir()
            except OSError:
                pass
    return result
