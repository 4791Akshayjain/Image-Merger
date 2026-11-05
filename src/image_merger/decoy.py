"""Decoy image returned when a code does not match anything.

A wrong code, or a PNG with no hidden data, must not produce an error or any
other tell. The decoy is derived only from the code and the file's size, so the
same wrong code always gives the same noisy mosaic.
"""

import hashlib
import struct

import numpy as np
from PIL import Image

DECOY_LONG_SIDE = 640
BLOCK = 16


def make_decoy(code: int, carrier_width: int, carrier_height: int) -> Image.Image:
    scale = DECOY_LONG_SIDE / max(carrier_width, carrier_height)
    width = max(64, int(round(carrier_width * scale)))
    height = max(64, int(round(carrier_height * scale)))

    digest = hashlib.sha256(
        b"imrg-decoy|" + str(code).encode("ascii") + struct.pack(">II", carrier_width, carrier_height)
    ).digest()
    rng = np.random.RandomState(np.frombuffer(digest, dtype="<u4"))

    blocks = rng.randint(0, 256, size=(-(-height // BLOCK), -(-width // BLOCK), 3), dtype=np.uint8)
    mosaic = np.repeat(np.repeat(blocks, BLOCK, axis=0), BLOCK, axis=1)[:height, :width].astype(np.int16)
    noise = rng.randint(-24, 25, size=(height, width, 3)).astype(np.int16)
    return Image.fromarray(np.clip(mosaic + noise, 0, 255).astype(np.uint8))
