import io
import os
import tempfile
from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image, ImageOps

from image_merger import format as fmt
from image_merger import png_container
from image_merger.format import ImageMergerError, PayloadEntry
from image_merger.shuffle import CodeError, parse_code, shuffle_bytes, unshuffle_bytes

# Which hidden image is returned (scrambled) when the code matches neither.
FALLBACK_ENTRY_INDEX = 0


@dataclass
class ExtractResult:
    image: Image.Image
    # Index of the entry the code matched, or None for a wrong code.
    # For tests/CLI only - the GUI must NOT show this, so a wrong code
    # looks exactly like a right one.
    matched_index: Optional[int]


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


def load_source_image(path: str) -> Tuple[Image.Image, int]:

    img = _open_image(path)
    mode_id = fmt.MODE_RGBA if _has_alpha(img) else fmt.MODE_RGB

    width, height = img.size
    if width * height * fmt.MODE_CHANNELS[mode_id] > fmt.MAX_PAYLOAD_BYTES:
        raise ImageMergerError(
            f"'{os.path.basename(path)}' is too large ({width}x{height}). "
            f"The limit is about {fmt.MAX_PAYLOAD_BYTES // (1024 * 1024)} MB of raw pixel data "
            f"per hidden image (roughly 21 megapixels)."
        )

    try:
        img = ImageOps.exif_transpose(img)
        img = img.convert(fmt.MODE_TO_PIL[mode_id])
    except (OSError, ValueError) as e:
        raise ImageMergerError(f"Could not read '{os.path.basename(path)}': {e}") from e
    return img, mode_id


def carrier_to_png_bytes(path: str) -> bytes:
    """Load the carrier image and re-encode it as a clean PNG."""
    img = _open_image(path)
    icc = img.info.get("icc_profile")
    has_alpha = _has_alpha(img)
    try:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGBA" if has_alpha else "RGB")
        buf = io.BytesIO()
        save_kwargs = {"icc_profile": icc} if icc else {}
        img.save(buf, format="PNG", **save_kwargs)
    except (OSError, ValueError) as e:
        raise ImageMergerError(f"Could not read carrier '{os.path.basename(path)}': {e}") from e
    return buf.getvalue()


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

def validate_merge_codes(code_a: str, code_b: str) -> Tuple[int, int]:
    """Parse both codes and make sure they are different."""
    a = parse_code(code_a)
    b = parse_code(code_b)
    if a == b:
        raise CodeError(
            "The two codes must be different. (Leading zeros don't count: "
            "007 and 7 are the same code.)"
        )
    return a, b


def _prepare_entry(path: str, code: int) -> PayloadEntry:
    img, mode_id = load_source_image(path)
    salt = os.urandom(fmt.SALT_LEN)
    return PayloadEntry(
        width=img.width,
        height=img.height,
        mode=mode_id,
        salt=salt,
        verifier=fmt.make_verifier(code, salt, fmt.KDF_ITERATIONS),
        data=shuffle_bytes(img.tobytes(), code),
    )


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


def create_merged_png(image_a: str, code_a: str, image_b: str, code_b: str,
                      carrier: str, output_path: str) -> str:
  
    a, b = validate_merge_codes(code_a, code_b)

    entries = [_prepare_entry(image_a, a), _prepare_entry(image_b, b)]
    carrier_png = carrier_to_png_bytes(carrier)

    blob = fmt.pack(fmt.Container(entries=entries))
    merged = png_container.embed_chunk(carrier_png, fmt.CHUNK_TYPE, blob)

    try:
        _atomic_write(output_path, merged)
    except OSError as e:
        raise ImageMergerError(f"Could not save the merged file: {e}") from e

    print(f"[OK] merged -> '{output_path}' ({len(merged)} bytes)")
    return output_path



def extract_image(merged_path: str, code: str) -> ExtractResult:

    code_int = parse_code(code, enforce_length=False)

    try:
        with open(merged_path, "rb") as f:
            png = f.read()
    except OSError as e:
        raise ImageMergerError(f"Could not open the file: {e}") from e

    blob = png_container.find_chunk(png, fmt.CHUNK_TYPE)
    if blob is None:
        raise ImageMergerError(
            "This PNG doesn't contain hidden image data from this app. It may "
            "have been re-saved or recompressed, which removes the hidden data."
        )
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
