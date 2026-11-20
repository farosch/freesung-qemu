#!/usr/bin/env python3
"""Create an isolated synthetic uImage with the TIFSDaemon auth check patched.

This is a test-only artifact. It does not patch a signed production image and
must not be used on a physical device.
"""

from __future__ import annotations

import argparse
import binascii
import struct
from pathlib import Path


AUTH_FUNCTION_VADDR = 0xC01759D0
AUTH_FUNCTION_RAW_OFFSET = 0x16D9D0
AUTH_FUNCTION_ORIGINAL = bytes.fromhex("0d c0 a0 e1 00 d8 2d e9")
AUTH_FUNCTION_PATCHED = bytes.fromhex("00 00 a0 e3 1e ff 2f e1")
IMAGE_HEADER_SIZE = 64


def uimage_crc(data: bytes) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def patch_synthetic_uimage(source: Path, output: Path) -> dict:
    data = bytearray(source.read_bytes())
    if len(data) < IMAGE_HEADER_SIZE:
        raise ValueError("uImage header is truncated")
    if data[:4] != bytes.fromhex("27051956"):
        raise ValueError("input is not a uImage")

    raw_size = struct.unpack(">I", data[12:16])[0]
    payload_offset = IMAGE_HEADER_SIZE + AUTH_FUNCTION_RAW_OFFSET
    if payload_offset + len(AUTH_FUNCTION_ORIGINAL) > IMAGE_HEADER_SIZE + raw_size:
        raise ValueError("auth function offset exceeds uImage payload")

    original = bytes(data[payload_offset:payload_offset + len(AUTH_FUNCTION_ORIGINAL)])
    if original != AUTH_FUNCTION_ORIGINAL:
        raise RuntimeError("synthetic uImage auth function bytes do not match")

    data[payload_offset:payload_offset + len(AUTH_FUNCTION_PATCHED)] = (
        AUTH_FUNCTION_PATCHED
    )

    payload = bytes(data[IMAGE_HEADER_SIZE:IMAGE_HEADER_SIZE + raw_size])
    struct.pack_into(">I", data, 24, uimage_crc(payload))

    struct.pack_into(">I", data, 4, 0)
    struct.pack_into(">I", data, 4, uimage_crc(bytes(data[:IMAGE_HEADER_SIZE])))

    output.write_bytes(data)
    return {
        "raw_size": raw_size,
        "auth_vaddr": AUTH_FUNCTION_VADDR,
        "payload_offset": payload_offset,
        "data_crc": struct.unpack(">I", data[24:28])[0],
        "header_crc": struct.unpack(">I", data[4:8])[0],
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = patch_synthetic_uimage(args.source, args.output)
    print(
        f"created synthetic uImage: {args.output} "
        f"(auth VA=0x{report['auth_vaddr']:08x}, "
        f"payload offset=0x{report['payload_offset']:x})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
