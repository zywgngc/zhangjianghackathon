"""Explicit, source-preserving resize operations for single-frame game assets.

SVG resizing uses only the standard library. Raster resizing imports Pillow
only when requested. ``allow_upscale`` constrains raster pixels; scalable SVG
geometry can always be displayed at the requested target size.
"""

from __future__ import annotations

import io
import math
import re
import stat
import warnings
from contextlib import ExitStack
from pathlib import Path
from typing import Optional, Union
from xml.etree import ElementTree as ET


MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_TARGET_DIMENSION = 8192
MAX_TARGET_PIXELS = 16_000_000
_RESIZE_KEYS = frozenset({"width", "height", "mode", "resample", "allow_upscale", "output_format"})
_RASTER_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}
_SVG_NS = "http://www.w3.org/2000/svg"


def normalize_resize(value: dict) -> dict:
    """Validate an explicit resize request and return a fresh normalized dict."""
    if not isinstance(value, dict):
        raise ValueError("resize must be an object")
    unknown = set(value) - _RESIZE_KEYS
    if unknown:
        raise ValueError("Unknown resize instruction: {}".format(", ".join(sorted(map(str, unknown)))))
    result = dict(value)
    for name in ("width", "height"):
        dimension = result.get(name)
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise ValueError("resize.{} must be an integer".format(name))
        if not 1 <= dimension <= MAX_TARGET_DIMENSION:
            raise ValueError("resize.{} must be between 1 and 8192".format(name))
    if result["width"] * result["height"] > MAX_TARGET_PIXELS:
        raise ValueError("resize target must not exceed 16 million pixels")
    result.setdefault("mode", "contain")
    result.setdefault("resample", "lanczos")
    result.setdefault("allow_upscale", False)
    if result["mode"] not in ("contain", "cover", "stretch"):
        raise ValueError("resize.mode must be contain, cover, or stretch")
    if result["resample"] not in ("lanczos", "nearest"):
        raise ValueError("resize.resample must be lanczos or nearest")
    if not isinstance(result["allow_upscale"], bool):
        raise ValueError("resize.allow_upscale must be a boolean")
    if "output_format" in result and result["output_format"] not in ("png", "webp", "jpeg"):
        raise ValueError("resize.output_format must be png, webp, or jpeg")
    return result


def _read_source(source: Union[Path, str]) -> bytes:
    path = Path(source)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Resize source must be a regular file")
    if metadata.st_size > MAX_SOURCE_BYTES:
        raise ValueError("Resize source exceeds the 64 MiB limit")
    with path.open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("Resize source exceeds the 64 MiB limit")
    return data


def _size(width, height) -> dict:
    return {"width": width, "height": height}


def _resize_operation(before: dict, resize: dict) -> dict:
    return {
        "operation": "resize",
        "before": dict(before),
        "after": _size(resize["width"], resize["height"]),
        "mode": resize["mode"],
        "resample": resize["resample"],
        "allow_upscale": resize["allow_upscale"],
    }


