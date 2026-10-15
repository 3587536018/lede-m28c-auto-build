#!/usr/bin/env python3
"""Read-only integrity checks for the pinned M28C sysupgrade image format.

LEDE 8e247d214 builds this image as gzip | append-metadata. fwtool commit
8f7fe925ca205c8e8e2d0d1b16218c1e148d5173 appends an info chunk and optionally
a signature chunk. Its CRC covers every preceding file byte, starting with
0xffffffff and without the final XOR: zlib.crc32(prefix) ^ 0xffffffff.

Signature framing and CRC are checked here; publisher authentication requires
a trusted public key and is outside this integrity check. Original image bytes,
including sysupgrade metadata, are never removed or rewritten.
"""

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path


MAGIC = 0x46577830
TRAILER = struct.Struct(">IIB3sI")
INFO = 1
SIGNATURE = 0
METADATA_LIMIT = 30 * 1024
SIGNATURE_LIMIT = 1024
BLOCK_SIZE = 1024 * 1024
DEFAULT_DEVICE = "widora,mangopi-m28c"
TARGET = "rockchip/armv8"


def read_chunk(stream, end):
    if end < TRAILER.size:
        raise ValueError("Missing or truncated fwtool trailer")
    stream.seek(end - TRAILER.size)
    trailer = stream.read(TRAILER.size)
    if len(trailer) != TRAILER.size:
        raise ValueError("Truncated fwtool trailer")
    magic, crc, kind, padding, size = TRAILER.unpack(trailer)
    if magic != MAGIC:
        raise ValueError("Missing fwtool metadata or unexpected trailing data")
    if padding != b"\0\0\0":
        raise ValueError("Invalid fwtool trailer padding")
    if kind not in (INFO, SIGNATURE):
        raise ValueError(f"Unsupported fwtool chunk type: {kind}")
    limit = METADATA_LIMIT + 8 if kind == INFO else SIGNATURE_LIMIT
    payload_size = size - TRAILER.size
    minimum = 9 if kind == INFO else 1
    if not minimum <= payload_size <= limit or size > end:
        raise ValueError("Invalid fwtool chunk size")
    start = end - size
    stream.seek(start)
    payload = stream.read(payload_size)
    if len(payload) != payload_size:
        raise ValueError("Truncated fwtool chunk payload")
    return start, kind, crc, payload, trailer


def gzip_crc(stream, length):
    """Validate exactly one gzip member, using bounded decompression memory."""
    if length < 20:
        raise ValueError("Missing or truncated gzip member")
    stream.seek(0)
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    remaining = length
    checksum = 0
    output_size = 0
    try:
        while remaining:
            block = stream.read(min(BLOCK_SIZE, remaining))
            if not block:
                raise ValueError("Truncated gzip member")
            remaining -= len(block)
            checksum = zlib.crc32(block, checksum)
            pending = block
            while pending:
                output_size += len(decoder.decompress(pending, BLOCK_SIZE))
                pending = decoder.unconsumed_tail
                if decoder.unused_data:
                    raise ValueError("Unexpected bytes or additional gzip member before metadata")
            if decoder.eof and remaining:
                raise ValueError("Unexpected bytes or additional gzip member before metadata")
    except zlib.error as exc:
        raise ValueError(f"Invalid gzip data, CRC or length: {exc}") from exc
    if not decoder.eof:
        raise ValueError("Truncated gzip member: missing stream end or checksum")
    if not output_size:
        raise ValueError("Firmware gzip payload is empty")
    return checksum


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON metadata field: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def parse_metadata(payload, expected_device):
    if payload[:8] != b"\0" * 8:
        raise ValueError("Unsupported fwtool metadata header version or flags")
    try:
        metadata = json.loads(payload[8:].decode("utf-8"),
                              object_pairs_hook=unique_object,
                              parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid fwtool JSON metadata: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError("Firmware metadata must be a JSON object")
    if metadata.get("metadata_version") != "1.1" or metadata.get("compat_version") != "1.0":
        raise ValueError("Unsupported metadata or compatibility version for the pinned image")
    devices = metadata.get("supported_devices")
    if (not isinstance(devices, list) or not devices
            or not all(isinstance(device, str) for device in devices)
            or expected_device not in devices):
        raise ValueError(f"Firmware supported_devices does not include {expected_device}")
    version = metadata.get("version")
    if not isinstance(version, dict) or version.get("target") != TARGET:
        raise ValueError(f"Firmware target must be {TARGET}")
    expected_board = expected_device.replace(",", "_")
    if version.get("board") != expected_board:
        raise ValueError(f"Firmware board must be {expected_board}")
    return metadata


def verify_image(path, expected_device=DEFAULT_DEVICE):
    """Return validated metadata, or raise ValueError for invalid image content."""
    path = Path(path)
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        last = read_chunk(stream, size)
        if last[1] == SIGNATURE:
            info = read_chunk(stream, last[0])
            if info[1] != INFO:
                raise ValueError("A signature must follow exactly one metadata chunk")
            chunks = [info, last]
        else:
            info = last
            chunks = [info]

        checksum = gzip_crc(stream, info[0])
        for _, kind, expected_crc, payload, trailer in chunks:
            checksum = zlib.crc32(payload, checksum)
            if (checksum ^ 0xffffffff) != expected_crc:
                label = "metadata" if kind == INFO else "signature"
                raise ValueError(f"Invalid fwtool {label} CRC")
            checksum = zlib.crc32(trailer, checksum)
        return parse_metadata(info[3], expected_device)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=DEFAULT_DEVICE,
                        help="Required supported_devices identifier (default: %(default)s)")
    parser.add_argument("images", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.images:
        try:
            metadata = verify_image(path, args.device)
        except (OSError, ValueError) as exc:
            parser.exit(1, f"ERROR: {path}: {exc}\n")
        print(f"Verified {path}: gzip integrity and fwtool metadata for "
              f"{args.device} ({metadata['version']['target']})")


if __name__ == "__main__":
    main()
