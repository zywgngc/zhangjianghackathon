import builtins
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from asset_agent.transforms import normalize_resize, transform_asset

try:
    from PIL import Image
except ImportError:
    Image = None


class ResizeContractTests(unittest.TestCase):
    def test_defaults_are_explicit_and_input_is_not_changed(self):
        request = {"width": 48, "height": 96}
        result = normalize_resize(request)
        self.assertEqual(result, {
            "width": 48, "height": 96, "mode": "contain",
            "resample": "lanczos", "allow_upscale": False,
        })
        self.assertEqual(request, {"width": 48, "height": 96})

    def test_unknown_invalid_and_oversized_instructions_are_rejected(self):
        invalid = [
            None, [], {}, {"width": True, "height": 2}, {"width": 1.0, "height": 2},
            {"width": 0, "height": 2}, {"width": 8193, "height": 1},
            {"width": 4096, "height": 4096}, {"width": 8, "height": 8, "mode": "fit"},
            {"width": 8, "height": 8, "resample": "bicubic"},
            {"width": 8, "height": 8, "allow_upscale": 1},
            {"width": 8, "height": 8, "output_format": "svg"},
            {"width": 8, "height": 8, "rotate": 90},
        ]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                normalize_resize(request)
        self.assertEqual(normalize_resize({"width": 4000, "height": 4000})["width"], 4000)


class SvgTransformTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "asset.svg"
        self.original = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="20" viewBox="-5 -6 10 20"><rect x="-5" y="-6" width="10" height="20"/></svg>'
        self.path.write_bytes(self.original)

    def transform(self, **options):
        return transform_asset(self.path, asset_type="icon", source_format="svg", resize={"width": 60, "height": 60, **options})

    def test_viewbox_is_preserved_and_vector_upscale_is_allowed(self):
        checksum = hashlib.sha256(self.original).hexdigest()
        for mode, aspect in [("contain", "xMidYMid meet"), ("cover", "xMidYMid slice"), ("stretch", "none")]:
            with self.subTest(mode=mode):
                result = self.transform(mode=mode)
                root = ET.fromstring(result["data"])
                self.assertEqual(root.get("viewBox"), "-5 -6 10 20")
                self.assertEqual(root.get("width"), "60")
                self.assertEqual(root.get("height"), "60")
                self.assertEqual(root.get("preserveAspectRatio"), aspect)
                self.assertEqual(result["format"], "svg")
                self.assertEqual(result["operations"][0]["operation"], "resize")
                self.assertTrue(result["operations"][0]["vector_scalable"])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), checksum)

    def test_missing_viewbox_is_created_from_absolute_source_dimensions(self):
        self.path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="24px" height="12"/>', encoding="utf-8")
        root = ET.fromstring(self.transform()["data"])
        self.assertEqual(root.get("viewBox"), "0 0 24 12")

    def test_svg_raster_conversion_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "SVG-to-raster"):
            self.transform(output_format="png")

    def test_external_resources_scripts_dtds_and_css_imports_are_rejected(self):
        invalid = [
            '<svg width="10" height="10"><script>alert(1)</script></svg>',
            '<svg width="10" height="10" onload="alert(1)"/>',
            '<svg width="10" height="10"><use href="https://example.test/image.svg#x"/></svg>',
            '<svg width="10" height="10"><image href="other.png"/></svg>',
            '<svg width="10" height="10"><path fill="url(https://example.test/fill.svg)"/></svg>',
            '<svg width="10" height="10"><style>@import "https://example.test/style.css";</style></svg>',
            '<svg width="10" height="10"><path style="fill:u\\72l(https://example.test/fill)"/></svg>',
            '<svg width="10" height="10"><path fill="u\\72l(https://example.test/fill)"/></svg>',
            '<svg width="10" height="10"><foreignObject><div/></foreignObject></svg>',
            '<!DOCTYPE svg SYSTEM "https://example.test/evil.dtd"><svg width="10" height="10"/>',
            '<!DOCTYPE svg [<!ENTITY a "abc">]><svg width="10" height="10">&a;</svg>',
            '<?xml-stylesheet href="https://example.test/style.css"?><svg width="10" height="10"/>',
            '<svg xml:base="https://example.test/" width="10" height="10"><use href="#x"/></svg>',
        ]
        for text in invalid:
            with self.subTest(svg=text):
                self.path.write_text(text, encoding="utf-8")
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    self.transform()
                self.assertEqual(self.path.read_bytes(), before)

    def test_animated_svg_cannot_mutate_a_safe_reference_to_an_external_one(self):
        self.path.write_text('<svg width="10" height="10"><use href="#x"><set attributeName="href" to="https://example.test/image.svg#x"/></use></svg>', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Animated SVG"):
            self.transform()

    def test_embedded_gif_apng_webp_and_other_raster_data_are_rejected(self):
        for mime_type in ("gif", "png", "webp", "jpeg"):
            with self.subTest(mime_type=mime_type):
                self.path.write_text(
                    '<svg width="10" height="10"><image href="data:image/{};base64,AAAA"/></svg>'.format(mime_type),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "embedded images are unsupported"):
                    self.transform()

    def test_local_use_fragment_is_still_supported(self):
        self.path.write_text('<svg width="10" height="10"><defs><path id="shape" d="M0 0h1v1z"/></defs><use href="#shape"/></svg>', encoding="utf-8")
        result = self.transform()
        self.assertIn(b'href="#shape"', result["data"])

    def test_root_inline_css_cannot_override_target_dimensions(self):
        declarations = [
            "width:512px;height:512px", "WIDTH : 512px", "height /* comment */ : 512px",
            "wi/**/dth:512px", "min-width:512px", "max-height:512px",
            "INLINE-SIZE:512px", "max-block-size:512px", "aspect-ratio:1",
            "all:initial", "padding:100px", "border-width:100px", "box-sizing:border-box",
            "-webkit-min-logical-width:512px", "-moz-box-sizing:border-box",
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                self.path.write_text('<svg width="10" height="20" style="{}"/>'.format(declaration), encoding="utf-8")
                before = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, "CSS geometry"):
                    self.transform(width=48, height=48)
                self.assertEqual(self.path.read_bytes(), before)

    def test_stylesheet_geometry_is_rejected_without_guessing_selectors(self):
        styles = [
            "svg { width:512px; height:512px; }",
            "SVG { /* root rule */ MiN-HEIGHT /* comment */ : 512px !important; }",
            ":root { max-inline-size:512px }",
            "rect { width:12px }",  # Conservative supported subset.
            "@media (min-width:1px) { svg { height:512px } }",
        ]
        for stylesheet in styles:
            with self.subTest(stylesheet=stylesheet):
                self.path.write_text('<svg width="10" height="20"><style>{}</style></svg>'.format(stylesheet), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "CSS geometry"):
                    self.transform()

    def test_non_geometry_paint_styles_remain_supported(self):
        self.path.write_text(
            '<svg width="10" height="20" style="fill:red;stroke:blue"><style>rect { fill: green; stroke-width: 2; }</style><rect width="10" height="20"/></svg>',
            encoding="utf-8",
        )
        root = ET.fromstring(self.transform(width=48, height=48)["data"])
        self.assertEqual((root.get("width"), root.get("height")), ("48", "48"))
        self.assertEqual(root.get("style"), "fill:red;stroke:blue")

    def test_internal_paint_references_are_preserved(self):
        self.path.write_text('<svg width="10" height="10"><defs><linearGradient id="g"/></defs><rect fill="url(#g)"/></svg>', encoding="utf-8")
        self.assertIn(b"url(#g)", self.transform()["data"])

    def test_invalid_source_geometry_and_xml_are_rejected(self):
        for text in [
            '<svg viewBox="0 0 nan 10"/>', '<svg viewBox="0 0 -1 10"/>',
            '<svg width="100%" height="100%"/>', '<html/>', '<svg',
        ]:
            with self.subTest(svg=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.transform()

    def test_source_byte_limit_is_enforced_before_parsing(self):
        with patch("asset_agent.transforms.MAX_SOURCE_BYTES", 8):
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                self.transform()

    def test_unsupported_types_formats_and_animation_metadata_are_rejected(self):
        for asset_type in ("tileset", "model", "audio", "font"):
            with self.subTest(asset_type=asset_type), self.assertRaises(ValueError):
                transform_asset(self.path, asset_type=asset_type, source_format="svg", resize={"width": 4, "height": 4})
        with self.assertRaisesRegex(ValueError, "animations"):
            transform_asset(self.path, asset_type="sprite", source_format="svg", resize={"width": 4, "height": 4}, animations=["walk"])
        with self.assertRaisesRegex(ValueError, "source_format"):
            transform_asset(self.path, asset_type="icon", source_format="gif", resize={"width": 4, "height": 4})

    def test_svg_does_not_import_pillow_and_raster_reports_missing_extra(self):
        original_import = builtins.__import__

        def without_pillow(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("Pillow unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_pillow):
            self.assertEqual(self.transform()["format"], "svg")
            with self.assertRaisesRegex(ValueError, r"game-asset-agent\[images\]"):
                transform_asset(self.path, asset_type="icon", source_format="png", resize={"width": 4, "height": 4})


@unittest.skipIf(Image is None, "Raster resize tests require the optional Pillow extra")
class RasterTransformTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "image.png"
        with Image.new("RGBA", (4, 2), (255, 0, 0, 255)) as image:
            image.save(self.path, format="PNG")

    def transform(self, *, source_format="png", **options):
        original_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = transform_asset(self.path, asset_type="sprite", source_format=source_format,
                                 resize={"width": 6, "height": 6, "resample": "nearest", **options})
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), original_hash)
        return result

    def decode(self, result):
        image = Image.open(io.BytesIO(result["data"]))
        self.addCleanup(image.close)
        image.load()
        return image

    def test_contain_keeps_resolution_and_centers_transparent_padding(self):
        result = self.transform()
        image = self.decode(result)
        self.assertEqual(image.size, (6, 6))
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(image.getpixel((1, 2)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((4, 3)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((5, 4)), (0, 0, 0, 0))
        operation = result["operations"][0]
        self.assertEqual(operation["before"], {"width": 4, "height": 2})
        self.assertEqual(operation["after"], {"width": 6, "height": 6})
        self.assertEqual(operation["content_size"], {"width": 4, "height": 2})
        self.assertEqual(operation["resample"], "nearest")

    def test_contain_upscale_requires_explicit_permission_and_retains_alpha(self):
        with Image.new("RGBA", (4, 2), (20, 40, 60, 80)) as image:
            image.save(self.path, format="PNG")
        result = self.transform(width=8, height=8, allow_upscale=True)
        output = self.decode(result)
        self.assertEqual(output.getpixel((0, 2)), (20, 40, 60, 80))
        self.assertEqual(output.getpixel((7, 5)), (20, 40, 60, 80))
        self.assertEqual(output.getpixel((0, 1)), (0, 0, 0, 0))

    def test_cover_crops_the_center_at_exact_target_size(self):
        with Image.new("RGBA", (6, 2)) as image:
            for x, color in enumerate([(255, 0, 0, 255)] * 2 + [(0, 255, 0, 100)] * 2 + [(0, 0, 255, 255)] * 2):
                for y in range(2):
                    image.putpixel((x, y), color)
            image.save(self.path, format="PNG")
        result = self.transform(width=2, height=2, mode="cover")
        output = self.decode(result)
        self.assertEqual(output.size, (2, 2))
        self.assertEqual(set(output.getdata()), {(0, 255, 0, 100)})
        self.assertEqual(result["operations"][0]["crop"], {"left": 2, "top": 0, "right": 4, "bottom": 2})

    def test_cover_and_stretch_refuse_implicit_upscale(self):
        for mode in ("cover", "stretch"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "allow_upscale"):
                self.transform(mode=mode)
        self.assertEqual(self.decode(self.transform(mode="cover", allow_upscale=True)).size, (6, 6))
        self.assertEqual(self.decode(self.transform(mode="stretch", allow_upscale=True)).size, (6, 6))

    def test_cover_extreme_aspect_ratio_avoids_a_huge_intermediate(self):
        with Image.new("RGBA", (8192, 1), (0, 255, 0, 255)) as image:
            image.save(self.path, format="PNG")
        result = self.transform(width=1, height=8192, mode="cover", allow_upscale=True)
        output = self.decode(result)
        self.assertEqual(output.size, (1, 8192))
        self.assertEqual(output.getpixel((0, 4096)), (0, 255, 0, 255))
        self.assertEqual(result["operations"][0]["crop_space"], "source_pixels")

    def test_stretch_fills_target_and_records_distortion(self):
        result = self.transform(width=2, height=2, mode="stretch")
        output = self.decode(result)
        self.assertEqual(output.size, (2, 2))
        self.assertEqual(output.getpixel((0, 0)), (255, 0, 0, 255))
        self.assertFalse(result["operations"][0]["aspect_ratio_preserved"])

    def test_jpeg_with_transparent_padding_automatically_becomes_png(self):
        with Image.new("RGB", (4, 2), "red") as image:
            image.save(self.path, format="JPEG")
        result = self.transform(source_format="jpg", width=4, height=4)
        output = self.decode(result)
        self.assertEqual(result["format"], "png")
        self.assertEqual(output.getpixel((0, 0))[3], 0)
        self.assertEqual(result["operations"][-1]["operation"], "format_conversion")
        self.assertEqual(result["operations"][-1]["reason"], "transparent_padding")

    def test_jpeg_without_padding_keeps_original_format(self):
        with Image.new("RGB", (4, 2), "red") as image:
            image.save(self.path, format="JPEG")
        result = self.transform(source_format="jpg", width=2, height=1)
        self.assertEqual(result["format"], "jpg")
        self.assertEqual(self.decode(result).format, "JPEG")

    def test_explicit_jpeg_cannot_discard_transparency(self):
        with self.assertRaisesRegex(ValueError, "transparency"):
            self.transform(output_format="jpeg")
        with Image.new("RGBA", (4, 2), (255, 0, 0, 10)) as image:
            image.save(self.path, format="PNG")
        with self.assertRaisesRegex(ValueError, "transparency"):
            self.transform(width=4, height=2, output_format="jpeg")

    def test_explicit_webp_preserves_transparent_pixels(self):
        result = self.transform(output_format="webp")
        self.assertEqual(result["format"], "webp")
        output = self.decode(result).convert("RGBA")
        self.addCleanup(output.close)
        self.assertEqual(output.getpixel((0, 0))[3], 0)
        self.assertEqual(output.getpixel((1, 2)), (255, 0, 0, 255))

    def test_exif_orientation_is_applied_and_recorded(self):
        with Image.new("RGB", (4, 2), "red") as image:
            exif = Image.Exif()
            exif[274] = 6
            image.save(self.path, format="JPEG", exif=exif)
        result = self.transform(source_format="jpeg", width=2, height=4)
        self.assertEqual(self.decode(result).size, (2, 4))
        self.assertEqual(result["operations"][0], {
            "operation": "exif_transpose", "orientation": 6,
            "before": {"width": 4, "height": 2}, "after": {"width": 2, "height": 4},
        })
        self.assertIsNone(self.decode(result).getexif().get(274))

    def test_animated_png_is_rejected(self):
        with Image.new("RGBA", (4, 2), "red") as first, Image.new("RGBA", (4, 2), "blue") as second:
            first.save(self.path, format="PNG", save_all=True, append_images=[second], duration=100, loop=0)
        with self.assertRaisesRegex(ValueError, "Animated"):
            self.transform()

    def test_malformed_or_misdeclared_raster_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "source_format"):
            self.transform(source_format="jpeg")
        self.path.write_bytes(b"not an image")
        with self.assertRaisesRegex(ValueError, "resize failed"):
            self.transform()


if __name__ == "__main__":
    unittest.main()