def _svg_length(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    match = re.fullmatch(
        r"\s*([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(px|in|cm|mm|pt|pc)?\s*",
        value,
        re.IGNORECASE,
    )
    if match is None:
        return None
    unit = (match.group(2) or "px").lower()
    scale = {"px": 1, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4, "pt": 96 / 72, "pc": 16}[unit]
    result = float(match.group(1)) * scale
    return result if math.isfinite(result) and result > 0 else None


def _check_svg_css(value: str) -> None:
    # CSS escape sequences can disguise url()/@import; the supported icon
    # subset has no reason to need them. Keep fragment paint references.
    if "\\" in value or re.search(r"@\s*import\b", value, re.IGNORECASE):
        raise ValueError("SVG CSS imports and escapes are unsupported")
    for match in re.finditer(r"url\s*\(([^)]*)\)", value, re.IGNORECASE):
        target = match.group(1).strip().strip("\"'").strip()
        if not target.startswith("#"):
            raise ValueError("External SVG references are forbidden")


def _check_svg_geometry_css(value: str) -> None:
    """Reject CSS that could override the declared target SVG viewport.

    This intentionally restricts the supported SVG subset instead of parsing
    selectors or attempting to rewrite a stylesheet. Geometry declarations in
    any style element are rejected, even if a selector appears child-only.
    """
    without_comments = re.sub(r"/\*.*?(?:\*/|$)", "", value, flags=re.DOTALL)
    geometry = (
        r"(?<![-\w])(?:-(?:webkit|moz|ms|o)-)?(?:"
        r"(?:min-|max-)?(?:width|height|inline-size|block-size|logical-width|logical-height)"
        r"|aspect-ratio|all|box-sizing|zoom"
        r"|(?:margin|padding)(?:-[a-z-]+)?"
        r"|border(?:-[a-z-]+)?"
        r"|contain-intrinsic-(?:size|width|height|inline-size|block-size)"
        r")\s*:"
    )
    if re.search(geometry, without_comments, re.IGNORECASE):
        raise ValueError("SVG CSS geometry declarations are unsupported; target dimensions must remain explicit")


def _transform_svg(data: bytes, resize: dict) -> dict:
    if "output_format" in resize:
        raise ValueError("SVG-to-raster conversion is unsupported; omit output_format for SVG")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("SVG resize requires UTF-8 XML") from exc
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ValueError("SVG DTDs and entities are forbidden")
    if re.search(r"<\?(?!xml(?:\s|\?>))", text, re.IGNORECASE):
        raise ValueError("SVG processing instructions are unsupported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG XML: {}".format(exc)) from exc
    if root.tag not in ("svg", "{" + _SVG_NS + "}svg"):
        raise ValueError("SVG source must have an svg root element")
    forbidden_elements = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video"}
    animated_elements = {"animate", "animatemotion", "animatetransform", "set", "discard"}
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1].lower()
        if local_name in forbidden_elements:
            raise ValueError("Active SVG elements are forbidden")
        if local_name in animated_elements:
            raise ValueError("Animated SVG assets cannot be resized")
        if local_name == "style":
            stylesheet = "".join(element.itertext())
            _check_svg_css(stylesheet)
            _check_svg_geometry_css(stylesheet)
        for key, value in element.attrib.items():
            local_key = key.rsplit("}", 1)[-1].lower()
            if local_key.startswith("on") or key == "{http://www.w3.org/XML/1998/namespace}base":
                raise ValueError("SVG event handlers and XML base URLs are forbidden")
            if local_key in {"href", "src"}:
                target = value.strip()
                if not target.startswith("#"):
                    raise ValueError("SVG references must use local #fragments; embedded images are unsupported")
            _check_svg_css(value)
            if element is root and local_key == "style":
                _check_svg_geometry_css(value)

    raw_viewbox = root.get("viewBox")
    if raw_viewbox is not None:
        try:
            viewbox = [float(part) for part in re.split(r"[\s,]+", raw_viewbox.strip())]
        except ValueError as exc:
            raise ValueError("SVG viewBox must contain four finite numbers") from exc
        if len(viewbox) != 4 or not all(math.isfinite(number) for number in viewbox) or min(viewbox[2:]) <= 0:
            raise ValueError("SVG viewBox must contain four finite numbers with positive size")
        source_width = _svg_length(root.get("width")) or viewbox[2]
        source_height = _svg_length(root.get("height")) or viewbox[3]
    else:
        source_width = _svg_length(root.get("width"))
        source_height = _svg_length(root.get("height"))
        if source_width is None or source_height is None:
            raise ValueError("SVG without viewBox needs positive absolute width and height")
        root.set("viewBox", "0 0 {:g} {:g}".format(source_width, source_height))
    aspect_ratio = {"contain": "xMidYMid meet", "cover": "xMidYMid slice", "stretch": "none"}[resize["mode"]]
    root.set("width", str(resize["width"]))
    root.set("height", str(resize["height"]))
    root.set("preserveAspectRatio", aspect_ratio)
    operation = _resize_operation(_size(source_width, source_height), resize)
    operation.update(
        vector_scalable=True,
        raster_resampling=False,
        allow_upscale_applies=False,
        preserve_aspect_ratio=aspect_ratio,
        viewBox=root.get("viewBox"),
    )
    return {
        "data": ET.tostring(root, encoding="utf-8", xml_declaration=True),
        "format": "svg",
        "width": resize["width"],
        "height": resize["height"],
        "operations": [operation],
    }


