import hashlib
import hmac
import struct
from dataclasses import dataclass, field
from typing import List

MAGIC = b"IMRG"
FORMAT_VERSION = 2             # version written by this app
SUPPORTED_READ_VERSIONS = (1, 2)
CHUNK_TYPE = b"imMg"           # ancillary, private, safe-to-copy PNG chunk
MIN_ENTRY_COUNT = 1
MAX_ENTRY_COUNT = 2

# Shuffle algorithms (stored in the container header)
SHUFFLE_BYTES = 1              # v1: every byte permuted, raw pixels (see shuffle.py)
SHUFFLE_BLOCKS = 2             # v2: pixel blocks permuted, then WebP-encoded

# Per-entry codecs
CODEC_RAW = 0                  # v1 only: uncompressed pixel bytes
CODEC_WEBP_LOSSY = 1
CODEC_WEBP_LOSSLESS = 2
WEBP_CODECS = (CODEC_WEBP_LOSSY, CODEC_WEBP_LOSSLESS)

DEFAULT_BLOCK_SIZE = 16        # matches the WebP/JPEG macroblock grid
MIN_BLOCK_SIZE = 4
MAX_BLOCK_SIZE = 128
MAX_WEBP_DIMENSION = 16383     # hard limit of the WebP format

MODE_RGB = 1
MODE_RGBA = 2
MODE_CHANNELS = {MODE_RGB: 3, MODE_RGBA: 4}
MODE_TO_PIL = {MODE_RGB: "RGB", MODE_RGBA: "RGBA"}

SALT_LEN = 16
VERIFIER_LEN = 32
KDF_ITERATIONS = 200_000
MAX_KDF_ITERATIONS = 5_000_000  # sanity bound when reading untrusted files

MAX_PAYLOAD_BYTES = 64 * 1024 * 1024

_HEADER = struct.Struct(">4sBBBBI")
# v1 entry: width, height, mode, data_len, salt, verifier
_ENTRY_V1 = struct.Struct(f">IIBQ{SALT_LEN}s{VERIFIER_LEN}s")
# v2 entry: width, height, mode, codec, block_size, quality, data_len, salt, verifier
_ENTRY_V2 = struct.Struct(f">IIBBBBQ{SALT_LEN}s{VERIFIER_LEN}s")

_DAMAGED = "The hidden data in this file is damaged or incomplete, so it cannot be read."
_TOO_LARGE = "The hidden image in this file is larger than this app supports."


class ImageMergerError(Exception):
    """Base class for all expected, user-presentable errors."""


class FormatError(ImageMergerError):
    """The hidden payload is missing, damaged, or from an unknown version."""


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------

@dataclass
class PayloadEntry:
    width: int
    height: int
    mode: int          # MODE_RGB or MODE_RGBA
    salt: bytes
    verifier: bytes
    data: bytes        # v1: shuffled raw pixel bytes; v2: WebP of the block-shuffled image
    codec: int = CODEC_RAW
    block_size: int = 0
    quality: int = 0   # informational: 1-100 for lossy, 0 for lossless/raw


@dataclass
class Container:
    entries: List[PayloadEntry] = field(default_factory=list)
    kdf_iterations: int = KDF_ITERATIONS
    shuffle_algorithm: int = SHUFFLE_BLOCKS
    version: int = FORMAT_VERSION


# --------------------------------------------------------------------------
# Code verifier
# --------------------------------------------------------------------------

def make_verifier(code: int, salt: bytes, iterations: int) -> bytes:
    """Salted, slow hash of the (canonical integer) code."""
    return hashlib.pbkdf2_hmac(
        "sha256", b"imrg-v1:" + str(code).encode("ascii"), salt, iterations
    )


