# Reading the UM01 firmware

The `um01` universal sensor is calibrated with two ULE commands. Their names
cannot be found anywhere on the base station: every binary there (`tram`,
`coco`, `uleapp`, both kernels, recovery, both data partitions) was searched and
the only calibration command present is `cal`, in `/usr/bin/calibrate.sh`. The
base does not parse, translate or filter the command at all - it forwards the
string to the node as an opaque payload. The vocabulary therefore lives only in
the node's own firmware.

This document describes how that firmware was read, so the result can be
verified or repeated on other node types.

**No firmware image is distributed with this project.** Only the command names
are, and those are facts needed for interoperability.

## What is inside a UM01

Board `337632-1` / `W30851` / `Q2526-B101-1`, powered by a CR123A cell. There
are two processors:

- a shielded DECT ULE radio module, labelled `Q2526-B101`
- a **Silicon Labs EFM32G Gecko** in a QFN32 package - the application MCU

The EFM32 runs the sensor logic and holds the command parser. It is an ARM
Cortex-M3 with a standard SWD debug port. On the unit examined here the debug
port was **not locked**, so the flash could be read without modifying anything.

## Finding the debug pins

The row of four tinned pads on the underside is **not** the debug port. Two of
them are ground and supply, the other two lead elsewhere.

SWD is on the chip itself. For the whole EFM32 family the debug pins are `PF0`
and `PF1`; on this QFN32 package that is:

| chip pin | function |
|---|---|
| 25 | `PF0` = `SWCLK` |
| 26 | `PF1` = `SWDIO` |
| 9 | `RESETn` |

> QFN pins are counted **anticlockwise** from the index dot: left edge 1-8,
> bottom edge 9-16, right edge 17-24, and the **top edge 25-32 from right to
> left**. Counting the top edge left to right lands on pins 32 and 31, which are
> ordinary GPIO and behave exactly like a dead debug port. This is the single
> most likely mistake and it costs hours.

Ground and 3.3 V can be taken from pads 3 and 4 of that row on the underside;
remove the battery first so the two supplies cannot fight. `SWCLK`, `SWDIO` and
`RESETn` are reachable from vias on the underside that lead to chip pins 25, 26
and 9, which is easier to solder than the 0.5 mm pitch of the package itself.

![Wiring used to read the sensor](docs/um01_swd_wiring.jpg)

Wire colours in the photo:

| colour | signal |
|---|---|
| white | `SWCLK` (chip pin 25) |
| yellow | `SWDIO` (chip pin 26) |
| orange | `RESETn` (chip pin 9) |
| black | `GND` (pad 3) |
| red | `3.3 V` (pad 4) |

## Reading it

Any SWD probe works. Both an ST-Link V2 and a Raspberry Pi Pico running
`debugprobe_on_pico.uf2` were tried; the ST-Link is shown here because it can
drive the reset line.

Check that the target answers first. Nothing is written by this command:

```
openocd -f interface/stlink.cfg -c "transport select swd" -f target/efm32.cfg \
  -c "adapter speed 100" \
  -c "reset_config srst_only srst_nogate connect_assert_srst" \
  -c "init" -c "targets" -c "flash probe 0" -c "exit"
```

A working connection reports the part and the flash size:

```
Info : SWD DPIDR 0x2ba01477
Info : [efm32.cpu] Cortex-M3 r2p0 processor detected
Info : detected part: EFM32G Gecko, rev 144
Info : flash size = 128 KiB
```

Then dump it:

```
openocd -f interface/stlink.cfg -c "transport select swd" -f target/efm32.cfg \
  -c "adapter speed 950" \
  -c "reset_config srst_only srst_nogate connect_assert_srst" \
  -c "init" \
  -c "dump_image C:/path/to/um01_flash.bin 0x00000000 0x20000" \
  -c "dump_image C:/path/to/um01_devinfo.bin 0x0FE08000 0x200" \
  -c "exit"
```

Two things that waste time:

- **Do not use `reset halt`.** `connect_assert_srst` keeps the chip in reset, so
  the halt request times out. Memory reads work while the chip is held in reset,
  so `init` followed by `dump_image` is enough.
- **Use forward slashes in the output path.** OpenOCD runs the argument through
  Tcl, which eats backslashes.

Sanity-check the result before unsoldering anything: the first word of the image
is the initial stack pointer and should be inside SRAM (`0x2000xxxx`), the
second is the reset vector and should be an odd address below the flash size.

`connect_assert_srst` also covers the case where the application firmware turns
the debug pins off after boot, because the chip never gets to run.

## Extracting the command table

```
python extract_um01_commands.py um01_flash.bin
```

The commands are plain ASCII, laid out in one table next to the event names.
On the examined firmware the table sits at `0x172ec`:

```
recal  sleep  cal    cal2   chipver  do-reset  temp   press
sys1   tim1   tim2   tilt   mag      statall   cfgcrc cfgclose

scfg=  dbg=   dbg=dumpall  cfg-def=  cfg-str=  nvm=   statedbg=
buz=   um-awake=  factcfg=  cfgx=  cfgy=  cfgz=  cfgw=
```

And the events the node sends:

```
ev=open  ev=close  ev=tilt  ev=preopen  ev=button  ev=button1  ev=button2
ev=calreq  ev=cal1req  ev=cal1started  ev=cal1done
ev=cal2started  ev=cal2done  ev=caldone  ev=calreused  ev=CaliDel
ev=dmon  ev=dmoff  ev=initdone  ev=ready  ev=cfgconfirm
ev=prealert  ev=forcedentry  ev=drillalert  ev=recalrec
```

## The result

The first calibration step is confirmed with **`cal`**, the second with
**`cal2`**, and `recal` discards the calibration.

The string `cal1` exists in the firmware only inside event and state names
(`ev=cal1req`, `ev=cal1started`, `cal=cal1done`). **As a command it does not
exist**, which is why guessing it from the event name never worked. The same
asymmetry exists on `ws02`, where the phone app sends `closed` but the node
receives `cal`.
