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


# --------------------------------------------------------------------------
# Format v1: byte shuffle (kept so old merged files can still be read)
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Format v2: block shuffle (the result is still a normal, compressible image)
# --------------------------------------------------------------------------

def _to_blocks(pixels: np.ndarray, block: int) -> np.ndarray:
    h, w, c = pixels.shape
    if h % block or w % block:
        raise ValueError("Image size must be a multiple of the block size.")
    by, bx = h // block, w // block
    return (pixels.reshape(by, block, bx, block, c)
                  .swapaxes(1, 2)
                  .reshape(by * bx, block, block, c))


def _from_blocks(blocks: np.ndarray, h: int, w: int) -> np.ndarray:
    n, block, _, c = blocks.shape
    by, bx = h // block, w // block
    return np.ascontiguousarray(
        blocks.reshape(by, bx, block, block, c).swapaxes(1, 2).reshape(h, w, c))


def shuffle_blocks(pixels: np.ndarray, block: int, code: int) -> np.ndarray:
    """Permute the block x block tiles of an (H, W, C) uint8 array."""
    h, w, _ = pixels.shape
    blocks = _to_blocks(pixels, block)
    perm = permutation_for(blocks.shape[0], code)
    return _from_blocks(blocks[perm], h, w)


def unshuffle_blocks(pixels: np.ndarray, block: int, code: int) -> np.ndarray:
    """
    Reverse shuffle_blocks(). The right code restores the original tiles;
    any other code returns a scrambled but same-size image.
    """
    h, w, _ = pixels.shape
    blocks = _to_blocks(pixels, block)
    perm = permutation_for(blocks.shape[0], code)
    out = np.empty_like(blocks)
    out[perm] = blocks
    return _from_blocks(out, h, w)
