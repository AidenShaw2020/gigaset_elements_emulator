# Gigaset elements local gateway

Runs a local replacement for the Gigaset elements cloud. The base station keeps
its original firmware and simply talks to this add-on instead of
`api-bs.gigaset-elements.de`.

Verified against firmware `bas-002.012.002` with `ws02` (window), `ds02`
(door), `um01` (universal window/door sensor), `ps02` (motion), `bn01` (button)
and `is01` (siren) nodes.

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
| `base_id` | usually empty; overrides the key derived from the base station's address |
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

The certificate is generated on first start and kept in the add-on data
directory, so the base does not have to re-request a signature after a restart.

## What you get in Home Assistant

Every paired node appears through MQTT discovery, grouped under the base:

- window / door: contact, tilt, position, calibration state, battery
- universal sensor: contact, position, calibration state, temperature, battery
- motion: motion sensor with a configurable off delay
- button: device triggers and an `event` entity
- siren: on/off plus a sound pattern selector
- base: identifier, address, alarm state and an alarm mode selector
- buttons for pairing, unpairing, listing nodes and (re)calibration

## Control channel

Everything can be driven from the Home Assistant UI. For scripted use, requests
can also be appended to `/share/gigaset/control.json`:

```json
{
  "requests": [
    { "id": "pair-01", "action": "pair_start" },
    { "id": "cal-01", "action": "cal_reset",
      "device_type": "ws02", "device_id": "025bcab723" }
  ]
}
```

Each request runs once; the id is remembered.

## Calibration

A window or door sensor accepts `cal` **only in response to its own `calreq`**.
To force a recalibration:

1. Press *Zrušit kalibraci* (`cal_reset`).
2. Put the window in the position that should mean *closed*.
3. Wake the sensor with its button.
4. The sensor starts asking for calibration and the bundled library answers
   automatically; the current position becomes *closed*.

The universal sensor `um01` stores two positions and is never calibrated
automatically, because each step records the position it is physically in:

1. Mount the sensor and close the window or door.
2. Press *Kalibrace 1 - zavreno*.
3. Open the window or door fully.
4. Press *Kalibrace 2 - otevreno*.

The *Calibration* entity shows which step the sensor is waiting for.

## Troubleshooting

**The base never connects.** Check the clock first - without NTP the base
refuses to come online. Then verify that `api-bs.gigaset-elements.de` resolves
to the Home Assistant host and that port 443 is redirected.

**Commands are ignored.** The base must be able to reach
`http://<gateway_address>:<control_poll_port>/gwctl`. The add-on answers only
requests coming from the base station's own address.

**Entities disappear.** Nothing else may publish to `<base_topic>/availability`;
that topic carries the add-on's last will.
