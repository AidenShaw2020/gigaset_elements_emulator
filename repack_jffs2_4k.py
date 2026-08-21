#!/usr/bin/env python3
"""Repack a JFFS2 image so no node crosses a 4 KiB erase block."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


MAGIC = 0x1985
CLEANMARKER = bytes.fromhex("851903200c000000b1b01ee4")


def align4(value: int) -> int:
    return (value + 3) & ~3


def read_nodes(image: bytes, source_block: int) -> list[bytes]:
    nodes: list[bytes] = []
    for block_start in range(0, len(image), source_block):
        block_end = min(block_start + source_block, len(image))
        offset = block_start
        while offset + 12 <= block_end:
            if image[offset : offset + 4] == b"\xff" * 4:
                break
            magic, node_type, total_length, _header_crc = struct.unpack_from(
                "<HHII", image, offset
            )
            if magic != MAGIC:
                raise ValueError(f"invalid JFFS2 magic at 0x{offset:x}")
            if total_length < 12 or offset + total_length > block_end:
                raise ValueError(
                    f"invalid/crossing node at 0x{offset:x}, length 0x{total_length:x}"
                )
            padded_end = offset + align4(total_length)
            node = image[offset:padded_end]
            # Cleanmarkers are regenerated at every physical 4 KiB block.
            if not ((node_type & 0x0FFF) == 3 and total_length == 12):
                nodes.append(node)
            offset = padded_end
    return nodes


def repack(nodes: list[bytes], output_size: int, block_size: int) -> bytes:
    output = bytearray(b"\xff" * output_size)
    offset = 0

    def start_block(at: int) -> int:
        if at + len(CLEANMARKER) > output_size:
            raise ValueError("output image is too small")
        output[at : at + len(CLEANMARKER)] = CLEANMARKER
        return at + len(CLEANMARKER)

    offset = start_block(0)
    for node in nodes:
        if len(node) > block_size - len(CLEANMARKER):
            raise ValueError(f"node of {len(node)} bytes does not fit in a block")
        block_end = ((offset // block_size) + 1) * block_size
        if offset + len(node) > block_end:
            offset = block_end
            offset = start_block(offset)
        output[offset : offset + len(node)] = node
        offset += len(node)

    # Mark all remaining erase blocks clean, matching the vendor image layout.
    next_block = ((offset + block_size - 1) // block_size) * block_size
    for block_start in range(next_block, output_size, block_size):
        output[block_start : block_start + len(CLEANMARKER)] = CLEANMARKER
    return bytes(output)


def validate(image: bytes, block_size: int) -> None:
    for block_start in range(0, len(image), block_size):
        if image[block_start : block_start + len(CLEANMARKER)] != CLEANMARKER:
            raise ValueError(f"missing cleanmarker at 0x{block_start:x}")
        offset = block_start + len(CLEANMARKER)
        block_end = min(block_start + block_size, len(image))
        while offset + 12 <= block_end and image[offset : offset + 4] != b"\xff" * 4:
            magic, _node_type, total_length, _header_crc = struct.unpack_from(
                "<HHII", image, offset
            )
            if magic != MAGIC or total_length < 12:
                raise ValueError(f"invalid node at 0x{offset:x}")
            offset += align4(total_length)
            if offset > block_end:
                raise ValueError(f"node crosses block ending at 0x{block_end:x}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-block", type=lambda value: int(value, 0), default=0x2000)
    parser.add_argument("--block", type=lambda value: int(value, 0), default=0x1000)
    parser.add_argument("--size", type=lambda value: int(value, 0), default=0x116000)
    args = parser.parse_args()

    nodes = read_nodes(args.input.read_bytes(), args.source_block)
    result = repack(nodes, args.size, args.block)
    validate(result, args.block)
    args.output.write_bytes(result)
    used_blocks = sum(
        result[offset + len(CLEANMARKER) : offset + args.block] != b"\xff" * (args.block - len(CLEANMARKER))
        for offset in range(0, len(result), args.block)
    )
    print(f"nodes={len(nodes)} size={len(result)} used_blocks={used_blocks}")
    print(f"written: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
