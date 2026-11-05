import io
import os
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PIL import Image, ImageOps

from image_merger import format as fmt
from image_merger import png_container, stego
from image_merger.decoy import make_decoy
from image_merger.format import ImageMergerError
from image_merger.shuffle import CodeError, parse_code, unshuffle_bytes

# Legacy (v1) files only: which hidden image is returned (scrambled) when the code matches neither.
FALLBACK_ENTRY_INDEX = 0

DEFAULT_QUALITY = 80       # 1-99 = lossy WebP, 100 = lossless
MIN_AUTO_QUALITY = 10      # auto-fit never goes below this
WEBP_MAX_SIDE = 16383

CapacityError = stego.CapacityError


@dataclass
class ExtractResult:
    image: Image.Image
    # Index of the slot the code matched, or None for a wrong code.
    # For tests/CLI only - the GUI must NOT show this, so a wrong code
    # looks exactly like a right one.
    matched_index: Optional[int]


@dataclass
class FitReport:
    fill: float                 # share of the carrier's usable positions that will be used
    quality: int                # WebP quality that will be used (100 = lossless)
    payload_bytes: int          # total encrypted bytes to hide
    capacity_bytes: int         # bytes the carrier holds at the "low risk" fill (10%)
    level: str                  # "low", "medium" or "high"


@dataclass
class MergeReport(FitReport):
    path: str = ""
    size_bytes: int = 0


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _open_image(path: str) -> Image.Image:
    try:
        return Image.open(path)
    except (OSError, Image.DecompressionBombError) as e:
        raise ImageMergerError(f"Could not open '{os.path.basename(path)}' as an image: {e}") from e


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info


def load_source_image(path: str) -> Image.Image:
    """Load a hidden image as RGB or RGBA (rotated upright)."""
    img = _open_image(path)
    mode_id = fmt.MODE_RGBA if _has_alpha(img) else fmt.MODE_RGB

    width, height = img.size
    if width * height * fmt.MODE_CHANNELS[mode_id] > fmt.MAX_PAYLOAD_BYTES:
        raise ImageMergerError(
            f"'{os.path.basename(path)}' is too large ({width}x{height}). "
            f"The limit is about {fmt.MAX_PAYLOAD_BYTES // (1024 * 1024)} MB of raw pixel data "
            f"per hidden image (roughly 21 megapixels)."
        )
    if max(width, height) > WEBP_MAX_SIDE:
        raise ImageMergerError(
            f"'{os.path.basename(path)}' is too large ({width}x{height}): "
            f"hidden images can be at most {WEBP_MAX_SIDE} pixels wide or tall."
        )

    try:
        img = ImageOps.exif_transpose(img)
        return img.convert(fmt.MODE_TO_PIL[mode_id])
    except (OSError, ValueError) as e:
        raise ImageMergerError(f"Could not read '{os.path.basename(path)}': {e}") from e


