# Gigaset elements local gateway

Runs a local replacement for the Gigaset elements cloud. The base station keeps
its original firmware and simply talks to this add-on instead of
`api-bs.gigaset-elements.de`.

Verified against firmware `bas-002.012.002` with `ws02` (window), `ds02`
(door), `ps02` (motion), `bn01` (button) and `is01` (siren) nodes, as well as
the `um01` universal sensor.

> **Independent, unofficial project.** Not affiliated with, endorsed by or
> supported by Gigaset. "Gigaset" and "Gigaset elements" are used only to
> identify the hardware this add-on is compatible with. Use it only on base
> stations and sensors you own or are authorised to administer.

## Before you start

### Lua files from your own base station (usually not needed)

The rule engine on the base downloads its Lua libraries from the cloud, and the
stock ones are Gigaset firmware, so they are **not** distributed here.

In practice you will probably never need them. The base only downloads a
manifest entry whose **file name changed**; everything else it keeps from the
day the original cloud provisioned it. Over a full day of live traffic this
add-on never served a single stock file - only its own four libraries.

You do need them if the base is factory reset or otherwise loses its copy. In
that case extract them from your own device and copy them to the directory in
`firmware_cre_dir` (`/share/gigaset/cre` by default):

```
/share/gigaset/cre/
    <endnode libraries>.lua
    <rules>.lua
    <internal rules>.lua
```

The add-on ships its own `gwctl`, `gwquiet`, `ws02`, `ds02` and `um01`
libraries, which take precedence over the stock ones.

### The CRE manifest (`/share/gigaset/cre_manifest.<base_key>.json`)

The manifest tells a base which Lua files to run, by exact file name. Every
base has its own: it names the versions the original cloud gave it, including
the automation rules its owner created.

