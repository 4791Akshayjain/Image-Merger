"""Stealth format v2: encrypted payloads hidden in the carrier's pixel values.

Nothing is stored outside the pixel data: the merged file is a plain PNG with no
extra chunks, no trailing bytes, no magic number and no plaintext header.

Summary of the format (see README for the user-facing description):

* Embedding only ever moves a channel value inside its group of four
  (0-3, 4-7, ...), so the top 6 bits of every value never change. Texture
  scores are computed from those top 6 bits, which makes the set of usable
  positions identical when merging and when extracting.
* The usable set E is the most textured ~75% of the colour values of fully
  opaque pixels (flat areas are skipped). E is split by index parity: even
  entries belong to slot 0, odd entries to slot 1. An unused slot is untouched.
* Each slot's key material comes from scrypt(code, salt derived from the
  carrier size and the slot). 32 bytes are a ChaCha20-Poly1305 key, 32 bytes
  seed a ChaCha20 keystream that picks the order of the bit positions.
* A slot holds: sealed header (44 bytes) + sealed body (28 bytes + image bytes).
  Everything is ciphertext, so the bits are indistinguishable from random.
"""

import hashlib
import io
import os
import struct
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
from PIL import Image, ImageOps
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from image_merger.format import ImageMergerError

# --------------------------------------------------------------------------
# Format constants (changing any of these breaks compatibility with old files)
# --------------------------------------------------------------------------

STEALTH_VERSION = 2
NUM_SLOTS = 2
FORMAT_TAG = b"imrg-stealth-v2"

SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024

NONCE_LEN = 12
TAG_LEN = 16
HEADER_PLAIN_LEN = 16                                   # version, flags, body length, padding
HEADER_SEALED_LEN = NONCE_LEN + HEADER_PLAIN_LEN + TAG_LEN   # 44 bytes
BODY_OVERHEAD = NONCE_LEN + TAG_LEN                     # 28 bytes
_HEADER_STRUCT = struct.Struct(">BBI10x")

TEXTURE_KEEP_NUM, TEXTURE_KEEP_DEN = 3, 4               # most textured 75% of values
MAX_SCORE = 79380                                       # upper bound of the texture score
DRAW_BLOCK = 1 << 20                                    # keystream draws per block
BAND_PIXELS = 4_000_000                                 # working-set size for texture maps

# Fill ratio = payload bits / usable positions (E).
TARGET_FILL = 0.10       # green up to here (auto-fit aims for this)
WARN_FILL = 0.25         # amber up to here, red above
MAX_SLOT_FILL = 0.50     # hard stop: share of one slot's positions that may be used

MAX_CARRIER_PIXELS = 50_000_000


class CapacityError(ImageMergerError):
    """The carrier cannot hold the hidden image(s) safely."""


# --------------------------------------------------------------------------
# Carrier
# --------------------------------------------------------------------------

@dataclass
class Carrier:
    width: int
    height: int
    color: np.ndarray                 # (h, w, 1 or 3) uint8, C-contiguous
    alpha: Optional[np.ndarray]       # (h, w) uint8 or None
    icc_profile: Optional[bytes]
    eligible: np.ndarray              # sorted flat indexes into color.reshape(-1)

    @property
    def eligible_count(self) -> int:
        return int(self.eligible.size)


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info


def _score_band(top_pad: np.ndarray, r0: int, r1: int) -> np.ndarray:
    """9 * sum(t^2) - sum(t)^2 over each 3x3 window for rows r0..r1-1."""
    block = top_pad[r0:r1 + 2].astype(np.int32)

    def box(a):
        horiz = a[:, :-2] + a[:, 1:-1] + a[:, 2:]
        return horiz[:-2] + horiz[1:-1] + horiz[2:]

    s1 = box(block)
    s2 = box(block * block)
    return 9 * s2 - s1 * s1


