#!/usr/bin/env python3
"""Write an image to a Gigaset Elements Base's SPI flash via the SC14452 ROM UART.

WARNING: this tool ERASES AND OVERWRITES flash memory starting at offset 0.
The vendor programmer (452fp.bin) cannot target a specific offset, so it
always writes the whole image starting from the beginning.

Protocol (taken from the state machine in flprogr.c):
  1. ROM handshake:  STX -> SOH+u16 length -> ACK -> loader body -> XOR -> ACK
  2. switch to 115200 (only after a short delay, otherwise the ROM does not
     jump into the payload)
  3. PROG_IDLE:      the chip sends PROG_STX (0x05)
     the host sends  PROG_SOH (0x03) + u32 image length (little endian)
  4. the chip replies PROG_ACK (0x07) (or PROG_NAK 0x16 on a bad length)
  5. the host sends the whole image -> the chip returns an XOR checksum ->
     the host sends PROG_ACK
  6. the chip erases sectors, programs flash and sends PROG_COM (0x17)

The vendor's own verification inside the chip is broken (it compares the
write buffer to itself), so a write must be verified with a separate dump.

Interrupting a write corrupts the bootloader, but the ROM UART mode is
masked into the chip and stays available, so the state is always
recoverable by trying again.
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

PROG_SOH = 0x03
PROG_STX = 0x05
PROG_ACK = 0x07
PROG_NAK = 0x16
PROG_COM = 0x17

FLASH_SIZE = 8 * 1024 * 1024


def read_byte(port: serial.Serial, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = port.read(1)
        if data:
            return data[0]
    return None


def upload_loader(port: serial.Serial, loader: bytes, wait_seconds: float) -> None:
    port.dtr = False
    port.rts = False
    port.reset_input_buffer()

    print(f"Waiting for ROM STX (up to {wait_seconds:.0f} s) ...", flush=True)
    deadline = time.monotonic() + wait_seconds
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("ROM never sent STX - is the base in ROM mode?")
        data = port.read(1)
        if data and data[0] == ROM_STX:
            break

    print("ROM detected, sending loader header", flush=True)
    port.write(struct.pack("<BH", ROM_SOH, len(loader)))

    deadline = time.monotonic() + 5
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("ROM did not acknowledge the header")
        reply = port.read(1)
        if not reply or reply[0] == ROM_STX:
            continue
        if reply[0] == ROM_NAK:
            raise RuntimeError("ROM rejected the loader length")
        if reply[0] != ROM_ACK:
            raise RuntimeError(f"unexpected ROM reply 0x{reply[0]:02x}")
        break

    print("Sending loader body", flush=True)
    port.write(loader)
    expected = 0
    for byte in loader:
        expected ^= byte

    got = read_byte(port, 10)
    if got is None:
        raise TimeoutError("ROM did not send a checksum")
    if got != expected:
        raise RuntimeError(
            f"loader checksum mismatch: expected 0x{expected:02x}, got 0x{got:02x}"
        )
    print(f"Loader checksum OK (0x{expected:02x})", flush=True)
    port.write(bytes([ROM_ACK]))
    port.flush()
    # Switching speed immediately causes a glitch on the line and the ROM
    # does not jump into the payload. The loader has a DELAY(1000) (~906 ms)
    # before its first output, so this delay is safe on both ends.
    time.sleep(0.3)
    port.baudrate = 115200
    port.timeout = 0.5


def send_image(port: serial.Serial, image: bytes, prog_timeout: float) -> None:
    print("Waiting for PROG_STX from the programmer ...", flush=True)
    got = read_byte(port, 20)
    while got is not None and got != PROG_STX:
        got = read_byte(port, 20)
    if got is None:
        raise TimeoutError("programmer never sent PROG_STX")

    print(f"Sending header: length {len(image)} B", flush=True)
    port.write(bytes([PROG_SOH]))
    port.write(struct.pack("<I", len(image)))
    port.flush()

    reply = read_byte(port, 10)
    if reply is None:
        raise TimeoutError("programmer did not acknowledge the length")
    if reply == PROG_NAK:
        raise RuntimeError("programmer rejected the image length")
    if reply != PROG_ACK:
        raise RuntimeError(f"unexpected programmer reply 0x{reply:02x}")

    print("Sending image (the chip buffers it in SDRAM, flash is untouched so far) ...", flush=True)
    chunk = 1024
    start = time.monotonic()
    sent = 0
    for offset in range(0, len(image), chunk):
        port.write(image[offset:offset + chunk])
        sent += len(image[offset:offset + chunk])
        if offset % (256 * 1024) == 0:
            elapsed = time.monotonic() - start
            rate = sent / elapsed / 1024 if elapsed > 0 else 0
            pct = sent * 100.0 / len(image)
            print(f"  {sent}/{len(image)} ({pct:5.1f}%)  {rate:6.1f} KiB/s", flush=True)
    port.flush()

    expected = 0
    for byte in image:
        expected ^= byte
    got = read_byte(port, 60)
    if got is None:
        raise TimeoutError("programmer did not send an image checksum")
    if got != expected:
        raise RuntimeError(
            f"image checksum mismatch: expected 0x{expected:02x}, got 0x{got:02x}"
        )
    print(f"Image checksum OK (0x{expected:02x})", flush=True)

    print("Sending PROG_ACK - flash erase/write starts NOW. DO NOT REMOVE POWER.", flush=True)
    port.write(bytes([PROG_ACK]))
    port.flush()

    print(f"Waiting for PROG_COM (up to {prog_timeout:.0f} s) ...", flush=True)
    got = read_byte(port, prog_timeout)
    if got is None:
        raise TimeoutError("programmer did not confirm completion (PROG_COM)")
    if got != PROG_COM:
        raise RuntimeError(f"unexpected reply after write 0x{got:02x}")
    print("Flash programmed (PROG_COM received).", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--loader", required=True, help="path to 452fp.bin")
    parser.add_argument("--image", required=True, help="image to write to flash")
    parser.add_argument("--wait", type=float, default=120.0)
    parser.add_argument("--prog-timeout", type=float, default=900.0)
    parser.add_argument(
        "--confirm-erase",
        action="store_true",
        help="required confirmation: the tool erases and overwrites the whole flash",
    )
    args = parser.parse_args()

    image = Path(args.image).read_bytes()
    loader = Path(args.loader).read_bytes()

    print(f"Image : {args.image}")
    print(f"        {len(image)} B  sha256={hashlib.sha256(image).hexdigest()}")
    print(f"Loader: {args.loader} ({len(loader)} B)")

    if len(image) > FLASH_SIZE:
        print("ERROR: image is larger than flash", file=sys.stderr)
        return 2
    if not args.confirm_erase:
        print(
            "\nERROR: --confirm-erase is missing. This tool erases the whole\n"
            "flash, including the Factory partition (MAC, DECT RFPI), and\n"
            "writes it from the given image.",
            file=sys.stderr,
        )
        return 2

    with serial.Serial(args.port, 9600, timeout=0.2) as port:
        upload_loader(port, loader, args.wait)
        send_image(port, image, args.prog_timeout)

    print("\nDONE. Verify the write with a fresh dump and a sha256 comparison.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
