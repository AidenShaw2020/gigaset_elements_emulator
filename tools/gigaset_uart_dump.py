#!/usr/bin/env python3
"""Read the complete Gigaset Elements Base flash via the SC14452 UART ROM.

This host only uploads a RAM-resident, read-only loader and receives bytes.
It never sends a flash erase/program command.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import time
from pathlib import Path

import serial


ROM_STX = 0x02
ROM_SOH = 0x01
ROM_ACK = 0x06
ROM_NAK = 0x15
DUMP_MAGIC = b"GIGA452D"
DUMP_DONE = b"DONE"


def read_until_byte(port: serial.Serial, wanted: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = port.read(1)
        if data and data[0] == wanted:
            return
    raise TimeoutError(f"timeout waiting for byte 0x{wanted:02x}")


def read_exact(port: serial.Serial, size: int, timeout: float) -> bytes:
    result = bytearray()
    deadline = time.monotonic() + timeout
    while len(result) < size:
        block = port.read(min(65536, size - len(result)))
        if block:
            result.extend(block)
            deadline = time.monotonic() + timeout
        elif time.monotonic() >= deadline:
            raise TimeoutError(f"received {len(result)} of {size} bytes")
    return bytes(result)


def fnv1a_update(value: int, data: bytes) -> int:
    for byte in data:
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return value


def upload_loader(port_name: str, loader: bytes, wait_seconds: float) -> serial.Serial:
    port = serial.Serial(port_name, 9600, timeout=0.2)
    port.dtr = False
    port.rts = False
    port.reset_input_buffer()
    print(f"Waiting for SC14452 ROM STX on {port_name} ...", flush=True)
    read_until_byte(port, ROM_STX, wait_seconds)
    print("ROM bootloader detected; uploading read-only RAM loader", flush=True)
    port.write(struct.pack("<BH", ROM_SOH, len(loader)))

    deadline = time.monotonic() + 5
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("ROM did not acknowledge loader header")
        reply = port.read(1)
        if not reply or reply[0] == ROM_STX:
            continue
        if reply[0] == ROM_NAK:
            raise RuntimeError("ROM rejected loader length")
        if reply[0] != ROM_ACK:
            raise RuntimeError(f"unexpected ROM reply 0x{reply[0]:02x}")
        break

    port.write(loader)
    expected_xor = 0
    for byte in loader:
        expected_xor ^= byte
    received = read_exact(port, 1, 5)[0]
    if received != expected_xor:
        raise RuntimeError(
            f"loader checksum mismatch: expected 0x{expected_xor:02x}, got 0x{received:02x}"
        )
    port.write(bytes([ROM_ACK]))
    port.flush()
    # Prepnuti rychlosti hned po ACK vyvola na lince zakmit, ktery ROM utne
    # skok do payloadu. Nechame linku chvili v klidu; loader ma pred odeslanim
    # hlavicky DELAY(1000), coz je zmerenych ~906 ms, takze rezerva staci.
    time.sleep(0.3)
    port.baudrate = 115200
    port.timeout = 0.5
    return port


def find_magic(port: serial.Serial, timeout: float) -> None:
    window = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        byte = port.read(1)
        if not byte:
            continue
        window.extend(byte)
        if len(window) > len(DUMP_MAGIC):
            del window[0]
        if bytes(window) == DUMP_MAGIC:
            return
    raise TimeoutError("RAM loader did not send dump header")


def dump_flash(port: serial.Serial, output: Path) -> None:
    find_magic(port, 15)
    flash_size, flash_id = struct.unpack("<II", read_exact(port, 8, 5))
    if flash_size <= 0 or flash_size > 0x1000000:
        raise RuntimeError(f"implausible flash size 0x{flash_size:x}")
    print(f"Flash ID: 0x{flash_id:06x}; size: {flash_size} bytes", flush=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    sha256 = hashlib.sha256()
    fnv1a = 2166136261
    received = 0
    started = time.monotonic()
    with output.open("wb") as stream:
        while received < flash_size:
            block = read_exact(port, min(65536, flash_size - received), 15)
            stream.write(block)
            sha256.update(block)
            fnv1a = fnv1a_update(fnv1a, block)
            received += len(block)
            elapsed = max(time.monotonic() - started, 0.001)
            print(
                f"\r{received}/{flash_size} ({received * 100 / flash_size:5.1f}%) "
                f"{received / elapsed / 1024:6.1f} KiB/s",
                end="",
                flush=True,
            )
    print()

    device_hash = struct.unpack("<I", read_exact(port, 4, 5))[0]
    trailer = read_exact(port, 4, 5)
    if trailer != DUMP_DONE:
        raise RuntimeError(f"invalid trailer {trailer!r}")
    if device_hash != fnv1a:
        raise RuntimeError(
            f"FNV-1a mismatch: device 0x{device_hash:08x}, host 0x{fnv1a:08x}"
        )
    print(f"Saved: {output.resolve()}")
    print(f"SHA256: {sha256.hexdigest()}")
    print(f"FNV-1a verified: 0x{fnv1a:08x}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--loader", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait", type=float, default=180)
    args = parser.parse_args()

    loader = args.loader.read_bytes()
    if not loader or len(loader) > 0xFFFF:
        raise RuntimeError(f"invalid loader size: {len(loader)}")
    port = upload_loader(args.port, loader, args.wait)
    try:
        dump_flash(port, args.output)
    finally:
        port.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
