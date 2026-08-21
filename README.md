# Local gateway for Gigaset elements

A self-hosted replacement for the Gigaset elements cloud. The base station keeps
its original firmware and simply talks to this gateway instead of
`api-bs.gigaset-elements.de`.

Runs either as a **Home Assistant add-on** or as a plain Python script.

Verified on live hardware against firmware `bas-002.012.002` (Dialog SC14452)
with sensor types `ws02` (window), `ds02` (door), `ps02` (motion), `bn01`
(button), `is01` (siren) and `um01` (universal sensor, including its
two-step calibration - see below).

> **Independent, unofficial project.** Not affiliated with, endorsed by or
> supported by Gigaset. "Gigaset" and "Gigaset elements" are used only to
> identify the hardware this gateway is compatible with. Use it only on base
> stations and sensors you own or are authorised to administer.

## What works

- The base accepts a self-signed certificate; it does not pin or validate the
  chain. Certificate signing requests are answered locally.
- Every `state`, `ev`, `ba`, `dev` and `ver` sink message is acknowledged,
  logged and published over MQTT.
- The CRE rule engine is served from local files, so the Lua libraries running
  on the base can be modified.
- Full Home Assistant integration through MQTT discovery: contacts, tilt,
  position, calibration state, battery, motion, buttons, siren and alarm modes.
- Pairing, unpairing, node listing and forced (re)calibration.
- Commands reach the base in about a second.

## Requirements

- Python 3.11+ and the packages in `requirements.txt` (the add-on brings its
  own)
- A host with a fixed address that the base can reach
- An MQTT broker, if you want the Home Assistant integration
- A router that can override DNS and redirect traffic (the examples use
  OpenWrt, but any dnsmasq-based system works for the DNS part)
- **The Lua files from your own base station** - see below

## Firmware files are not included

The rule engine on the base downloads its Lua libraries from the cloud. The
stock libraries are Gigaset firmware and are therefore **not** part of this
repository.

In practice you will probably never need them. The base only downloads a
manifest entry whose **file name changed**; everything else it keeps from the
day the original cloud provisioned it. On a base station that has been through
the Gigaset cloud, this gateway only ever serves its own four libraries -
verified over a full day of live traffic, during which not a single stock file
was requested.

You do need them if the base is factory reset or otherwise loses
`/mnt/data/cre`. Extract them from your own device and put them in the
directory listed in `cre_source_dirs`; a missing file is answered with 404 and
logged.

### The manifest belongs to your base, not to this repository

The CRE manifest names the Lua files the base should run, by exact file name,
and **every physical base station has a different one**: it lists the
versions the original cloud gave it, including the automation rules its
owner created. Running more than one base station against the same add-on
means repeating this whole section once per base.

**A base deletes every Lua file its manifest does not name.** Serving it anyone
else's manifest therefore destroys rules that, with the cloud gone, cannot be
downloaded again. The add-on refuses to start without your own manifest, and
the gateway will not ask the base to re-read its configuration until it has it.

Once the control library is running it sends `/mnt/data/cfg/cre` over by itself,
so this is a one-time exercise per base station. To get there the first time
you need to read that base's flash:

1. Disconnect power, bridge GND and UTX on the 3-pin header, connect power,
   remove the bridge, then attach a 3.3 V UART adapter. All LEDs stay dark and
   the mask ROM starts emitting `STX` at 9600 baud. The header is, component
   side with the ethernet jack down, left to right: **URX / GND / UTX**.
2. `python tools/gigaset_uart_dump.py --port COM3 --output flash.bin`
   The dump is read-only - the tool never sends an erase or program command.
   Eight megabytes take about thirteen minutes.
3. `python -m pip install jefferson`
4. `python tools/extract_base_manifest.py flash.bin -o cre_manifest.json`
5. Copy the result to `/share/gigaset/cre_manifest.<base_key>.json` (add-on) or
   point `cre_manifest_file` at it (script) - `<base_key>` is that base's own
   LAN IP address with dots replaced by underscores (`192.0.2.50` becomes
   `cre_manifest.192_0_2_50.json`), matching how the add-on identifies each base
   station everywhere else. Give the base a fixed DHCP lease so this stays
   correct. With only one base station this is the only manifest file that
   exists, but it still needs the `<base_key>` suffix.