def code_matches(code: int, entry: PayloadEntry, iterations: int) -> bool:
    return hmac.compare_digest(make_verifier(code, entry.salt, iterations), entry.verifier)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def padded_size(width: int, height: int, block_size: int):
    """Image size rounded up to a whole number of blocks."""
    return (-(-width // block_size) * block_size,
            -(-height // block_size) * block_size)


def _validate_geometry(width: int, height: int, mode: int, data_len: int,
                       codec: int = CODEC_RAW, block_size: int = 0,
                       quality: int = 0) -> None:
    """Validate sizes BEFORE any image-sized memory is allocated."""
    if mode not in MODE_CHANNELS:
        raise FormatError(_DAMAGED)
    if width <= 0 or height <= 0:
        raise FormatError(_DAMAGED)
    raw_size = width * height * MODE_CHANNELS[mode]
    if raw_size > MAX_PAYLOAD_BYTES:
        raise FormatError(_TOO_LARGE)

    if codec == CODEC_RAW:
        if data_len != raw_size:
            raise FormatError(_DAMAGED)
        return

    if codec not in WEBP_CODECS or not MIN_BLOCK_SIZE <= block_size <= MAX_BLOCK_SIZE:
        raise FormatError(_DAMAGED)
    if not 0 <= quality <= 100:
        raise FormatError(_DAMAGED)
    if max(padded_size(width, height, block_size)) > MAX_WEBP_DIMENSION:
        raise FormatError(_TOO_LARGE)
    if not 0 < data_len <= MAX_PAYLOAD_BYTES:
        raise FormatError(_DAMAGED)


# --------------------------------------------------------------------------
# Pack / unpack
# --------------------------------------------------------------------------

def pack(container: Container) -> bytes:
    """Always writes the current format version (v2)."""
    if not MIN_ENTRY_COUNT <= len(container.entries) <= MAX_ENTRY_COUNT:
        raise FormatError(
            f"A merged file must hold {MIN_ENTRY_COUNT} or {MAX_ENTRY_COUNT} images."
        )

    parts = [_HEADER.pack(
        MAGIC, FORMAT_VERSION, SHUFFLE_BLOCKS,
        len(container.entries), 0, container.kdf_iterations,
    )]
    for e in container.entries:
        if len(e.salt) != SALT_LEN or len(e.verifier) != VERIFIER_LEN:
            raise FormatError("Internal error: bad salt/verifier length.")
        if e.codec == CODEC_RAW:
            raise FormatError("Internal error: raw entries cannot be written in format v2.")
        _validate_geometry(e.width, e.height, e.mode, len(e.data),
                           e.codec, e.block_size, e.quality)
        parts.append(_ENTRY_V2.pack(e.width, e.height, e.mode, e.codec, e.block_size,
                                    e.quality, len(e.data), e.salt, e.verifier))
        parts.append(e.data)
    return b"".join(parts)


def unpack(blob: bytes) -> Container:
    """Reads format v1 (raw, byte-shuffled) and v2 (WebP, block-shuffled)."""
    if len(blob) < _HEADER.size:
        raise FormatError(_DAMAGED)

    magic, version, algo, count, _reserved, iterations = _HEADER.unpack_from(blob, 0)
    if magic != MAGIC:
        raise FormatError("The hidden data in this PNG was not created by this app.")
    if version not in SUPPORTED_READ_VERSIONS:
        raise FormatError(
            f"This file uses format version {version}, which this version of the "
            f"app can't read. Please update the app."
        )
    expected_algo = SHUFFLE_BYTES if version == 1 else SHUFFLE_BLOCKS
    if algo != expected_algo or not MIN_ENTRY_COUNT <= count <= MAX_ENTRY_COUNT:
        raise FormatError(_DAMAGED)
    if not 1 <= iterations <= MAX_KDF_ITERATIONS:
        raise FormatError(_DAMAGED)

    entry_struct = _ENTRY_V1 if version == 1 else _ENTRY_V2
    pos = _HEADER.size
    entries = []
    for _ in range(count):
        if pos + entry_struct.size > len(blob):
            raise FormatError(_DAMAGED)
        fields = entry_struct.unpack_from(blob, pos)
        pos += entry_struct.size
        if version == 1:
            width, height, mode, data_len, salt, verifier = fields
            codec, block_size, quality = CODEC_RAW, 0, 0
        else:
            width, height, mode, codec, block_size, quality, data_len, salt, verifier = fields

        _validate_geometry(width, height, mode, data_len, codec, block_size, quality)
        if pos + data_len > len(blob):
            raise FormatError(_DAMAGED)

        entries.append(PayloadEntry(width, height, mode, salt, verifier,
                                    bytes(blob[pos:pos + data_len]),
                                    codec, block_size, quality))
        pos += data_len

    if pos != len(blob):
        raise FormatError(_DAMAGED)

    return Container(entries=entries, kdf_iterations=iterations,
                     shuffle_algorithm=algo, version=version)