def _transform_raster(data: bytes, source_format: str, resize: dict) -> dict:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ValueError("Raster resize requires Pillow; install game-asset-agent[images]") from exc
    operations = []
    with warnings.catch_warnings(), ExitStack() as stack:
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        original = stack.enter_context(Image.open(io.BytesIO(data)))
        if original.format != _RASTER_FORMATS[source_format]:
            raise ValueError("Raster content does not match the declared source_format")
        if getattr(original, "n_frames", 1) != 1 or getattr(original, "is_animated", False):
            raise ValueError("Animated raster assets cannot be resized")
        orientation = original.getexif().get(274, 1)
        upright = stack.enter_context(ImageOps.exif_transpose(original))
        if orientation in (2, 3, 4, 5, 6, 7, 8):
            operations.append({
                "operation": "exif_transpose", "orientation": orientation,
                "before": _size(*original.size), "after": _size(*upright.size),
            })
        image = stack.enter_context(upright.convert("RGBA"))
        source_width, source_height = image.size
        target_width, target_height = resize["width"], resize["height"]
        operation = _resize_operation(_size(source_width, source_height), resize)
        resample = Image.Resampling.NEAREST if resize["resample"] == "nearest" else Image.Resampling.LANCZOS
        mode = resize["mode"]
        if mode == "contain":
            scale = min(target_width / source_width, target_height / source_height)
            if not resize["allow_upscale"]:
                scale = min(scale, 1.0)
            scaled_width = max(1, min(target_width, round(source_width * scale)))
            scaled_height = max(1, min(target_height, round(source_height * scale)))
            scaled = stack.enter_context(image.resize((scaled_width, scaled_height), resample))
            output = stack.enter_context(Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0)))
            left, top = (target_width - scaled_width) // 2, (target_height - scaled_height) // 2
            output.paste(scaled, (left, top))
            operation["content_size"] = _size(scaled_width, scaled_height)
            operation["padding"] = {
                "left": left, "top": top,
                "right": target_width - scaled_width - left,
                "bottom": target_height - scaled_height - top,
                "color": "transparent",
            }
        elif mode == "cover":
            scale = max(target_width / source_width, target_height / source_height)
            if scale > 1 and not resize["allow_upscale"]:
                raise ValueError("cover would upscale the raster; set allow_upscale=true explicitly")
            # Crop in source coordinates while resampling. Resizing the entire
            # image first could allocate a huge intermediate for thin images.
            crop_width = min(source_width, target_width / scale)
            crop_height = min(source_height, target_height / scale)
            left, top = (source_width - crop_width) / 2, (source_height - crop_height) / 2
            box = (left, top, min(source_width, left + crop_width), min(source_height, top + crop_height))
            output = stack.enter_context(image.resize((target_width, target_height), resample, box=box))
            operation["content_size"] = _size(target_width, target_height)
            operation["crop"] = dict(zip(("left", "top", "right", "bottom"), box))
            operation["crop_space"] = "source_pixels"
            operation["scale"] = scale
        else:
            if not resize["allow_upscale"] and (target_width > source_width or target_height > source_height):
                raise ValueError("stretch would upscale the raster; set allow_upscale=true explicitly")
            output = stack.enter_context(image.resize((target_width, target_height), resample))
            operation["content_size"] = _size(target_width, target_height)
            operation["aspect_ratio_preserved"] = False
        operations.append(operation)
        output_format = resize.get("output_format", source_format)
        has_transparency = output.getchannel("A").getextrema()[0] < 255
        if _RASTER_FORMATS[output_format] == "JPEG" and has_transparency:
            if "output_format" in resize:
                raise ValueError("JPEG output cannot retain transparency; choose png or webp")
            output_format = "png"
        if _RASTER_FORMATS[output_format] != _RASTER_FORMATS[source_format]:
            operations.append({
                "operation": "format_conversion",
                "before": {"format": source_format}, "after": {"format": output_format},
                "reason": "transparent_padding" if "output_format" not in resize else "explicit_output_format",
            })
        output_stream = io.BytesIO()
        save_options = {}
        if _RASTER_FORMATS[output_format] == "JPEG":
            output = stack.enter_context(output.convert("RGB"))
            save_options.update(quality=95, subsampling=0)
        elif output_format == "webp":
            save_options["lossless"] = True
        output.save(output_stream, format=_RASTER_FORMATS[output_format], **save_options)
        return {
            "data": output_stream.getvalue(), "format": output_format,
            "width": target_width, "height": target_height, "operations": operations,
        }


def transform_asset(
    source: Union[Path, str], *, asset_type: str, source_format: str,
    resize: dict, animations=None,
) -> dict:
    """Return resized bytes and operation metadata without changing the source.

    Only single-frame icons/sprites are supported. Target dimensions are exact;
    contain pads transparently, cover crops centrally, and stretch can distort.
    Vector SVGs remain SVGs and can scale independently of ``allow_upscale``.
    """
    normalized = normalize_resize(resize)
    if asset_type not in ("icon", "sprite"):
        raise ValueError("Resize supports only single-frame icon and sprite assets")
    if animations:
        raise ValueError("Assets with animations cannot be resized")
    if not isinstance(source_format, str):
        raise ValueError("source_format must be svg, png, jpg, jpeg, or webp")
    source_format = source_format.lower()
    if source_format not in {"svg", "png", "jpg", "jpeg", "webp"}:
        raise ValueError("Unsupported resize source_format: {}".format(source_format))
    try:
        data = _read_source(source)
        if source_format == "svg":
            return _transform_svg(data, normalized)
        return _transform_raster(data, source_format, normalized)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Asset resize failed: {}".format(exc)) from exc
