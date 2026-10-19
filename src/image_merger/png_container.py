import zlib

from image_merger.format import ImageMergerError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CHUNK_LENGTH = 0x7FFFFFFF  # PNG spec: chunk length must be < 2^31


class ContainerError(ImageMergerError):
    """The file is not a readable PNG."""


def _crc(chunk_type: bytes, data) -> int:
    return zlib.crc32(data, zlib.crc32(chunk_type)) & 0xFFFFFFFF


def _walk(png: bytes):

    if len(png) < 8 or png[:8] != PNG_SIGNATURE:
        raise ContainerError("This is not a valid PNG file.")

    pos = 8
    while pos + 12 <= len(png):
        length = int.from_bytes(png[pos:pos + 4], "big")
        ctype = bytes(png[pos + 4:pos + 8])
        data_start = pos + 8
        data_end = data_start + length
        chunk_end = data_end + 4
        if chunk_end > len(png):
            raise ContainerError("This PNG file is truncated or damaged.")
        yield ctype, pos, data_start, data_end, chunk_end
        if ctype == b"IEND":
            return
        pos = chunk_end

    raise ContainerError("This PNG file is truncated or damaged (no end marker).")


def embed_chunk(png: bytes, chunk_type: bytes, payload: bytes) -> bytes:

    if len(chunk_type) != 4:
        raise ValueError("PNG chunk types are exactly 4 bytes.")
    if len(payload) > MAX_CHUNK_LENGTH:
        raise ContainerError("The combined image data is too large for a single PNG chunk.")

    iend_start = None
    for ctype, start, *_ in _walk(png):
        if ctype == b"IEND":
            iend_start = start

    chunk = b"".join([
        len(payload).to_bytes(4, "big"),
        chunk_type,
        payload,
        _crc(chunk_type, payload).to_bytes(4, "big"),
    ])
    return b"".join([png[:iend_start], chunk, png[iend_start:]])


def find_chunk(png: bytes, chunk_type: bytes):

    for ctype, _start, data_start, data_end, chunk_end in _walk(png):
        if ctype != chunk_type:
            continue
        data = png[data_start:data_end]
        stored = int.from_bytes(png[data_end:chunk_end], "big")
        if _crc(chunk_type, data) != stored:
            raise ContainerError(

            )
        return bytes(data)
    return None
