#!/usr/bin/env python3
"""Vytahnout CRE manifest z dumpu flash pameti zakladny.

Manifest (`/mnt/data/cfg/cre`) rika zakladne, ktere Lua soubory ma nacist, a na
kazde zakladne je jiny: jsou v nem verze, ktere ji nadelil puvodni cloud, vcetne
pravidel zalozenych jejim majitelem.  Brana ho potrebuje, aby zakladne mohla
podstrcit vlastni knihovny a neprisla pritom o nic, co uz na sobe ma - polozku,
kterou v prijatem manifestu nenajde, zakladna ze sveho disku smaze.

Data lezi ve dvou oddilech JFFS2 (aktivni a predchozi).  Tenhle skript je z
obrazu vyrizne a rozbali nastrojem `jefferson` (`pip install jefferson`).
Vysledkem je soubor, ktery staci polozit do `/share/gigaset/cre_manifest.json`.

    python extract_base_manifest.py flash.bin -o cre_manifest.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# Rozlozeni flash pameti zakladny bas-002.012.002 (MX25L6405D, 8 MiB).
DATA_PARTITIONS = (("fs1", 0x00359000, 1112 * 1024), ("fs2", 0x006E0000, 1112 * 1024))
JFFS2_MAGIC = b"\x85\x19"


def carve(image: bytes, directory: Path) -> list[Path]:
    """Vyriznout z obrazu oddily, ktere opravdu vypadaji jako JFFS2."""
    carved: list[Path] = []
    for name, offset, size in DATA_PARTITIONS:
        chunk = image[offset : offset + size]
        if not chunk.startswith(JFFS2_MAGIC):
            print(f"{name}: na offsetu {offset:#x} neni JFFS2, preskakuji")
            continue
        target = directory / f"{name}.bin"
        target.write_bytes(chunk)
        carved.append(target)
    return carved


def unpack(partition: Path, directory: Path) -> list[Path]:
    """Rozbalit oddil a vratit vsechny nalezene manifesty."""
    output = directory / f"{partition.stem}_root"
    try:
        subprocess.run(
            ["jefferson", "-d", str(output), str(partition)],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        raise SystemExit(
            "Chybi nastroj 'jefferson'. Nainstalujte ho prikazem:\n"
            "    python -m pip install jefferson"
        )
    except subprocess.CalledProcessError as error:
        print(f"{partition.name}: jefferson skoncil s chybou {error.returncode}")
        return []
    return sorted(output.rglob("cre"))


def load(path: Path) -> dict[str, Any] | None:
    if path.parent.name != "cfg":
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or "endnode_libraries" not in manifest:
        return None
    return manifest


def entry_count(manifest: dict[str, Any]) -> int:
    return sum(len(value) for value in manifest.values() if isinstance(value, dict))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="dump flash pameti zakladny")
    parser.add_argument("-o", "--output", type=Path, default=Path("cre_manifest.json"))
    arguments = parser.parse_args()

    image = arguments.image.read_bytes()
    found: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as workspace:
        directory = Path(workspace)
        partitions = carve(image, directory)
        if not partitions:
            raise SystemExit(
                "V obrazu nejsou zadne datove oddily. Je to dump cele flash "
                "pameti zakladny?"
            )
        for partition in partitions:
            for candidate in unpack(partition, directory):
                manifest = load(candidate)
                if manifest is None:
                    continue
                print(
                    f"{partition.stem}: "
                    + ", ".join(
                        f"{name} {len(value)}"
                        for name, value in sorted(manifest.items())
                        if isinstance(value, dict)
                    )
                )
                found.append(manifest)

    if not found:
        raise SystemExit("Manifest se v obrazu nepodarilo najit.")

    # Oddily se pri aktualizaci stridaji, takze ten obsahlejsi je ten zivy.
    best = max(found, key=entry_count)
    arguments.output.write_text(
        json.dumps(best, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"ulozeno do {arguments.output} ({entry_count(best)} polozek)")


if __name__ == "__main__":
    main()
