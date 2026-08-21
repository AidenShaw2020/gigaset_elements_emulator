#!/usr/bin/env python3
"""Vytahne z dumpu firmwaru uzlu retezce, ktere vypadaji jako ULE prikazy.

ULE prikazy jsou u techto senzoru obycejne ASCII (`cal`, `verreq`, `sirenon`) a
zakladna je jen prepolsi, takze v dumpu firmwaru uzlu lezi v citelne podobe.
Postup, jak dump porizet, je v UM01_FIRMWARE.md.

    python extract_um01_commands.py um01_flash.bin
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

STRING_RE = re.compile(rb"[\x20-\x7e]{3,}")

# Prikazy jsou bud holy retezec, nebo maji parametr za "=".
COMMAND_SHAPE = re.compile(r"^[a-z][a-z0-9_-]{2,15}=?$")

# Udalosti a stavy, ktere uzel naopak sam odesila.
EVENT_RE = re.compile(r"^(ev|cal|state|err)=")

CALIBRATION_RE = re.compile(
    r"(?<![a-z])(cal\w*|pos\w*|ref\w*|learn\w*|teach\w*|dm(on|off))(?![a-z])"
)


def strings(data: bytes) -> list[tuple[int, str]]:
    return [(m.start(), m.group().decode("ascii")) for m in STRING_RE.finditer(data)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="dump flash pameti uzlu")
    parser.add_argument(
        "--window",
        type=int,
        default=0,
        help="o kolik bajtu rozsirit oblast s tabulkou prikazu na obe strany",
    )
    arguments = parser.parse_args()

    data = arguments.image.read_bytes()
    found = strings(data)
    print(f"{arguments.image.name}: {len(data)} B, {len(found)} retezcu\n")

    events = [(offset, text) for offset, text in found if EVENT_RE.match(text)]
    if not events:
        raise SystemExit(
            "V obrazu nejsou zadne retezce tvaru 'ev=...'. Je to dump firmwaru uzlu?"
        )

    # Prikazy leziv v tabulce hned za udalostmi, protoze je preklada tentyz kod.
    first = events[0][0]
    last = events[-1][0]
    print("=== udalosti a stavy, ktere uzel odesila ===")
    for offset, text in events:
        print(f"0x{offset:05x}  {text}")

    print("\n=== prikazy, ktere uzel prijima ===")
    for offset, text in found:
        if first - arguments.window <= offset <= last + arguments.window:
            if COMMAND_SHAPE.match(text) and not EVENT_RE.match(text):
                mark = "  <-- kalibrace" if CALIBRATION_RE.search(text) else ""
                print(f"0x{offset:05x}  {text}{mark}")


if __name__ == "__main__":
    main()
