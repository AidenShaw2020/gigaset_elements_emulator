# Reading the DS02 firmware

`ds02` (door) and `ws02` (window) run the same single-step calibration and
answer the same `cal` command. With several spare `ds02` units and a need for
one more `ws02`, the obvious question is whether a `ds02` can simply be
relabelled - the way a `um01` can be turned into a `ws02`, see
[UM01_FIRMWARE.md](UM01_FIRMWARE.md). Reading the firmware answers that, but
not the way that question hopes for.

**No firmware image is distributed with this project**, for the same reason
as `UM01_FIRMWARE.md`: this document describes how the chip was read and what
that reading found, not the contents of the chip itself.

## What is inside a DS02

Board `W30851` / `Q2511-B101-4` - the same `W30851` reference as the `um01`
board, just paired with a different radio module. Same chip family too:
**Silicon Labs EFM32G210F128**, QFN32, rev 20 (`um01`'s was rev 144 - same
part, different die batch, same 128 KiB flash / 512 B page). Debug port
unlocked.

## Finding the debug pins

Same pins as `um01`, because it's the same package: `PF0` = chip pin 25 =
`SWCLK`, `PF1` = chip pin 26 = `SWDIO`, chip pin 9 = `RESETn`. See
[UM01_FIRMWARE.md](UM01_FIRMWARE.md#finding-the-debug-pins) for the QFN pin
counting convention and the mistake that costs hours if you get it wrong.

![Wiring used to read the sensor](docs/ds02_swd_wiring.jpg)

Wire colours in the photo, same convention as the `um01` photo: white
`SWCLK`, yellow `SWDIO`, black `GND`, red `3.3 V`.

## Reading it

Identical procedure to `um01`:

```
openocd -f interface/stlink.cfg -c "transport select swd" -f target/efm32.cfg \
  -c "adapter speed 950" \
  -c "reset_config srst_only srst_nogate connect_assert_srst" \
  -c "init" \
  -c "dump_image C:/path/to/ds02_flash.bin 0x00000000 0x20000" \
  -c "dump_image C:/path/to/ds02_devinfo.bin 0x0FE08000 0x200" \
  -c "exit"
```

A working connection reports:

```
Info : SWD DPIDR 0x2ba01477
Info : [efm32.cpu] Cortex-M3 r2p0 processor detected
Info : detected part: EFM32G Gecko, rev 20
Info : flash size = 128 KiB
```

Same two pitfalls as `um01` apply: don't use `reset halt` (use
`connect_assert_srst` and read straight through it), and use forward slashes
in the output path.

## The command vocabulary isn't in this chip

`extract_um01_commands.py` and a plain case-insensitive search both come up
empty: not one of `cal`, `ver=`, `ev=`, `nvm=`, `statall`, `chipver` appears
anywhere in the 128 KiB image, even though the node visibly answers `cal` and
sends `ver=`/`ev=calreq` over the air. The reason is architectural, not
obfuscation: this EFM32 is not the ULE command parser. It talks to a second,
separate chip - the shielded DECT ULE radio module - over `USART0` at 9600
baud, using a small binary register protocol (a length-prefixed frame, an
opcode byte for register read/write, a register index, no text anywhere).
The human-readable `cal`/`ver=`/`ev=` vocabulary lives in *that* module, not
in the one this document shows how to read.

One of those registers, index `0x16`, holds a single ASCII byte: `'d'` or
`'w'`, selecting between two state machines that are both already compiled
into this same firmware. But every reference to that register in the image is
a *read* - there is no code path anywhere that writes to it, and its default
value is `0`. It has to be written by the other chip after every reset. So
the firmware genuinely knows how to run as either a door or a window sensor,
but which one it is on a given boot is a decision this chip receives, not one
it makes or remembers.

That is also why the `um01` conversion trick doesn't transfer: there is no
`nvm=`-style command here, because there is no command parser here at all.
Relabelling a `ds02` as `ws02` would mean reverse-engineering the radio
module instead - a different chip, and out of scope for this document.

## The same bootloader trap as UM01

This bootloader checks whether `SWCLK` is held high before jumping to the
application, so an SWD probe left connected through a reset diverts the boot
into firmware-update mode instead. Disconnect the probe - or at least the
`SWCLK` wire - before power-cycling the sensor, same as `um01`.

## The result

**A `ds02` cannot be made to report as `ws02` by anything found in this
chip.** The door/window type lives in the DECT ULE radio module, which this
document does not cover. Practically: a spare `ds02` still works fine
physically mounted on a window - it just shows up in Home Assistant labelled
as a door, not a window, since that label is exactly the thing this chip
cannot change on its own.