Each physical base station needs its **own** manifest file, named after that
base's own address: `<base_key>` is the base's LAN IP address with dots
replaced by underscores, e.g. a base at `192.0.2.50` needs
`/share/gigaset/cre_manifest.192_0_2_50.json`. Give the base a fixed DHCP
lease so this name stays stable. This applies from the very first base
station onward, not just when adding a second one - see [Multiple base
stations](#multiple-base-stations) if you are upgrading an existing
single-base install.

Without at least one such file the add-on **refuses to start**, because a
base deletes every Lua file its manifest does not name - and the rules its
owner created cannot be downloaded again now that the cloud is gone. Sending
one base's manifest to a different base would be just as destructive, so the
add-on never falls back to serving a manifest to a base it has no file for -
that base simply keeps asking, and the add-on's log says so.

Reading it out of the base is a one-time exercise per base station;
afterwards the control library keeps the file up to date by itself. The
repository has the tools and the full procedure (`gigaset_uart_dump.py`,
`extract_base_manifest.py`); in short it is a UART dump of the flash followed
by:

```
python -m pip install jefferson
python extract_base_manifest.py flash.bin -o cre_manifest.json
```

Copy the result to `/share/gigaset/cre_manifest.<base_key>.json` for that
particular base.

It is a small JSON file with the keys `endnode_libraries`, `internal_rules`
and `rules`, and it is used exactly as it is - the add-on only injects its
own libraries and its control library.

Usually you do not have to update it by hand after the first copy. The
control library runs on the base station, so once it is loaded it sends
`/mnt/data/cfg/cre` over on its own and the add-on keeps that base's file up
to date for you.

A base that has never run the control library does need the manual copy, and
there is no way around it: **the base deletes every Lua file the manifest does
not name**, so serving it a manifest that is not its own destroys the rules its
owner created - and those cannot be fetched again now that the cloud is gone.

### Multiple base stations

Add a second (or further) base station the same way as the first: give it its
own manifest file at `/share/gigaset/cre_manifest.<base_key>.json`. All base
stations point at the same add-on, and each is identified purely by its own
LAN address, so there is nothing else to configure - sensors from every base
show up together in Home Assistant, grouped under their own base entity.

**Upgrading an existing single-base install:** older versions of this add-on
used one shared file, `/share/gigaset/cre_manifest.json`. Rename it to the
new per-base form once - `/share/gigaset/cre_manifest.<base_key>.json`, using
your base's own address - before adding a second base station. The add-on's
log names the exact file it is missing if you get the key wrong.

Scripted control requests that are not aimed at a specific sensor (pairing,
listing nodes, alarm mode - see [Control channel](#control-channel)) need an
explicit `"base"` field once more than one base station is connected, since
there is otherwise no way to tell which base should carry them out.

### Router

The base resolves exactly one hostname and always connects to port 443:

```
api-bs.gigaset-elements.de:443
```

- Point that name at the Home Assistant host (DNS override on your router).
- Redirect the base's port 443 to the add-on port (8443 by default), unless you
  set `port` to 443.
- Allow the base to reach the control port (8080 by default) on the Home
  Assistant host.
- **Redirect the base's UDP 123 to a local NTP server.** The base ignores DHCP
  option 42 and has `pool.ntp.org` hardcoded. Without a working clock it never
  comes online: the cloud LED stays dark and the sync LED blinks.

## Options

| option | meaning |
|---|---|
| `gateway_address` | usually empty; the gateway uses the address the base actually reached it on |
| `base_id` | usually empty; overrides the key derived from the base station's address, but only while a single base is connected |
| `port` | TLS port for the cloud API |
| `control_poll_port` | plain HTTP port the base polls for commands |
| `control_poll_interval` | seconds between polls; also the worst-case command latency |
| `certificate_hostnames` | DNS names in the generated certificate |
| `certificate_addresses` | extra IP addresses in the certificate |
| `mqtt_*` | leave empty to use the MQTT broker configured in Home Assistant |
| `motion_off_delay` | how long a motion sensor stays `on` after movement |
| `battery_empty_mv` / `battery_full_mv` | voltage range mapped to 0–100 % |
| `timezone` / `timezone_name` | pushed to the base station |
| `firmware_cre_dir` | where the stock Lua files are |
| `log_requests` | log request headers, for debugging only |
| `log_heartbeats` | log `EVENT HEARTBEAT` lines; the base keeps a long-poll connection open for cloud events and every empty poll (roughly every 20 s, per connection) logs one - turn off to cut most of the log volume |

The certificate is generated on first start and kept in the add-on data
directory, so the base does not have to re-request a signature after a restart.

## What you get in Home Assistant

Every paired node appears through MQTT discovery, grouped under the base:

- window / door: contact, tilt, position, calibration state, battery
- universal sensor: contact, position, calibration state, temperature, air
  pressure, battery
- motion: motion sensor with a configurable off delay
- button: device triggers and an `event` entity
- siren: on/off plus a sound pattern selector
- base: identifier, address, alarm state and an alarm mode selector
- buttons for pairing, unpairing, forgetting, listing nodes and (re)calibration

Every sensor has both an **Unpair** and a **Forget** button. Unpair tells the
base station itself to release the node and waits for it to confirm before
clearing the entity - the right choice for a sensor whose base is still
connected. Forget clears the add-on's own record and the Home Assistant
entity immediately, without contacting any base station - use it for a
sensor whose base station is no longer reachable from this add-on (replaced,
offline, or simply not one of the bases currently configured here), where
waiting for that confirmation would wait forever.

## Control channel

Everything can be driven from the Home Assistant UI. For scripted use, requests
can also be appended to `/share/gigaset/control.json`:

```json
{
  "requests": [
    { "id": "pair-01", "action": "pair_start", "base": "192_0_2_50" },
    { "id": "cal-01", "action": "cal_reset",
      "device_type": "ws02", "device_id": "025bcab723" },
    { "id": "raw-01", "action": "endnode_command", "command": "cfgclose",
      "device_type": "um01", "device_id": "0355594c4c" }
  ]
}
```

Each request runs once; the id is remembered. `endnode_command` sends whatever
is in `command` straight to the node, which is useful for trying out commands
that have no button.

`endnodes_cleanup` removes specific entries from a base's own
`/mnt/data/db/endnodes` directly on the base - `command` is a
semicolon-separated list of device ids, e.g.
`{ "id": "cleanup-01", "action": "endnodes_cleanup", "base": "192_0_2_50", "command": "027c3e0674;031c01eb41" }`.
There is no button for it because the target list is different every time
it is needed; see [RECOVERY.md](../../RECOVERY.md) for when this comes up.

Requests aimed at a specific sensor (anything with `device_type`/`device_id`)
are routed automatically to whichever base station last reported an event
from that sensor - no `"base"` needed. Requests aimed at a base station
itself (`pair_start`, `pair_stop`, `reglist`, `alarm_ack`, `mode_*`) need the
optional `"base"` field (the base_key, its address with dots replaced by
underscores) once more than one base station is connected; with a single base
it can be left out, same as before.

## Calibration

A window or door sensor accepts `cal` **only in response to its own `calreq`**,
and the add-on answers `calreq` only after the sensor's own button confirms
the position - never automatically on a timer, since that could capture
whatever position it happened to be in while still being handled or placed.
To force a recalibration:

1. Press *Zrušit kalibraci* (`cal_reset`).
2. Put the window in the position that should mean *closed*.
3. Press the button on the sensor itself. This both wakes it and confirms the
   position is correct now - the bundled library sends `cal` in response, and
   the current position becomes *closed*.

The universal sensor `um01` ships as a *umos* sensor and calibrates in two
steps that only the original cloud used to drive. Neither step is triggered
by *Zrušit kalibraci* the way a window sensor's is:

1. Put the sensor in the position that should mean *closed*.
2. Press *Kalibrace 1 - zavřeno* (`calibrate_step1`). This sends `cfgclose`,
   which restarts the sensor; on the next boot it captures the current
   position on its own, without waiting for a further command.
3. Move the sensor to the position that should mean *open*.
4. Press *Kalibrace 2 - otevřeno* (`calibrate_step2`, `cal2`).

`UM01_FIRMWARE.md` in the repository has the full explanation, including why
guessing `cal` (what a window sensor uses) never works here.

## Troubleshooting

**The base never connects.** Check the clock first - without NTP the base
refuses to come online. Then verify that `api-bs.gigaset-elements.de` resolves
to the Home Assistant host and that port 443 is redirected.

**Commands are ignored.** The base must be able to reach
`http://<gateway_address>:<control_poll_port>/gwctl`. The add-on answers only
requests coming from the base station's own address.

**Entities disappear.** Nothing else may publish to `<base_topic>/availability`;
that topic carries the add-on's last will.

**The base never even attempts a connection, and a factory reset does not
help.** This can mean the base's own stored TLS client identity (its
`cert.key`/`cert.csr`/`cert.crt`) is missing or expired on its own flash -
something a factory reset does not touch. Fixing this needs a UART-level
recovery using a second, known-good base station as a donor; see
[RECOVERY.md](../../RECOVERY.md) in the project root.