def _build_eligible(color: np.ndarray, alpha: Optional[np.ndarray]) -> np.ndarray:
    height, width, channels = color.shape
    top = (color >> 2).astype(np.int16)
    padded = [np.pad(top[:, :, c], 1, mode="edge") for c in range(channels)]
    band = max(1, BAND_PIXELS // width)
    index_dtype = np.int32 if height * width * channels < 2 ** 31 else np.int64

    hist = np.zeros(MAX_SCORE + 1, dtype=np.int64)
    for r0 in range(0, height, band):
        r1 = min(height, r0 + band)
        opaque = None if alpha is None else (alpha[r0:r1] == 255)
        for c in range(channels):
            score = _score_band(padded[c], r0, r1)
            valid = score > 0
            if opaque is not None:
                valid &= opaque
            hist += np.bincount(score[valid], minlength=MAX_SCORE + 1)

    total = int(hist.sum())
    if total == 0:
        return np.empty(0, dtype=index_dtype)
    needed = (TEXTURE_KEEP_NUM * total + TEXTURE_KEEP_DEN - 1) // TEXTURE_KEEP_DEN
    at_least = np.cumsum(hist[::-1])[::-1]               # at_least[s] = count(score >= s)
    threshold = int(np.flatnonzero(at_least >= needed)[-1])

    pieces = []
    for r0 in range(0, height, band):
        r1 = min(height, r0 + band)
        opaque = None if alpha is None else (alpha[r0:r1] == 255)
        usable = np.empty((r1 - r0, width, channels), dtype=bool)
        for c in range(channels):
            ok = _score_band(padded[c], r0, r1) >= threshold
            if opaque is not None:
                ok &= opaque
            usable[:, :, c] = ok
        idx = np.flatnonzero(usable)
        idx += r0 * width * channels
        pieces.append(idx.astype(index_dtype))
    return np.concatenate(pieces) if pieces else np.empty(0, dtype=index_dtype)


def carrier_from_image(img: Image.Image, transpose: bool = True,
                       max_megapixels: Optional[float] = None) -> Carrier:
    """Normalise a PIL image to L / LA / RGB / RGBA arrays and find usable positions."""
    try:
        icc = img.info.get("icc_profile")
        width, height = img.size
        if not max_megapixels and width * height > MAX_CARRIER_PIXELS:
            raise ImageMergerError(
                f"The carrier is too large ({width}x{height}). Choose a smaller image or set "
                f"a maximum carrier size (megapixels) so it is scaled down first."
            )
        if img.mode.startswith("I"):                     # 16/32-bit greyscale
            raw = np.asarray(img).astype(np.int64)
            if raw.size and raw.max() > 255:
                raw = raw >> 8
            img = Image.fromarray(raw.clip(0, 255).astype(np.uint8))
        if transpose:
            img = ImageOps.exif_transpose(img)
        has_alpha = _has_alpha(img)
        gray_like = img.mode in ("L", "LA", "1")
        if gray_like:
            img = img.convert("LA" if has_alpha else "L")
        else:
            img = img.convert("RGBA" if has_alpha else "RGB")

        if max_megapixels:
            limit = max_megapixels * 1_000_000
            if img.width * img.height > limit:
                scale = (limit / (img.width * img.height)) ** 0.5
                img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                                 Image.LANCZOS)
        if img.width * img.height > MAX_CARRIER_PIXELS:
            raise ImageMergerError("The carrier is still too large after scaling; lower the "
                                   "maximum carrier size.")

        arr = np.asarray(img)
        if arr.ndim == 2:
            arr = arr[:, :, None]
        channels = arr.shape[2]
        if channels in (3, 4):
            rgb = arr[:, :, :3]
            if np.array_equal(rgb[:, :, 0], rgb[:, :, 1]) and np.array_equal(rgb[:, :, 0], rgb[:, :, 2]):
                # Grey image stored as RGB: keep it grey, otherwise hiding would break R == G == B.
                arr = np.concatenate([rgb[:, :, :1], arr[:, :, 3:]], axis=2)
                channels = arr.shape[2]
        if channels == 1:
            color, alpha = arr[:, :, :1], None
        elif channels == 2:
            color, alpha = arr[:, :, :1], arr[:, :, 1]
        elif channels == 3:
            color, alpha = arr, None
        else:
            color, alpha = arr[:, :, :3], arr[:, :, 3]
        color = np.array(color, order="C")            # writable copy
        alpha = None if alpha is None else np.ascontiguousarray(alpha)
    except ImageMergerError:
        raise
    except (OSError, ValueError, MemoryError, Image.DecompressionBombError) as e:
        if isinstance(e, MemoryError):
            raise
        raise ImageMergerError(f"Could not read the carrier image: {e}") from e

    return Carrier(width=color.shape[1], height=color.shape[0], color=color, alpha=alpha,
                   icc_profile=icc, eligible=_build_eligible(color, alpha))