def _encode_webp(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    try:
        if quality >= 100:
            img.save(buf, format="WEBP", lossless=True, exact=True, quality=75, method=4)
        else:
            img.save(buf, format="WEBP", quality=quality, method=4)
    except (OSError, ValueError) as e:
        raise ImageMergerError(f"Could not compress a hidden image: {e}") from e
    return buf.getvalue()


def _decode_webp(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img.convert("RGBA" if _has_alpha(img) else "RGB")
    except (OSError, ValueError) as e:
        raise ImageMergerError(f"The hidden image data could not be decoded: {e}") from e


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

def validate_merge_codes(code_a: str, code_b: Optional[str] = None) -> Tuple[int, ...]:
    """Parse the required first code and optional second code."""
    a = parse_code(code_a)
    if not code_b:
        return (a,)
    b = parse_code(code_b)
    if a == b:
        raise CodeError(
            "The two codes must be different. (Leading zeros don't count: "
            "007 and 7 are the same code.)"
        )
    return a, b


def _level(fill: float) -> str:
    if fill <= stego.TARGET_FILL:
        return "low"
    if fill <= stego.WARN_FILL:
        return "medium"
    return "high"


def _check_slots(blob_lengths: List[int], eligible_count: int) -> None:
    for slot, length in enumerate(blob_lengths):
        limit = stego.max_slot_bits(eligible_count, slot)
        if length * 8 > limit:
            safe_kb = stego.TARGET_FILL * eligible_count / 8 / 1024
            raise CapacityError(
                "This carrier is too small for the hidden image"
                + ("s" if len(blob_lengths) > 1 else "")
                + f". It holds about {safe_kb:.0f} KB comfortably; lower the hidden-image quality "
                  "or use a larger carrier."
            )


def _fit_payloads(images: List[Image.Image], quality: int, auto_fit: bool,
                  eligible_count: int) -> Tuple[int, List[bytes]]:
    if eligible_count < 2 * stego.HEADER_SEALED_LEN * 8:
        raise CapacityError("This carrier has too little detail to hide anything in. "
                            "Use a larger or more detailed photo as the carrier.")
    cache = {}

    def encode(q: int) -> List[bytes]:
        if q not in cache:
            cache[q] = [_encode_webp(im, q) for im in images]
        return cache[q]

    def fill_of(blobs: List[bytes]) -> float:
        return stego.fill_ratio([stego.sealed_length(len(b)) for b in blobs], eligible_count)

    blobs = encode(quality)
    if auto_fit and fill_of(blobs) > stego.TARGET_FILL and quality > MIN_AUTO_QUALITY:
        lo, hi, best = MIN_AUTO_QUALITY, quality - 1, MIN_AUTO_QUALITY
        while lo <= hi:
            mid = (lo + hi) // 2
            if fill_of(encode(mid)) <= stego.TARGET_FILL:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        quality, blobs = best, encode(best)

    _check_slots([stego.sealed_length(len(b)) for b in blobs], eligible_count)
    return quality, blobs


def _plan(image_a: str, image_b: Optional[str], carrier_path: str, quality: int,
          auto_fit: bool, max_carrier_mp: Optional[float]):
    if not 1 <= quality <= 100:
        raise ImageMergerError("Hidden-image quality must be between 1 and 100.")
    if max_carrier_mp is not None and max_carrier_mp <= 0:
        raise ImageMergerError("The maximum carrier size must be greater than zero.")

    images = [load_source_image(image_a)]
    if image_b:
        images.append(load_source_image(image_b))

    carrier_img = _open_image(carrier_path)
    try:
        carrier = stego.carrier_from_image(carrier_img, transpose=True, max_megapixels=max_carrier_mp)
    finally:
        carrier_img.close()

    quality, blobs = _fit_payloads(images, quality, auto_fit, carrier.eligible_count)
    return carrier, quality, blobs


def _fit_report(carrier: stego.Carrier, quality: int, blobs: List[bytes]) -> FitReport:
    lengths = [stego.sealed_length(len(b)) for b in blobs]
    fill = stego.fill_ratio(lengths, carrier.eligible_count)
    return FitReport(
        fill=fill, quality=quality, payload_bytes=sum(lengths),
        capacity_bytes=int(stego.TARGET_FILL * carrier.eligible_count / 8),
        level=_level(fill),
    )


def estimate_fit(image_a: str, image_b: Optional[str], carrier: str, quality: int = DEFAULT_QUALITY,
                 auto_fit: bool = True, max_carrier_mp: Optional[float] = None) -> FitReport:
    """Dry run: how full would the carrier be? (No codes needed, nothing is written.)"""
    carrier_obj, quality, blobs = _plan(image_a, image_b, carrier, quality, auto_fit, max_carrier_mp)
    return _fit_report(carrier_obj, quality, blobs)


def _atomic_write(path: str, data: bytes) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".merge_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def create_merged_png(image_a: str, code_a: str, image_b: Optional[str], code_b: Optional[str],
                      carrier: str, output_path: str, quality: int = DEFAULT_QUALITY,
                      auto_fit: bool = True, max_carrier_mp: Optional[float] = None) -> MergeReport:
    codes = validate_merge_codes(code_a, code_b)
    if bool(image_b) != bool(code_b):
        raise ImageMergerError(
            "To add Image B, choose both the image and its code. Otherwise leave both empty."
        )

    carrier_obj, quality, blobs = _plan(image_a, image_b, carrier, quality, auto_fit, max_carrier_mp)
    report = _fit_report(carrier_obj, quality, blobs)

    stego.embed_images(carrier_obj, dict(enumerate(codes)), dict(enumerate(blobs)))
    merged = stego.carrier_to_png_bytes(carrier_obj)

    # Self-check on the exact bytes that will be written: both images must read back.
    check = stego.carrier_from_png_bytes(merged)
    for slot, (code, blob) in enumerate(zip(codes, blobs)):
        if stego.read_slot(check, code, slot) != blob:
            raise ImageMergerError(
                "The merged image failed its self-check, so nothing was saved. "
                "Try a different carrier image."
            )

    try:
        _atomic_write(output_path, merged)
    except OSError as e:
        raise ImageMergerError(f"Could not save the merged file: {e}") from e

    return MergeReport(**report.__dict__, path=output_path, size_bytes=len(merged))


# --------------------------------------------------------------------------
# Extract
# --------------------------------------------------------------------------

def _extract_legacy(blob: bytes, code_int: int) -> ExtractResult:
    """Files made by version 0.1 (hidden data in a private PNG chunk)."""
    container = fmt.unpack(blob)

    matched = None
    for i, entry in enumerate(container.entries):
        if fmt.code_matches(code_int, entry, container.kdf_iterations):
            matched = i
            break

    entry = container.entries[matched if matched is not None else FALLBACK_ENTRY_INDEX]
    pixels = unshuffle_bytes(entry.data, code_int)
    img = Image.frombytes(fmt.MODE_TO_PIL[entry.mode], (entry.width, entry.height), pixels)
    return ExtractResult(image=img, matched_index=matched)


def extract_image(merged_path: str, code: str) -> ExtractResult:
    code_int = parse_code(code, enforce_length=False)

    try:
        with open(merged_path, "rb") as f:
            png = f.read()
    except OSError as e:
        raise ImageMergerError(f"Could not open the file: {e}") from e

    legacy_blob = png_container.find_chunk(png, fmt.CHUNK_TYPE)
    if legacy_blob is not None:
        return _extract_legacy(legacy_blob, code_int)

    carrier = stego.carrier_from_png_bytes(png)
    for slot in range(stego.NUM_SLOTS):
        data = stego.read_slot(carrier, code_int, slot)
        if data is not None:
            return ExtractResult(image=_decode_webp(data), matched_index=slot)

    # Wrong code, or no hidden data at all: both look exactly the same on purpose.
    return ExtractResult(image=make_decoy(code_int, carrier.width, carrier.height), matched_index=None)