Keep the dump. It is the only backup of the rules on that base, and the Lua
files in it are what you would need after a factory reset.

Shipped here are only the four libraries written for this project:

| file | purpose |
|---|---|
| `gwctl` | control channel; generated at runtime |
| `gwquiet` | silences `debug` and `info` logging, see [Throughput](#throughput) |
| `ws02`, `ds02` | automatic answer to a sensor's calibration request |
| `um01` | delivery of the two calibration steps of the universal sensor |

## How it works

The base resolves one hostname and opens a TLS connection to it:

```
api-bs.gigaset-elements.de:443
```

Point that name at the gateway and answer with a self-signed certificate. The
base does the rest by itself.

Two details are easy to miss and both prevent the base from coming online:

1. **The base always connects to port 443.** If 443 is taken on the gateway
   host, run the gateway on another port and let the router rewrite the
   destination port.
2. **The base ignores DHCP option 42 and has NTP servers hardcoded**
   (`{at,ch,de,europe,fr,nl,se}.pool.ntp.org`). Without a working clock it
   reports `cloud_nok`, the cloud LED stays dark and the sync LED blinks. If the
   base is on an isolated network, redirect its UDP 123 traffic to a local NTP
   server. Redirecting the traffic is better than overriding `pool.ntp.org` in
   DNS, which would affect every other client on the network.

## Setup as a Home Assistant add-on

This is an add-on repository, not a HACS repository - HACS does not distribute
add-ons. Install it through the Supervisor instead:

1. Add this repository under **Settings → Add-ons → Add-on store → ⋮ →
   Repositories**.
2. Install *Gigaset elements local gateway*.
3. Leave the MQTT options empty to use the broker Home Assistant already knows
   about. The remaining defaults are usually fine.
4. Configure the router as described below and start the add-on.

The certificate is generated on first start, and the address the base uses for
the control channel is taken from the first connection it makes. See the add-on
documentation for the full option list.

## Setup as a script

```sh
python -m pip install -r requirements.txt
python generate_certificate.py --dns api-bs.gigaset-elements.de --ip <GATEWAY_IP>
cp gigaset_gateway.example.json gigaset_gateway.json
python gigaset_gateway.py --config gigaset_gateway.json
```

Both certificate options may be repeated. A wildcard is only possible for DNS
names; X.509 has no wildcard for IP addresses, so list every address the gateway
may ever use.

The base connects within a minute. Look for `EVENT HEARTBEAT` lines.

## Router

DNS override:

```
address=/api-bs.gigaset-elements.de/<GATEWAY_IP>
```

Port rewrite, only needed when the gateway does not listen on 443:

```
config redirect
    option name      'gigaset-api'
    option target    'DNAT'
    option proto     'tcp'
    option src       '<BASE_ZONE>'
    option src_ip    '<BASE_IP>'
    option src_dip   '<GATEWAY_IP>'
    option src_dport '443'
    option dest_ip   '<GATEWAY_IP>'
    option dest_port '8443'
```

Access to the control port, needed when the base is on a separate network:

```
config rule
    option name      'gigaset-control'
    option target    'ACCEPT'
    option proto     'tcp'
    option src       '<BASE_ZONE>'
    option src_ip    '<BASE_IP>'
    option dest      '<GATEWAY_ZONE>'
    option dest_ip   '<GATEWAY_IP>'
    option dest_port '8080'
```

NTP redirect, needed when the base has no route to the internet:

```
config redirect
    option name      'gigaset-ntp'
    option target    'DNAT'
    option proto     'udp'
    option src       '<BASE_ZONE>'
    option src_ip    '<BASE_IP>'
    option src_dport '123'
    option dest_ip   '<ROUTER_IP_IN_BASE_VLAN>'
    option dest_port '123'
```

## Isolating the base

The base only needs DHCP, DNS, NTP and the gateway. Blocking its route to the
internet is recommended, otherwise a DNS failure could let it reach the real
cloud and install a firmware update that undoes this work.

| direction | protocol | port | target |
|---|---|---|---|
| base → router | UDP | 67 | DHCP |
| base → router | TCP+UDP | 53 | DNS |
| base → router | UDP | 123 | NTP |
| base → gateway | TCP | 443 | cloud API |
| base → gateway | TCP | 8080 | control channel |

## Home Assistant entities

Every paired node is announced through MQTT discovery and grouped under the
base station.

| node | entities |
|---|---|
| `ws02`, `ds02` | contact, tilt, position, calibration state, battery, calibrate / reset calibration buttons |
| `um01` | contact, position, calibration state, temperature, air pressure, battery, two calibration step buttons and a reset calibration button |
| `ps02` | motion with a configurable off delay, battery |
| `bn01` | device triggers and an `event` entity, battery |
| `is01` | siren on/off, sound pattern selector |
| base | identifier, address, alarm state, alarm mode selector, pairing and node listing buttons |

## Control channel

Everything is available from the Home Assistant UI. For scripted use, requests
can also be appended to the file named by `control.request_file`:

```json
{
  "requests": [
    { "id": "pair-01", "action": "pair_start" },
    { "id": "list-01", "action": "reglist" },
    { "id": "cal-01", "action": "cal_reset",
      "device_type": "ws02", "device_id": "025bcab723" }
  ]
}
```

| action | effect |
|---|---|
| `pair_start` / `pair_stop` | open and close the DECT registration window |
| `reglist` | log the registered nodes |
| `unpair` | remove a node |
| `calibrate` / `cal_reset` | send `cal` / `recal` to a node |
| `calibrate_step1` / `calibrate_step2` | send `cfgclose` / `cal2` to a `um01` node |
| `endnode_command` | send the command in the request's `command` field to a node |
| `siren_on` / `siren_off` | sound the siren |
| `pattern_*` | play a sound pattern |
| `mode_home`, `mode_away`, `mode_night`, `mode_custom` | switch the alarm mode |

Each request is executed once; the id is remembered. No restart is needed after
editing the file.

### How commands actually reach the base

Three mechanisms exist on the base and only one of them is usable.

- Writing to the `ulecontrol` JBus topic (what `/usr/bin/sender` does) is
  handled by UleApp, which compares the command against its own table
  (`regon`, `regoff`, `reglist`, `delete`, …) and **silently drops anything
  else**. The stock `/usr/bin/calibrate.sh` writes `{"cmd":"cal"}` this way and
  therefore never did anything.
- The cloud message types `cre-event` and `rcsh` are accepted by the base but
  produce no effect whatsoever - no rule engine callback, no log, nothing. Only
  `heartbeat` and `configuration-changed` do anything over that channel.
- Commands for an end node must go through the CRE function
  `ule_command_send(devType, devId, command)`, which means running code inside
  the rule engine.

Delivering that code as a new Lua library costs a full rule engine reload, some
16 to 26 seconds. Instead, the `gwctl` library stays loaded permanently and
polls the gateway over plain HTTP for pending commands, so the latency is just
the poll interval.

## Calibration of window and door sensors

Event vocabulary observed on live hardware:

| payload | meaning |
|---|---|
| `calreq` | the sensor is uncalibrated and is asking to be calibrated |
| `caldone` | calibration succeeded |
| `open` / `close` / `tilt` | position, only reported after calibration |
| `button` | pairing button |

A sensor accepts `cal` **only in response to its own `calreq`**. Outside of that
window it ignores the command. To force a recalibration:

1. Queue a `cal_reset` request (`recal`).
2. Put the window into the position that should mean *closed*.
3. Wake the sensor with its button. The command is delivered and the
   calibration is discarded.
4. The sensor starts sending `calreq`; the bundled `ws02` / `ds02` libraries
   answer with `cal` automatically and the current position becomes *closed*.

Because ULE nodes sleep most of the time, the gateway writes a marker file
(`/tmp/gwarm.<devId>`) alongside the immediate attempt. The library sends the
command again the moment the node reports anything, which is the only reliable
point at which it is awake.

`moving` and `closed` appear in the original Android app but they are **cloud
API state names, not ULE commands** - a `ws02` or `ds02` sensor does not react
to them.

## Calibration of the universal sensor

An `um01` ships as a *umos* sensor and calibrates in two steps that only the
original cloud used to drive. Neither step is the `cal` command a window
sensor uses - the sensor's own firmware ignores `cal` entirely while it is
configured as `um`, which is why guessing a step from the event names never
worked.

| payload | meaning |
|---|---|
| `cal1req` / `cal2req` | the sensor is asking for the first / second step |
| `cal1done` / `cal2done` | that step succeeded |
| `open` / `close` / `tilt` / `preopen` | position, only reported after both steps |

The first step is a side effect of `cfgclose`, not a command that answers a
request: it restarts the sensor, and the restarted firmware captures whatever
position it is in as the *closed* reference on its own, a few seconds later.
The second step is the familiar `cal2`:

1. Put the sensor in the position that should mean *closed*.
2. Queue a `calibrate_step1` request (`cfgclose`) and wake the sensor with its
   button. It restarts and reports `ev=cal1started` / `ev=cal1done`.
3. Move the sensor to the position that should mean *open*.
4. Queue a `calibrate_step2` request (`cal2`) and wake the sensor again.

[docs/UM01_FIRMWARE.md](docs/UM01_FIRMWARE.md) documents how this was found, including
how to read the sensor's firmware and the command table recovered from it -
and a caveat for anyone repeating the readout with a debug probe still
attached during the restart.

## Siren

`sirenon` and `sirenoff` are the ULE commands. Nodes with firmware `00250000`
or newer also accept sound patterns:

```
pattern=<total duration>,<sequence>
```

The sequence alternates tone and silence; the letter is the time unit
(`m`=1 ms, `c`=10 ms, `d`=0.1 s, `s`=1 s, `a`=10 s, `h`=100 s, `k`=1000 s). The
patterns taken from the stock rules are `1s,4d1d5d` (doorbell), `2h,2D2d`
(alarm), `3s,3S` (test) and `9s,4C2s` (reminder). `patternoff` stops playback.

Commands go straight to the node, bypassing the base station's priority
manager, so a running alarm may override a manual command.

## Throughput

The base opens a **new TLS connection for every message** and drains its queue
at roughly one message per second. The gateway itself answers in about 30 ms,
so the limit is entirely on the base.

Out of the box, a large part of that budget was spent on `debug` and `info`
messages from the rule engine, which delayed sensor states by 10 to 30 seconds.
The bundled `gwquiet` library replaces those two entry points of the stock
`cloudLog` table with no-ops once the rule engine has started, which brings the
delay down to 0 to 3 seconds. `warn` and `error` are left untouched.

## Output

- `gigaset_events.jsonl` - every incoming request
- `gigaset_state.json` - last known state per device
- MQTT topics under `gigaset/`

## Safety notes

- Never run anything slow in the load path of a CRE library. `os.execute`
  blocks the whole Lua VM; if that happens during boot the watchdog resets the
  base and the loop can only be broken over UART.
- The base station has a long historical event queue. Leave the gateway running
  until it has been acknowledged up to the present.
- Nothing except the gateway may publish to `<base_topic>/availability`; that
  topic carries the gateway's MQTT last will, and a stray `offline` makes every
  entity disappear from Home Assistant.
- Test pad 2 on the board carries 6.5 V.

## Recovering a broken base station

If a base lost its own Lua rules to a wrong manifest, or never reaches this
gateway at all because its own stored TLS identity is missing or expired,
see [docs/RECOVERY.md](docs/RECOVERY.md) - a UART-level procedure that rebuilds the
broken unit's flash from a second, known-good base station while preserving
the broken unit's own MAC, DECT pairing data and cryptographic identity.

## Roadmap

- Multi-base support
- Pattern editor instead of a fixed list
- Optional recording of the base station's own diagnostic log uploads

## How this was built

Everything here was written from scratch. The protocol was worked out by
observing a base station and sensors owned by the author: network captures of
the traffic to the original cloud, the base station's own diagnostic log
uploads, and analysis of firmware and of the vendor's Android application,
both obtained through legitimate channels. Only what was needed to keep the
hardware working without the cloud was reproduced.

No vendor source code is contained in this repository, and none is needed to
run the gateway.

## License

See `LICENSE`. The Lua files extracted from a base station are Gigaset firmware
and are not covered by it.
