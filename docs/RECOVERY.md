# Recovering a base station's flash

This covers two situations where a base station stops working and a normal
factory reset does not fix it:

- **A wrong or mismatched CRE manifest was served to it.** The base deletes
  every Lua file its manifest does not name (see "The manifest belongs to
  your base, not to this repository" in the [README](../README.md)). If that
  happened with the wrong manifest, the base's own automation rules are
  gone from its filesystem and cannot be downloaded again - the cloud that
  used to hold them is shut down.
- **The base never reaches this gateway at all** - it boots, its status LED
  never reaches the normal "connected" state, and no HTTPS traffic to the
  gateway ever shows up in a packet capture. On at least one unit this
  turned out to be because the base's own stored client identity was
  incomplete: one flash bank held a client certificate that had been
  expired for years, and the other (active) bank held a newer private key
  and CSR but **no certificate at all** - so the base had nothing valid to
  offer as its side of the TLS handshake and never got as far as asking
  the gateway to sign one.

Both are recovered the same way: rebuild the broken filesystem content from
a **known-good donor base** of the same hardware and firmware, while
carefully preserving everything on the broken unit that makes it *that*
physical device - its MAC/RFPI, its DECT pairing data, and its own
cryptographic identity. This is a UART-level flash operation, not something
the gateway can do over the network.

> **You need a second, working base station of the same model to use as a
> donor**, plus physical UART access to both. If you only have the one
> broken unit, this procedure cannot help you - there is nothing left to
> copy the working parts from.

## Flash map

Confirmed on the Dialog SC14452 base station, 8 MiB SPI flash:

| Partition | Start | End (exclusive) | Size | Contents |
|---|---:|---:|---:|---|
| Loader | `0x000000` | `0x001000` | 4 KiB | Mask ROM loader area |
| Bootloader | `0x001000` | `0x020000` | 124 KiB | SiTelboot |
| Recovery | `0x020000` | `0x0E8000` | 800 KiB | Recovery uImage |
| Linux1 | `0x0E8000` | `0x359000` | 2,500 KiB | Linux firmware bank 1 |
| FS1 | `0x359000` | `0x46F000` | 1,112 KiB | JFFS2 bank 1 |
| Linux2 | `0x46F000` | `0x6E0000` | 2,500 KiB | Linux firmware bank 2 |
| FS2 | `0x6E0000` | `0x7F6000` | 1,112 KiB | JFFS2 bank 2 (usually the active one) |
| Factory | `0x7F6000` | `0x7FE000` | 32 KiB | MAC address, DECT RFPI |
| Env | `0x7FE000` | `0x800000` | 8 KiB | U-Boot environment (`ethaddr`, active bank) |

Both JFFS2 banks hold the same directory layout: `cert/` (TLS client key,
CSR and certificate), `cfg/`, `cre/` (rule engine libraries and rules),
`db/`, `cache/`, and the firmware payload under `fw/`. The two banks can
disagree with each other - in the case that motivated this procedure, the
inactive bank's certificate material was years older than the active
bank's key/CSR pair.

## Golden rules

1. **Full 8 MiB dump before touching anything**, on both the donor and the
   broken unit. Keep the broken unit's pre-recovery dump; it is the only
   record of what that specific device used to contain.
2. **Never write another base's image unmodified.** That clones its MAC,
   DECT RFPI and pairing data onto your hardware; two units would then
   claim the same identity.
3. **Always preserve, from the broken unit's own dump:** the Factory and
   Env partitions (`0x7F6000`-`0x800000`) verbatim, and the NVS/DECT
   pairing data inside its own JFFS2 content. Never take these from the
   donor.
4. **The UART flash tool erases and rewrites the entire chip from offset
   0.** It cannot write a single partition in isolation - build the full
   8 MiB image first, then flash it in one pass.
5. **Do not remove power once erase/write has started.** The ROM UART
   bootloader itself is masked into the chip and survives a bad write, so
   an interrupted attempt is recoverable by starting over - but the
   external flash content in between is not bootable.
6. **The chip's own post-write verification is unreliable** (it compares
   the write buffer against itself, not against the flash). Verify a write
   by taking a fresh dump and comparing its SHA-256 against the image you
   sent, or at minimum by confirming the unit boots and reaches the
   gateway normally.
