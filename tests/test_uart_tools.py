from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extractor = load_tool("extract_base_manifest")
uart_dump = load_tool("gigaset_uart_dump")
uart_flash = load_tool("gigaset_uart_flash")


def jffs2_node(total_length: int = 12) -> bytes:
    return struct.pack("<HHII", 0x1985, 0x2003, total_length, 0)


class Jffs2CarveTests(unittest.TestCase):
    def test_finds_partition_at_exact_offset(self) -> None:
        chunk = jffs2_node() + b"\xff" * 52
        self.assertEqual(extractor.find_jffs2_start(chunk), 0)

    def test_finds_partition_after_four_padding_bytes(self) -> None:
        chunk = b"\xff" * 4 + jffs2_node() + b"\xff" * 48
        self.assertEqual(extractor.find_jffs2_start(chunk), 4)

    def test_rejects_magic_with_implausible_node_length(self) -> None:
        chunk = b"\xff" * 4 + struct.pack("<HHII", 0x1985, 0x2003, 0, 0)
        self.assertIsNone(extractor.find_jffs2_start(chunk))

    def test_carve_removes_leading_padding(self) -> None:
        node = jffs2_node()
        image = b"\x00" * 16 + b"\xff" * 4 + node + b"\xff" * 48
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(extractor, "DATA_PARTITIONS", (("fs", 16, 64),)):
                carved = extractor.carve(image, Path(tmp))
            self.assertEqual(len(carved), 1)
            self.assertTrue(carved[0].read_bytes().startswith(node))


class FakeSerial:
    def __init__(self, incoming: list[bytes]) -> None:
        self.incoming = list(incoming)
        self.writes: list[bytes] = []
        self.dtr = True
        self.rts = True
        self.baudrate = 9600
        self.timeout = 0.2
        self.flush_count = 0
        self.reset_input_count = 0

    def reset_input_buffer(self) -> None:
        self.reset_input_count += 1

    def read(self, _size: int = 1) -> bytes:
        return self.incoming.pop(0) if self.incoming else b""

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        self.flush_count += 1


class UartLoaderTests(unittest.TestCase):
    def test_dump_accepts_delayed_loader_checksum_0x89(self) -> None:
        # 0x89 is the XOR of the currently built 452dump.bin.  Some SC14452
        # ROMs return it only after the full 9600-baud upload has drained.
        loader = b"\x89"
        port = FakeSerial(
            [
                bytes([uart_dump.ROM_STX]),
                b"",  # a slow ROM has not replied to the length yet
                bytes([uart_dump.ROM_ACK]),
                bytes([0x89]),
            ]
        )
        with (
            mock.patch.object(uart_dump.serial, "Serial", return_value=port),
            mock.patch.object(uart_dump.time, "sleep"),
        ):
            returned = uart_dump.upload_loader("COM3", loader, 1, 60)

        self.assertIs(returned, port)
        self.assertEqual(
            port.writes,
            [
                struct.pack("<BH", uart_dump.ROM_SOH, len(loader)),
                loader,
                bytes([uart_dump.ROM_ACK]),
            ],
        )
        self.assertGreaterEqual(port.flush_count, 2)
        self.assertEqual(port.baudrate, 115200)
        self.assertTrue(port.dtr)
        self.assertTrue(port.rts)
        self.assertEqual(port.reset_input_count, 0)

    def test_dump_retries_when_rom_restarts_during_checksum(self) -> None:
        loader = b"\x89"
        port = FakeSerial(
            [
                bytes([uart_dump.ROM_STX]),
                bytes([uart_dump.ROM_ACK]),
                bytes([uart_dump.ROM_STX]),
                bytes([uart_dump.ROM_STX]),
                bytes([uart_dump.ROM_ACK]),
                bytes([0x89]),
            ]
        )
        with (
            mock.patch.object(uart_dump.serial, "Serial", return_value=port),
            mock.patch.object(uart_dump.time, "sleep"),
        ):
            uart_dump.upload_loader("/dev/ttyACM1", loader, 1, 60)

        header = struct.pack("<BH", uart_dump.ROM_SOH, len(loader))
        self.assertEqual(
            port.writes,
            [header, loader, header, loader, bytes([uart_dump.ROM_ACK])],
        )

    def test_flash_upload_accepts_delayed_loader_checksum_0x89(self) -> None:
        # This tests only the ROM-to-RAM loader upload.  It never reaches the
        # destructive image-transfer/erase path in gigaset_uart_flash.py.
        loader = b"\x89"
        port = FakeSerial(
            [
                bytes([uart_flash.ROM_STX]),
                b"",
                bytes([uart_flash.ROM_ACK]),
                bytes([0x89]),
            ]
        )
        with mock.patch.object(uart_flash.time, "sleep"):
            uart_flash.upload_loader(port, loader, 1, 60)

        self.assertEqual(
            port.writes,
            [
                struct.pack("<BH", uart_flash.ROM_SOH, len(loader)),
                loader,
                bytes([uart_flash.ROM_ACK]),
            ],
        )
        self.assertGreaterEqual(port.flush_count, 2)
        self.assertEqual(port.baudrate, 115200)
        self.assertTrue(port.dtr)
        self.assertTrue(port.rts)
        self.assertEqual(port.reset_input_count, 0)

    def test_flash_retries_when_rom_rejects_loader_length(self) -> None:
        loader = b"\x89"
        port = FakeSerial(
            [
                bytes([uart_flash.ROM_STX]),
                bytes([uart_flash.ROM_NAK]),
                bytes([uart_flash.ROM_STX]),
                bytes([uart_flash.ROM_ACK]),
                bytes([0x89]),
            ]
        )
        with mock.patch.object(uart_flash.time, "sleep"):
            uart_flash.upload_loader(port, loader, 1, 60)

        header = struct.pack("<BH", uart_flash.ROM_SOH, len(loader))
        self.assertEqual(
            port.writes,
            [header, header, loader, bytes([uart_flash.ROM_ACK])],
        )


if __name__ == "__main__":
    unittest.main()
