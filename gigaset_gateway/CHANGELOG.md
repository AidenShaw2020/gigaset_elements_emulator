# Changelog

## 1.0.26

- Stop the control library from depending on the rule engine's logging module.
  While a base station is being bootstrapped that module is not loaded, so
  every log line raised an error that was caught and thrown away - taking the
  rest of the work with it.

## 1.0.25

- Fix the generated control library, which did not compile: the shell snippet
  it writes was escaped once too few, so its string literals ended in the
  middle of a line. The rule engine then refused the whole configuration and
  the base station kept re-reading it. The snippet is now a Lua long string and
  needs no escaping at all.

## 1.0.24

- Fix the add-on failing to start: the flag that decides whether the base
  station needs bootstrapping was read without ever being set.

## 1.0.23

- Always report what the base station says its rule engine configuration looks
  like, even when the stored manifest is kept as it is.

## 1.0.22

- Say plainly that calibrating a `um01` does not work yet. The sensor reports
  its battery, temperature, firmware and its mounting and button events, but
  never a position.

## 1.0.21

- Work out the CRE manifest of a base station that has never talked to this
  add-on, so nobody has to read it out of the flash. On a first start with
  nothing to go on, the base is served only the files the add-on can actually
  provide - enough for it to accept the configuration and load the control
  library. That library then lists `/mnt/data/cre`, and since a manifest is
  nothing but a list of file names, the real one is rebuilt from the listing
  and applied straight away. Verified against a real base: all 54 entries came
  out identical to its own `/cfg/cre`.

## 1.0.20

- Fetch the CRE manifest from the base station itself. The control library now
  sends `/cfg/cre` to the add-on once per rule engine load, so nobody has to
  read it out of the flash to find out which Lua files their base expects. It
  is stored as `/share/gigaset/cre_manifest.json` and used from the next start;
  a file that is already there is never overwritten.

## 1.0.19

- Take the CRE manifest from `/share/gigaset/cre_manifest.json` when it exists.
  The manifest names the exact Lua files a base has to run, so it differs from
  base to base - it carries the versions the original cloud handed out plus the
  owner's own automation rules. Until now every installation got the one from
  the developer's base, which on any other base names files nobody can serve.
  Whatever the source, only the add-on's own libraries are injected into it.

## 1.0.18

- Search the `um01` calibration command systematically instead of guessing a
  handful of names. The command is not present anywhere in the base station's
  flash, the node answers `verreq` but says nothing about commands it does not
  understand, so the only remaining option is to walk the space defined by the
  grammar the firmware uses elsewhere: a bare word, `set=<property>,<value>`
  and `mset=<a>;<b>`. One candidate per request, 180 in total.

## 1.0.17

- Log what the base station reports on `/api/v1/bs/sink/unknown`. That is where
  it forwards a command a node did not understand, which is the only way to
  tell a rejected command apart from one that was accepted and did nothing.

## 1.0.16

- Log the whole message for node events that carry no `payload`. The `sink/dev`
  announce, which is where a node describes itself, is exactly such a message,
  so its contents used to end up in the event file and nowhere else.

## 1.0.15

- Third round of candidates for the `um01` calibration command, this time
  following the DECT naming the firmware is built on: a request is answered by
  a confirm, so `cal1req` should be answered by `cal1cfm` or `cal1res`. The
  node replies to `verreq` within a second, so it does accept commands and only
  the name is wrong.

## 1.0.14

- Second round of candidates for the `um01` calibration command. `cal`, `cal1`,
  `cal=1`, `set=cal,1`, `calibrate` and `startcal` were all ignored by the node
  on live hardware. The list now starts with `verreq`, which the node has to
  answer, so it also shows whether the node accepts any command at all.

## 1.0.13

- Find the calibration command of the `um01` sensor by trying it. The name is
  not recoverable from the base station - the command is only text that gets
  forwarded over DECT and the node's firmware is what interprets it - and the
  obvious `cal1` turned out to be wrong on live hardware. After the user asks
  for a calibration step, the library now tries one candidate per request from
  the sensor and logs which one the node finally accepted.

## 1.0.12

- Name the manifest entries that cannot be served at start-up. A missing file
  is answered with 404 and the base station then keeps re-reading its
  configuration without ever confirming it, which looks like nothing happening
  at all.

## 1.0.11

- Ask the base station to re-read its configuration whenever the CRE manifest
  changes. Until now that only happened when the `gwctl` library changed, so a
  library added by an add-on update was served but never actually requested -
  the base only reads the manifest when it is told to.

