import numpy as np

from image_merger.format import ImageMergerError

MIN_CODE_DIGITS = 3
MAX_CODE_DIGITS = 20
_MAX_PARSE_DIGITS = 40  # hard sanity cap when length rules are not enforced


class CodeError(ImageMergerError):
    """The numeric code is missing or malformed."""


def parse_code(code: str, enforce_length: bool = True) -> int:

    code = (code or "").strip()
    if not code:
        raise CodeError("Please enter a numeric code.")
    if not (code.isascii() and code.isdigit()):
        raise CodeError("The code must contain digits only (0-9).")
    if enforce_length:
        if not MIN_CODE_DIGITS <= len(code) <= MAX_CODE_DIGITS:
            raise CodeError(
                f"The code must be {MIN_CODE_DIGITS}-{MAX_CODE_DIGITS} digits long."
            )
    elif len(code) > _MAX_PARSE_DIGITS:
        raise CodeError("That code is too long.")
    return int(code)


def _seed_words(code: int) -> np.ndarray:
    """Split an arbitrary-size non-negative int into 32-bit words for seeding."""
    words = []
    while code:
        words.append(code & 0xFFFFFFFF)
        code >>= 32
    return np.array(words or [0], dtype=np.uint32)


def permutation_for(length: int, code: int) -> np.ndarray:
    """Deterministic permutation of range(length) derived from `code`."""
    rng = np.random.RandomState(_seed_words(code))
    return rng.permutation(length)


def shuffle_bytes(data: bytes, code: int) -> bytes:
    """Reorder `data` according to the permutation derived from `code`."""
    arr = np.frombuffer(data, dtype=np.uint8)
    perm = permutation_for(arr.size, code)
    return arr[perm].tobytes()


def unshuffle_bytes(data: bytes, code: int) -> bytes:
    """
    Reverse shuffle_bytes(). The right code restores the original
    order; any other code returns a scrambled but same-size result.
    """
    arr = np.frombuffer(data, dtype=np.uint8)
    perm = permutation_for(arr.size, code)
    out = np.empty_like(arr)
    out[perm] = arr
    return out.tobytes()
