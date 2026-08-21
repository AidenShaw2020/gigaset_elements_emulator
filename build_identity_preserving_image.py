#!/usr/bin/env python3
"""Build a Base-1 recovery image while retaining the target Base identity."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


FLASH_SIZE = 0x800000
FS1_START, FS1_END = 0x359000, 0x46F000
FS2_START, FS2_END = 0x6E0000, 0x7F6000
FACTORY_START, ENV_END = 0x7F6000, 0x800000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base1", type=Path, required=True)
    parser.add_argument("--target-dump", type=Path, required=True)
    parser.add_argument(
        "--fs1",
        type=Path,
        help="optional rebuilt FS1; defaults to preserving FS1 from target dump",
    )
    parser.add_argument("--fs2", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base1 = args.base1.read_bytes()
    target = args.target_dump.read_bytes()
    fs1 = args.fs1.read_bytes() if args.fs1 else target[FS1_START:FS1_END]
    fs2 = args.fs2.read_bytes()
    if len(base1) != FLASH_SIZE or len(target) != FLASH_SIZE:
        raise ValueError("both full flash images must be exactly 8 MiB")
    if len(fs1) != FS1_END - FS1_START or len(fs2) != FS2_END - FS2_START:
        raise ValueError("FS1/FS2 image has an unexpected size")

    result = bytearray(base1)
    # Keep target FS1 by default, or use an explicitly rebuilt compatible slot.
    result[FS1_START:FS1_END] = fs1
    # Active FS2 contains Base-1 data with target cert/key and DECT NVS overlaid.
    result[FS2_START:FS2_END] = fs2
    # Factory carries the hardware MAC/RFPI snapshot; Env carries ethaddr and boot slot.
    result[FACTORY_START:ENV_END] = target[FACTORY_START:ENV_END]
    output = bytes(result)

    if output[:FS1_START] != base1[:FS1_START]:
        raise AssertionError("firmware prefix no longer matches Base 1")
    if output[FS1_END:FS2_START] != base1[FS1_END:FS2_START]:
        raise AssertionError("Linux2 no longer matches Base 1")
    if output[FS1_START:FS1_END] != fs1:
        raise AssertionError("FS1 no longer matches selected image")
    if output[FACTORY_START:ENV_END] != target[FACTORY_START:ENV_END]:
        raise AssertionError("Factory/Env no longer match target dump")

    args.output.write_bytes(output)
    factory_mac = ":".join(f"{byte:02X}" for byte in output[FACTORY_START:FACTORY_START + 6])
    env_match = re.search(rb"ethaddr=([^\x00]+)", output[0x7FE000:ENV_END])
    env_mac = env_match.group(1).decode("ascii") if env_match else "NOT FOUND"
    print(f"written: {args.output.resolve()}")
    print(f"size: {len(output)}")
    print(f"sha256: {sha256(output)}")
    print(f"Factory MAC: {factory_mac}")
    print(f"Env ethaddr: {env_mac}")
    fs1_label = "rebuilt" if args.fs1 else "target"
    print(f"FS1({fs1_label}) sha256: {sha256(output[FS1_START:FS1_END])}")
    print(f"FS2(hybrid) sha256: {sha256(output[FS2_START:FS2_END])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