## 1.0.10

- Log the messages the base station's Lua libraries produce (`CRE WARN …`).
  The rule engine has no other output - the serial console is disabled in the
  firmware - so until now there was no way to tell whether a library was even
  loaded, let alone what it did with a command.
- New control action `endnode_command`, which sends a free-form command to a
  node. The ULE command vocabulary is not documented anywhere and the strings
  live in the node's firmware, not in the base station, so trying a command is
  the only way to verify it.

## 1.0.9

- Support the `um01` universal window/door sensor. The stock library only
  handles temperature, so nothing ever answered the sensor's `cal1req` and it
  asked for calibration forever. The add-on now ships its own `um01` library
  and exposes contact, position, calibration state and battery, plus the two
  calibration buttons. Both steps are deliberately manual: the first stores the
  closed position and the second the open one, so the sensor has to be moved in
  between.
- Publish the temperature a node reports on its `tp` or `state` sink as a
  temperature entity instead of leaving it in *Last event*.
- Merge the shipped CRE manifest into the one kept in `/data` on every start.
  It used to be copied only on the very first run, so a library added by an
  add-on update never reached the base station.

## 1.0.7

- Replace the bundled `cloudLog` library with `gwquiet`. The rule engine's
  `debug` and `info` messages are now silenced by swapping those two entries of
  the stock `cloudLog` table once the engine has started, so the stock library
  itself stays in place and is never downloaded or shipped. `warn` and `error`
  keep working, including their source location.

## 1.0.6

- Fix the control channel, which never worked in the add-on: with
  `gateway_address` left empty the base station was told to fetch its commands
  from `http://null:8080/gwctl`. `bashio::config` reports an unset option as the
  string `null`, which is not empty, so the automatic address detection was
  skipped. Every button press and every alarm mode change was queued and never
  collected.
- Log the first successful poll from the base and warn when a command is queued
  while the base has never asked for one. An empty queue is served silently, so
  a broken control channel used to look exactly like nothing happening.

## 1.0.5

- Add a "Zrusit poplach" button to the base station. A bump alarm raised in
  `home` mode could not be silenced from Home Assistant at all: `home` only
  reacts to shock events, so switching to another mode was the sole way out.
  The button sends the `alarm.intrusion.ack` CRE event, which ends the
  intrusion and stops the siren. An earlier attempt at this event went
  unanswered because it was sent without a payload argument - the firmware
  always passes one, and without it the event is never dispatched.
- Stop the alarm mode select from snapping back to its previous value. The
  default mode was republished on every single request from the base, so it
  overwrote the user's choice long before the base could confirm the change.
  It is now published only once per base station.

## 1.0.4

- Keep a single base station device in Home Assistant. The device used to be
  keyed by the identifier from the certificate signing request, which the base
  sends once every few years - a second gateway never saw it, fell back to the
  address and published a second base with the sensors split between the two.
  The key is now always the base station's address, which every gateway knows
  from the first request, and the old identifier-keyed device is removed. The
  identifier is still shown by the "Base identifier" entity, and the new
  `base_id` option overrides the key if the base reaches each gateway from a
  different address.

## 1.0.3

- Clear the alarm state when the mode changes to anything but `away`. The base
  acknowledges a running intrusion at that point but sends no end event of its
  own, so the alarm could stay latched forever - in particular if the gateway
  was not running when the alarm ended.

## 1.0.2

- Clean up the base station device when its name changes. Until the base has
  its certificate signed it is known by its address, afterwards by the
  identifier from the certificate. Home Assistant used to end up with two base
  devices, the older of which never received another state.

## 1.0.1

- Build against `ghcr.io/home-assistant/base` directly; Supervisor no longer
  provides `BUILD_FROM` and ignores `build.yaml`.
- `gateway_address` is now optional. The gateway uses the address the base
  station actually reached it on, which is also correct behind DNAT.
- Dropped the container port mapping. With host networking it did nothing and
  only duplicated the port options.
- The directory with the stock rule engine files is a fallback for factory
  resets, not a startup requirement.

## 1.0.0

First packaged release.

- Serves the Gigaset elements cloud API locally, including the CRE rule engine
  and certificate signing.
- MQTT discovery for window, door, motion, button and siren nodes plus the base
  station itself.
- Control channel with roughly one second latency: the base polls the add-on
  for pending commands instead of reloading a rule library.
- Pairing, unpairing, node listing, forced (re)calibration, alarm modes, siren
  on/off and sound patterns.
