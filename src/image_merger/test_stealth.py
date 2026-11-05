"""Tests for the stealth (v2) format.

Run from the folder that contains the `image_merger` package:

    python -m unittest image_merger.test_stealth -v
"""

import io
import os
import struct
import tempfile
import unittest

import numpy as np
from PIL import Image

from image_merger import format as fmt
from image_merger import png_container, stego
from image_merger.format import ImageMergerError
from image_merger.image_pipeline import (CapacityError, create_merged_png, estimate_fit,
                                         extract_image)
from image_merger.shuffle import shuffle_bytes


def photo(width, height, seed, mode="RGB"):
    """Smooth colour field plus fine noise: a rough stand-in for a photo."""
    rng = np.random.default_rng(seed)
    base = rng.normal(128, 60, (height // 8 + 2, width // 8 + 2, 3))
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).resize((width, height), Image.BICUBIC)
    arr = np.clip(np.asarray(img) + rng.normal(0, 6, (height, width, 3)), 0, 255).astype(np.uint8)
    out = Image.fromarray(arr)
    if mode == "L":
        return out.convert("L")
    if mode == "RGBA":
        alpha = np.full((height, width), 255, np.uint8)
        alpha[: height // 4] = 128           # a translucent band
        alpha[-5:] = 0
        return Image.fromarray(np.dstack([arr, alpha]))
    return out


def png_chunk_types(data):
    pos, types = 8, []
    while pos < len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        types.append(data[pos + 4:pos + 8])
        pos += 12 + length
    return types


class StealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = cls.tmp.name
        cls.carrier = cls.p("carrier.png")
        photo(1400, 1000, 1).save(cls.carrier)
        cls.a = cls.p("a.png")
        photo(300, 200, 2).save(cls.a)
        cls.b = cls.p("b.png")
        photo(200, 300, 3).save(cls.b)
        cls.merged = cls.p("merged.png")
        cls.report = create_merged_png(cls.a, "123456", cls.b, "7654321", cls.carrier, cls.merged)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def p(cls, name):
        return os.path.join(cls.dir, name)

    # ---- round trips -----------------------------------------------------

    def test_two_images_round_trip(self):
        ra = extract_image(self.merged, "123456")
        rb = extract_image(self.merged, "7654321")
        self.assertEqual((ra.matched_index, ra.image.size), (0, (300, 200)))
        self.assertEqual((rb.matched_index, rb.image.size), (1, (200, 300)))
        # Lossy WebP: close to the original, not identical.
        orig = np.asarray(Image.open(self.a).convert("RGB")).astype(int)
        diff = np.abs(np.asarray(ra.image.convert("RGB")).astype(int) - orig).mean()
        self.assertLess(diff, 12)

    def test_single_image(self):
        out = self.p("single.png")
        create_merged_png(self.a, "4242", None, None, self.carrier, out)
        self.assertEqual(extract_image(out, "4242").matched_index, 0)
        self.assertIsNone(extract_image(out, "4243").matched_index)

    def test_lossless_is_exact(self):
        small = self.p("small.png")
        photo(100, 80, 9).save(small)
        out = self.p("lossless.png")
        create_merged_png(small, "111222", None, None, self.carrier, out, quality=100, auto_fit=False)
        got = extract_image(out, "111222").image.convert("RGB")
        self.assertTrue(np.array_equal(np.asarray(got), np.asarray(Image.open(small).convert("RGB"))))

    def test_leading_zeros_ignored_on_extract(self):
        self.assertEqual(extract_image(self.merged, "0123456").matched_index, 0)

    # ---- wrong code / no data -------------------------------------------

    def test_wrong_code_gives_deterministic_decoy(self):
        r1 = extract_image(self.merged, "555555")
        r2 = extract_image(self.merged, "555555")
        r3 = extract_image(self.merged, "555556")
        self.assertIsNone(r1.matched_index)
        self.assertEqual(r1.image.tobytes(), r2.image.tobytes())
        self.assertNotEqual(r1.image.tobytes(), r3.image.tobytes())

    def test_plain_png_looks_like_wrong_code(self):
        r = extract_image(self.carrier, "123456")
        self.assertIsNone(r.matched_index)
        self.assertEqual(r.image.mode, "RGB")

    # ---- stealth properties ---------------------------------------------

    def test_no_extra_chunks_or_markers(self):
        data = open(self.merged, "rb").read()
        self.assertTrue(set(png_chunk_types(data)) <= {b"IHDR", b"IDAT", b"IEND", b"iCCP"})
        self.assertNotIn(b"IMRG", data)
        self.assertNotIn(fmt.CHUNK_TYPE, data)
        self.assertIsNone(png_container.find_chunk(data, fmt.CHUNK_TYPE))

    def test_top_six_bits_never_change(self):
        before = np.asarray(Image.open(self.carrier).convert("RGB")).astype(int)
        after = np.asarray(Image.open(self.merged).convert("RGB")).astype(int)
        self.assertTrue(np.array_equal(before >> 2, after >> 2))
        self.assertLessEqual(np.abs(before - after).max(), 1 + 2)   # stays inside a group of 4
        changed = (before != after).mean()
        self.assertGreater(changed, 0)
        self.assertLess(changed, self.report.fill)                  # about half the bits flip

    def test_eligible_set_unchanged_by_embedding(self):
        orig = stego.carrier_from_image(Image.open(self.carrier))
        merged = stego.carrier_from_png_bytes(open(self.merged, "rb").read())
        self.assertTrue(np.array_equal(orig.eligible, merged.eligible))

    def test_path_prefix_property(self):
        seed = bytes(range(32))
        long_path = stego.path_positions(seed, 100_000, 5_000)
        short_path = stego.path_positions(seed, 100_000, 352)
        self.assertTrue(np.array_equal(long_path[:352], short_path))
        self.assertEqual(len(set(long_path.tolist())), 5_000)

    # ---- carrier modes ----------------------------------------------------

    def test_rgba_alpha_untouched(self):
        carrier = self.p("rgba.png")
        photo(900, 700, 5, "RGBA").save(carrier)
        out = self.p("rgba_out.png")
        create_merged_png(self.a, "135791", None, None, carrier, out)
        before = np.asarray(Image.open(carrier))
        after = np.asarray(Image.open(out))
        self.assertEqual(Image.open(out).mode, "RGBA")
        self.assertTrue(np.array_equal(before[..., 3], after[..., 3]))
        self.assertEqual(extract_image(out, "135791").matched_index, 0)

    def test_grayscale_stays_grayscale(self):
        carrier = self.p("gray.png")
        photo(1000, 800, 6, "L").save(carrier)
        out = self.p("gray_out.png")
        create_merged_png(self.a, "246802", None, None, carrier, out)
        self.assertEqual(Image.open(out).mode, "L")
        self.assertEqual(extract_image(out, "246802").matched_index, 0)

    def test_gray_stored_as_rgb_becomes_gray(self):
        carrier = self.p("gray_rgb.png")
        photo(1000, 800, 6, "L").convert("RGB").save(carrier)
        out = self.p("gray_rgb_out.png")
        create_merged_png(self.a, "246802", None, None, carrier, out)
        self.assertEqual(Image.open(out).mode, "L")
        self.assertEqual(extract_image(out, "246802").matched_index, 0)

    def test_max_carrier_megapixels_scales_down(self):
        out = self.p("scaled.png")
        create_merged_png(self.a, "808080", None, None, self.carrier, out, max_carrier_mp=0.5)
        w, h = Image.open(out).size
        self.assertLessEqual(w * h, 500_000)
        self.assertEqual(extract_image(out, "808080").matched_index, 0)

    # ---- capacity ---------------------------------------------------------

    def test_small_carrier_is_rejected(self):
        tiny = self.p("tiny.png")
        photo(120, 90, 7).save(tiny)
        with self.assertRaises(CapacityError):
            create_merged_png(self.a, "123456", None, None, tiny, self.p("tiny_out.png"), auto_fit=False,
                              quality=100)
        self.assertFalse(os.path.exists(self.p("tiny_out.png")))

    def test_slot_boundary(self):
        carrier = stego.carrier_from_image(Image.open(self.carrier))
        limit_bytes = stego.max_slot_bits(carrier.eligible_count, 0) // 8
        seed = bytes(32)
        at_limit = os.urandom(limit_bytes)
        stego.embed_slot(carrier, 0, at_limit, seed)                       # exactly at the limit
        with self.assertRaises(CapacityError):
            stego.embed_slot(carrier, 0, os.urandom(limit_bytes + 1), seed)  # one byte over

    def test_auto_fit_lowers_quality_and_fill(self):
        big = self.p("bigger.png")
        photo(200, 130, 8).save(big)
        loose = estimate_fit(big, None, self.carrier, quality=100, auto_fit=False)
        tuned = estimate_fit(big, None, self.carrier, quality=100, auto_fit=True)
        self.assertGreater(loose.fill, tuned.fill)
        self.assertLess(tuned.quality, 100)
        self.assertLessEqual(tuned.fill, stego.TARGET_FILL + 1e-9)

    # ---- validation and old files ----------------------------------------

    def test_code_validation(self):
        with self.assertRaises(ImageMergerError):
            create_merged_png(self.a, "007", self.b, "7", self.carrier, self.p("x.png"))
        with self.assertRaises(ImageMergerError):
            create_merged_png(self.a, "12", None, None, self.carrier, self.p("x.png"))
        with self.assertRaises(ImageMergerError):
            create_merged_png(self.a, "123456", self.b, "", self.carrier, self.p("x.png"))

    def test_legacy_v1_files_still_extract(self):
        code = 987654
        img = Image.open(self.a).convert("RGB")
        salt = os.urandom(fmt.SALT_LEN)
        entry = fmt.PayloadEntry(img.width, img.height, fmt.MODE_RGB, salt,
                                 fmt.make_verifier(code, salt, fmt.KDF_ITERATIONS),
                                 shuffle_bytes(img.tobytes(), code))
        buf = io.BytesIO()
        Image.open(self.carrier).convert("RGB").save(buf, format="PNG")
        legacy = png_container.embed_chunk(buf.getvalue(), fmt.CHUNK_TYPE,
                                           fmt.pack(fmt.Container(entries=[entry])))
        path = self.p("legacy.png")
        open(path, "wb").write(legacy)
        result = extract_image(path, str(code))
        self.assertEqual(result.matched_index, 0)
        self.assertTrue(np.array_equal(np.asarray(result.image), np.asarray(img)))


if __name__ == "__main__":
    unittest.main()
