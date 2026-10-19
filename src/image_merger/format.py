import hashlib
import hmac
import struct
from dataclasses import dataclass, field
from typing import List


MAGIC = b"IMRG"
FORMAT_VERSION = 1
SHUFFLE_ALGORITHM = 1          # numpy RandomState permutation, see shuffle.py
CHUNK_TYPE = b"imMg"           # ancillary, private, safe-to-copy PNG chunk
ENTRY_COUNT = 2

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
_ENTRY = struct.Struct(f">IIBQ{SALT_LEN}s{VERIFIER_LEN}s")

_DAMAGED = "The hidden data in this file is damaged or incomplete, so it cannot be read."



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
    data: bytes        # shuffled raw pixel bytes


@dataclass
class Container:
    entries: List[PayloadEntry] = field(default_factory=list)
    kdf_iterations: int = KDF_ITERATIONS
    shuffle_algorithm: int = SHUFFLE_ALGORITHM


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

def _validate_geometry(width: int, height: int, mode: int, data_len: int) -> None:
    """Validate sizes BEFORE any image-sized memory is allocated."""
    if mode not in MODE_CHANNELS:
        raise FormatError(_DAMAGED)
    if width <= 0 or height <= 0:
        raise FormatError(_DAMAGED)
    expected = width * height * MODE_CHANNELS[mode]
    if expected > MAX_PAYLOAD_BYTES:
        raise FormatError("The hidden image in this file is larger than this app supports.")
    if data_len != expected:
        raise FormatError(_DAMAGED)


# --------------------------------------------------------------------------
# Pack / unpack
# --------------------------------------------------------------------------

def pack(container: Container) -> bytes:
    if len(container.entries) != ENTRY_COUNT:
        raise FormatError(f"A merged file must hold exactly {ENTRY_COUNT} images.")

    parts = [_HEADER.pack(
        MAGIC, FORMAT_VERSION, container.shuffle_algorithm,
        len(container.entries), 0, container.kdf_iterations,
    )]
    for e in container.entries:
        if len(e.salt) != SALT_LEN or len(e.verifier) != VERIFIER_LEN:
            raise FormatError("Internal error: bad salt/verifier length.")
        _validate_geometry(e.width, e.height, e.mode, len(e.data))
        parts.append(_ENTRY.pack(e.width, e.height, e.mode, len(e.data), e.salt, e.verifier))
        parts.append(e.data)
    return b"".join(parts)


def unpack(blob: bytes) -> Container:
    if len(blob) < _HEADER.size:
        raise FormatError(_DAMAGED)

    magic, version, algo, count, _reserved, iterations = _HEADER.unpack_from(blob, 0)
    if magic != MAGIC:
        raise FormatError("The hidden data in this PNG was not created by this app.")
    if version != FORMAT_VERSION:
        raise FormatError(
            f"This file uses format version {version}, which this version of the "
            f"app can't read. Please update the app."
        )
    if algo != SHUFFLE_ALGORITHM or count != ENTRY_COUNT:
        raise FormatError(_DAMAGED)
    if not 1 <= iterations <= MAX_KDF_ITERATIONS:
        raise FormatError(_DAMAGED)

    pos = _HEADER.size
    entries = []
    for _ in range(count):
        if pos + _ENTRY.size > len(blob):
            raise FormatError(_DAMAGED)
        width, height, mode, data_len, salt, verifier = _ENTRY.unpack_from(blob, pos)
        pos += _ENTRY.size

        _validate_geometry(width, height, mode, data_len)
        if pos + data_len > len(blob):
            raise FormatError(_DAMAGED)

        entries.append(PayloadEntry(width, height, mode, salt, verifier,
                                    bytes(blob[pos:pos + data_len])))
        pos += data_len

    if pos != len(blob):
        raise FormatError(_DAMAGED)

    return Container(entries=entries, kdf_iterations=iterations, shuffle_algorithm=algo)