7. **Never commit or publish a recovery image, a private key, or a
   `cert.key`/`cert.crt` pair.** They identify one physical device and, for
   the key material, let anyone impersonate it.

## Dumping a unit

Both the donor and the broken unit need a full dump first. This is the
same read-only procedure as extracting a manifest (see the README), just
kept as a full image instead of only the CRE manifest:

```sh
python tools/gigaset_uart_dump.py --port COM3 --output donor_flash.bin
python tools/gigaset_uart_dump.py --port COM3 --output broken_flash.bin
```

Record the size (must be exactly 8,388,608 bytes) and SHA-256 of each dump
before doing anything else.

## Inspecting the certificate material

Extract the JFFS2 content of both banks on both dumps (a JFFS2 image can be
unpacked with `jefferson`, already required for manifest extraction) and
compare what is under `cert/` in each:

- Does `cert.crt` exist, and does its validity period cover today?
- Does the certificate's public key match the public key in `cert.key` and
  `cert.csr`? A certificate that does not match the current key is stale -
  probably left over from an earlier provisioning attempt.
- Do the two banks (FS1 and FS2) even agree with each other? They are not
  guaranteed to.

Pick the **newest self-consistent key/CSR pair** as the unit's real
identity. If it already has a matching, unexpired certificate, keep all
three files as they are. If it does not, the CSR still carries the
device's real subject and public key, so a fresh certificate can be issued
for it locally:

```sh
python -c "
from pathlib import Path
import json
from gigaset_gateway import sign_client_certificate
config = json.loads(Path('gigaset_gateway.json').read_text(encoding='utf-8'))
csr = Path('cert.csr').read_bytes()
Path('cert.crt').write_bytes(sign_client_certificate(csr, config))
"
```

`sign_client_certificate()` keeps the CSR's subject and public key, issues
from this gateway's own certificate/key, sets `BasicConstraints(ca=False)`
and the `clientAuth` extended key usage, and signs with SHA-256 - the same
function the gateway itself uses to answer `POST /api/v1/bs01/sign` when a
base asks for a certificate live. Doing it up front, offline, and writing
the result into the flash image is only needed for a unit that is too
broken to ever reach that endpoint on its own.

## Building the recovery image

1. For each JFFS2 bank that needs rebuilding, start from the donor's
   extracted filesystem tree and overlay the broken unit's own `cert/`
   directory (key, CSR, certificate) and its own NVS files. A bank that is
   already fine on the broken unit does not need to be touched - it can be
   kept verbatim from the broken unit's own dump instead of being rebuilt.
2. Repackage each tree into a JFFS2 image. `mkfs.jffs2` (Debian/Ubuntu
   package `mtd-utils`) works well from a throwaway container if you do
   not want it installed on the host:

   ```sh
   docker run --rm -v "$PWD:/work" debian:bookworm-slim sh -c '
     apt-get update && apt-get install -y --no-install-recommends mtd-utils &&
     mkfs.jffs2 --root=/work/fs_root --output=/work/fs.bin \
       --eraseblock=0x2000 --pad=0x116000 --little-endian'
   ```

3. `mkfs.jffs2` targets a 8 KiB logical erase block, but this flash erases
   in 4 KiB physical blocks, and a JFFS2 node is not allowed to cross an
   erase block boundary. Repack to the real block size:

   ```sh
   python tools/repack_jffs2_4k.py fs.bin fs_4k.bin
   ```

4. Unpack the repacked image again (independently, with `jefferson`) and
   diff every file and its hash against the source tree, to catch mistakes
   before anything is written to hardware.
5. Compose the final 8 MiB image: the donor's system partitions (Loader,
   Bootloader, Recovery, both Linux banks), your rebuilt FS1/FS2, and the
   **broken unit's own** Factory and Env bytes:

   ```sh
   python tools/build_identity_preserving_image.py \
     --base1 donor_flash.bin \
     --target-dump broken_flash.bin \
     --fs2 fs2_4k.bin \
     --output recovery_image.bin
   ```

   Pass `--fs1` too if FS1 also needed rebuilding; otherwise it is kept
   from the broken unit's own dump by default. The tool asserts that the
   firmware banks still match the donor byte-for-byte and that Factory/Env
   still match the broken unit's own dump before writing anything, and
   prints the resulting MAC/`ethaddr` so you can confirm they match the
   physical unit you are about to flash.

