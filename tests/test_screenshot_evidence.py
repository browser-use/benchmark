"""Image evidence keeps chronology and remains usable within request budgets."""

import base64
from io import BytesIO
import random
import struct
import unittest
import zlib

from PIL import Image
from screenshot_evidence import prepare_screenshots


def encode(image, image_format="PNG", **options):
    output = BytesIO()
    image.save(output, format=image_format, **options)
    return base64.b64encode(output.getvalue()).decode()


def noise():
    return Image.frombytes(
        "RGB", (512, 256), random.Random(42).randbytes(512 * 256 * 3)
    )


class ScreenshotEvidenceTests(unittest.TestCase):
    def test_capture_endpoints_and_return_to_previous_state(self):
        a = encode(Image.new("RGB", (20, 10), "red"))
        b = encode(Image.new("RGB", (20, 10), "blue"))
        images, meta = prepare_screenshots([a, a, b, b, a, a], 50, 8000000)
        self.assertEqual(images, [a, b, a])
        self.assertEqual(meta["source_indices"], [0, 2, 5])
        self.assertEqual(
            prepare_screenshots([a, a, a], 50, 8000000)[1]["source_indices"], [0, 2]
        )
        self.assertEqual(
            prepare_screenshots([a, b], 1, 8000000)[1]["source_indices"], [1]
        )

    def test_compression_preserves_resolution_when_it_fits(self):
        source = noise()
        original = encode(source)
        jpeg_size = len(encode(source, "JPEG", quality=90, subsampling=0))
        png_size = min(
            len(original), len(encode(source.convert("RGBA"), optimize=True))
        )
        self.assertLess(jpeg_size, png_size)
        budget = jpeg_size + png_size  # Two frames, with margin above JPEG size.
        images, meta = prepare_screenshots([original, original], 50, budget)
        self.assertEqual(len(images), 2)
        self.assertEqual(meta["source_indices"], [0, 1])
        self.assertLessEqual(meta["encoded_bytes"], budget)
        self.assertEqual(meta["mime_types"], ["image/jpeg", "image/jpeg"])
        for image in images:
            with Image.open(BytesIO(base64.b64decode(image))) as decoded:
                self.assertEqual(decoded.size, (512, 256))
                self.assertEqual(decoded.format, "JPEG")
        self.assertFalse(meta["resized"])

    def test_oversized_boundaries_are_resized_not_rejected(self):
        source = noise()
        original = encode(source)
        encoded_sizes = [
            len(original),
            len(encode(source.convert("RGBA"), optimize=True)),
            *(len(encode(source, "JPEG", quality=q, subsampling=0)) for q in (90, 80)),
        ]
        budget = (
            min(encoded_sizes) // 2
        )  # Each frame gets at most a quarter of full size.
        images, meta = prepare_screenshots([original, original], 50, budget)
        self.assertEqual(len(images), 2)
        self.assertLessEqual(meta["encoded_bytes"], budget)
        self.assertFalse(meta["omitted"])
        self.assertEqual(len(meta["resized"]), 2)
        for image in images:
            with Image.open(BytesIO(base64.b64decode(image))) as decoded:
                self.assertLess(decoded.width, 512)
                self.assertAlmostEqual(decoded.width / decoded.height, 2, delta=0.1)

    def test_exact_budget_preserves_original_modes(self):
        for mode in ("RGB", "RGBA", "P", "L", "1"):
            with self.subTest(mode=mode):
                original = encode(Image.new(mode, (10, 10)))
                images, meta = prepare_screenshots([original], 50, len(original))
                self.assertEqual(images, [original])
                self.assertFalse(meta["resized"])

    def test_transparent_palette_remains_transparent_when_resized(self):
        image = Image.frombytes("P", (100, 100), random.Random(17).randbytes(10000))
        image.info["transparency"] = 0
        original = encode(image)
        budget = (
            min(len(original), len(encode(image.convert("RGBA"), optimize=True))) // 2
        )
        images, meta = prepare_screenshots([original], 50, budget)
        self.assertEqual(len(images), 1)
        self.assertEqual(meta["mime_types"], ["image/png"])
        self.assertLessEqual(meta["encoded_bytes"], budget)
        self.assertEqual(len(meta["resized"]), 1)
        with Image.open(BytesIO(base64.b64decode(images[0]))) as decoded:
            self.assertLess(decoded.convert("RGBA").getextrema()[3][0], 255)

    def test_corrupt_images_are_reported_without_rejecting_remaining_evidence(self):
        valid = encode(Image.new("RGB", (1, 1), "red"))
        raw = base64.b64decode(valid)
        kind, data = b"zTXt", b"key\x00\x01bad"
        chunk = (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )
        corrupt = base64.b64encode(raw[:-12] + chunk + raw[-12:]).decode()
        images, meta = prepare_screenshots(
            [valid, corrupt, "not base64", None], 50, 8000000
        )
        self.assertEqual(images, [valid])
        self.assertEqual(len(meta["omitted"]), 3)
        self.assertIn("unreadable", " ".join(meta["notes"]))

    def test_unusable_budget_and_empty_input_produce_evidence_notes(self):
        image = encode(Image.new("RGB", (10, 10)))
        for budget in (-1, 0, 1):
            images, meta = prepare_screenshots([image], 50, budget)
            self.assertEqual(images, [])
            self.assertEqual(len(meta["omitted"]), 1)
        self.assertEqual(prepare_screenshots([], 50, 8000000)[0], [])
        self.assertEqual(prepare_screenshots([image], 0, 8000000)[0], [])