def carrier_from_png_bytes(data: bytes) -> Carrier:
    """Read a merged PNG for extraction (no rotation or scaling is applied)."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (OSError, Image.DecompressionBombError) as e:
        raise ImageMergerError(f"Could not read this file as an image: {e}") from e
    return carrier_from_image(img, transpose=False, max_megapixels=None)


def carrier_to_png_bytes(carrier: Carrier) -> bytes:
    if carrier.alpha is None:
        arr = carrier.color if carrier.color.shape[2] == 3 else carrier.color[:, :, 0]
    else:
        arr = np.concatenate([carrier.color, carrier.alpha[:, :, None]], axis=2)
    buf = io.BytesIO()
    kwargs = {"icc_profile": carrier.icc_profile} if carrier.icc_profile else {}
    try:
        Image.fromarray(arr).save(buf, format="PNG", **kwargs)
    except (OSError, ValueError) as e:
        raise ImageMergerError(f"Could not encode the merged PNG: {e}") from e
    return buf.getvalue()


# --------------------------------------------------------------------------
# Capacity helpers
# --------------------------------------------------------------------------

def pool_size(eligible_count: int, slot: int) -> int:
    """Number of positions that belong to `slot` (even/odd entries of E)."""
    return max(0, (eligible_count - slot + 1) // 2)


def sealed_length(image_bytes: int) -> int:
    return HEADER_SEALED_LEN + BODY_OVERHEAD + image_bytes


def fill_ratio(sealed_lengths, eligible_count: int) -> float:
    if eligible_count <= 0:
        return float("inf")
    return sum(sealed_lengths) * 8 / eligible_count


def max_slot_bits(eligible_count: int, slot: int) -> int:
    return int(MAX_SLOT_FILL * pool_size(eligible_count, slot))


# --------------------------------------------------------------------------
# Keys, sealing and bit positions
# --------------------------------------------------------------------------

def derive_keys(code: int, width: int, height: int, slot: int):
    """Return (aead_key, path_seed) for a numeric code and slot."""
    salt = hashlib.sha256(FORMAT_TAG + b"|salt|" + struct.pack(">IIB", width, height, slot)).digest()
    material = hashlib.scrypt(
        b"imrg2:" + str(code).encode("ascii"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=64,
    )
    return material[:32], material[32:]


def seal_image(image_bytes: bytes, aead_key: bytes) -> bytes:
    """Encrypt one hidden image: sealed header (44 bytes) + sealed body."""
    aead = ChaCha20Poly1305(aead_key)
    header = _HEADER_STRUCT.pack(STEALTH_VERSION, 0, len(image_bytes))
    n1, n2 = os.urandom(NONCE_LEN), os.urandom(NONCE_LEN)
    return (n1 + aead.encrypt(n1, header, b"h")
            + n2 + aead.encrypt(n2, image_bytes, b"b" + header))


def path_positions(seed: bytes, pool: int, count: int) -> np.ndarray:
    """First `count` distinct pool indexes of a keyed pseudorandom sequence.

    The sequence comes from a ChaCha20 keystream, so it does not depend on
    numpy's RNG. The first m entries are always a prefix of the first M entries.
    """
    if count > pool:
        raise CapacityError("Not enough room in the carrier for this image.")
    if count == 0:
        return np.empty(0, dtype=np.int64)
    stream = Cipher(algorithms.ChaCha20(seed, bytes(16)), mode=None).encryptor()
    zeros = bytes(DRAW_BLOCK * 8)
    seen = np.zeros(pool, dtype=bool)
    chunks, got = [], 0
    while got < count:
        draws = np.frombuffer(stream.update(zeros), dtype="<u8") % np.uint64(pool)
        draws = draws.astype(np.int64)
        fresh = draws[~seen[draws]]
        _, first = np.unique(fresh, return_index=True)
        fresh = fresh[np.sort(first)]
        seen[fresh] = True
        chunks.append(fresh)
        got += fresh.size
    return np.concatenate(chunks)[:count]


def _flat_positions(carrier: Carrier, slot: int, path: np.ndarray) -> np.ndarray:
    return carrier.eligible[slot + 2 * path].astype(np.int64)


def embed_slot(carrier: Carrier, slot: int, sealed: bytes, seed: bytes) -> None:
    """Write `sealed` into the carrier's pixel values (modifies carrier.color)."""
    bits = np.unpackbits(np.frombuffer(sealed, dtype=np.uint8))
    pool = pool_size(carrier.eligible_count, slot)
    if bits.size > MAX_SLOT_FILL * pool:
        raise CapacityError("The hidden image does not fit in this carrier.")
    positions = _flat_positions(carrier, slot, path_positions(seed, pool, bits.size))

    flat = carrier.color.reshape(-1)
    values = flat[positions]
    change = (values & 1) != bits
    target = positions[change]
    old = values[change].astype(np.int16)
    low = old & 3
    random_step = np.random.default_rng().choice(np.array([-1, 1], dtype=np.int16), size=old.size)
    # Stay inside the group of four: 0 can only go up, 3 can only go down.
    step = np.where(low == 0, 1, np.where(low == 3, -1, random_step)).astype(np.int16)
    flat[target] = (old + step).astype(np.uint8)