## Known side effect: the donor's own paired-device bookkeeping comes along

The rebuilt filesystem tree is the donor's, so anything under `db/`/`cfg/`
that is not explicitly overlaid with the target's own files comes from the
donor too - including `db/endnodes`, the donor's own record of which
sensors it has paired. The recovered unit's real DECT join table (governed
by its own NVS, which *is* correctly preserved) has no idea about the
donor's sensors, but this file can still list them, and any rule that reads
it (for example a base's own "turn off every known siren" logic on an
alarm mode change) will try to command devices that were never really
paired to this physical unit and get rejected.

`delete` (Unpair) will not clean this up on its own - it acts on the live
DECT join table, not on this file, and a stale entry copied from the donor
is generally not actually present there. Use the `endnodes_cleanup`
control action instead (see the add-on's `DOCS.md`/`CHANGELOG.md`) to
remove the specific stale entries directly, no further UART step needed.

Some stale entries still show up as targets of a base's own internal
wildcard commands (for example "turn every known siren off" on an alarm
mode change) even after `endnodes_cleanup`, logged as a base-reported
`UZEL ODMÍTL`/rejected-command event repeating for the same device id. This
is cosmetic, not harmful - nothing about the add-on or the base's real
sensors breaks because of it.

**`deleteall` does not fix this - confirmed on real hardware.** It correctly
wipes the base's entire DECT join table (real sensors included - they came
back as `deleted` events and had to be paired again), but the wildcard
commands kept targeting the same stale device ids afterwards regardless.
Whatever list a wildcard command (`devId: "*"`) actually iterates is neither
`db/endnodes` nor the DECT join table `delete`/`deleteall` operate on - some
third, so far unidentified store. Until that is found, there is no known
way to make this specific log line stop; treat it as harmless and ignore
it, or if you find the real source, please report it.

## A factory reset undoes this recovery - confirmed on real hardware

**Do not factory-reset a unit that went through this procedure, unless you
are prepared to run the whole thing again.** A factory reset restores the
writable filesystem content - including the re-issued `cert.crt` and
whatever else this procedure overlaid onto the donor's tree - to some
baseline built into the flash, not to the state this procedure produced.
On the unit this happened to, the base went right back to rejecting the
gateway's certificate with `unknown_ca`, exactly the original symptom that
made this recovery necessary in the first place.

The fix is to flash the same recovery image again - there is no need to
rebuild it from scratch if you still have the one that already worked. Any
pairing done, or `endnodes_cleanup` run, after that image was flashed is
lost the same way and needs to be redone afterwards.

## Writing it back

Physically switch the target into the ROM UART bootloader the same way as
for a dump (see the README), then:

```sh
python tools/gigaset_uart_flash.py \
  --port COM3 \
  --loader 452fp.bin \
  --image recovery_image.bin \
  --confirm-erase
```

An 8 MiB write takes on the order of ten to fifteen minutes. Wait for the
tool to report that it received the chip's completion signal before
removing power or leaving ROM mode. Then power-cycle into a normal boot
and confirm the base reaches this gateway - that is a more reliable check
than the vendor's own write verification (see Golden rule 6).

## If the base still rejects the gateway's certificate after this

A base whose own certificate was never valid is more likely to reject the
gateway's self-signed server certificate with a TLS `unknown_ca` alert when
the connection negotiates TLS 1.3 - capping the server at TLS 1.2 with a
relaxed cipher policy worked around this on the unit that prompted this
procedure. The gateway already does this by default; see the
[changelog](../addon/gigaset_gateway/CHANGELOG.md) for details. The underlying
reason a fresh TLS 1.3 handshake behaves differently on this class of unit
is not fully understood - if capping the TLS version does not help on your
unit, the cause is likely something else and worth reporting.