def read_slot(carrier: Carrier, code: int, slot: int) -> Optional[bytes]:
    """Return the hidden image bytes for (code, slot), or None if nothing verifies."""
    pool = pool_size(carrier.eligible_count, slot)
    if pool < HEADER_SEALED_LEN * 8:
        return None
    aead_key, seed = derive_keys(code, carrier.width, carrier.height, slot)
    aead = ChaCha20Poly1305(aead_key)
    flat = carrier.color.reshape(-1)

    def read_bytes(nbytes: int) -> bytes:
        pos = _flat_positions(carrier, slot, path_positions(seed, pool, nbytes * 8))
        return np.packbits(flat[pos] & 1).tobytes()

    head = read_bytes(HEADER_SEALED_LEN)
    try:
        header = aead.decrypt(head[:NONCE_LEN], head[NONCE_LEN:], b"h")
    except InvalidTag:
        return None
    version, _flags, length = _HEADER_STRUCT.unpack(header)
    if version != STEALTH_VERSION:
        return None
    total = sealed_length(length)
    if total * 8 > MAX_SLOT_FILL * pool:
        return None

    blob = read_bytes(total)
    body = blob[HEADER_SEALED_LEN:]
    try:
        return aead.decrypt(body[:NONCE_LEN], body[NONCE_LEN:], b"b" + header)
    except InvalidTag:
        return None


def embed_images(carrier: Carrier, codes: Dict[int, int], image_bytes: Dict[int, bytes]) -> Dict[int, int]:
    """Seal and embed {slot: image_bytes} using {slot: code}. Returns sealed sizes per slot."""
    sizes = {}
    for slot, data in image_bytes.items():
        aead_key, seed = derive_keys(codes[slot], carrier.width, carrier.height, slot)
        sealed = seal_image(data, aead_key)
        embed_slot(carrier, slot, sealed, seed)
        sizes[slot] = len(sealed)
    return sizes
