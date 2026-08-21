# Changelog

## 1.0.49

- Fix two bugs found while running three base stations at once:
  - `/gwctl` (the plain-HTTP control channel) rejected every request from
    any base station that had not yet triggered `remember_base_identity()`
    (which only fires occasionally - from a certificate CN or a specific
    rule report). With more than one base connected, whichever base didn't
    happen to trigger that yet got every single control-channel request
    permanently refused with `403 Forbidden`, logged as `CONTROL POLL
    odmítnut` / `CONTROL UPLOAD odmítnut`. The check now asks whether the
    peer has a configured CRE manifest instead - every legitimate base has
    one from startup, so this no longer depends on incidental runtime
    events.
  - The first time a base sent any request after a restart, the add-on
    replayed **every** device currently in `self.state` - including
    devices that actually belong to a different base station - as that
    base's own Home Assistant discovery documents. Because discovery
    documents are only ever published once per topic, whichever base
    happened to send the first request after a restart silently "adopted"
    every sensor from every other base into its own `via_device`, and the
    other base stations were left with no entities linked to them at all.
    The replay is now filtered to only the devices actually last seen on
    that specific base.

## 1.0.48

- Relax the server's cipher policy (`context.set_ciphers("ALL:@SECLEVEL=0")`)
  alongside the 1.0.47 TLS 1.2 cap. Needed together with a re-issued client
  certificate (see 1.0.47 and [RECOVERY.md](../../RECOVERY.md)) to get the
  unit described below connecting at all; on its own, capping the TLS
  version was not enough for that unit either.

## 1.0.47

- Cap the TLS server at TLS 1.2 (`maximum_version`, alongside the existing
  `minimum_version`). Previously nothing capped the top end, so a client
  that offers TLS 1.3 got it. While adding a second base station, that
  base rejected our self-signed certificate with `unknown_ca` about 20 ms
  after receiving it - a fast, local, deliberate rejection, not a timeout -
  while directly inspecting the certificate confirmed it was otherwise
  completely valid (correct dates, correct SAN, unexpired). The connection
  negotiated TLS 1.3. Working hypothesis: this unit's TLS 1.3 certificate
  path validates more strictly than its TLS 1.2 path, which is the one the
  add-on has always been verified against (hence the pre-existing
  `minimum_version`). Capping the maximum keeps every base on the
  known-working 1.2 path.
- The TLS version cap alone did not fix that unit - the rejection was
  byte-for-byte identical before and after, repeated identically across
  many reconnect attempts on the real network. Three other base stations
  added to the same property since (without a flash dump) accepted the
  add-on's self-signed certificate without needing any of this, including
  one confirmed to have previously been paired with the real Gigaset
  cloud, so this was never a general problem with every base.
- **Root cause found via a full UART flash dump of the failing unit**: its
  own stored client identity was broken. One JFFS2 bank held a client
  certificate that had been expired since 2018; the other, active bank had
  a newer private key and CSR but **no certificate at all**. A base with
  nothing valid to present as its own side of the handshake never
  completes a TLS 1.3 connection to this add-on the same way it does on
  TLS 1.2, which is the path this add-on had actually been verified
  against up to this point. Re-issuing a certificate for that unit's own
  CSR and writing it back into flash, together with the TLS 1.2 cap and
  the 1.0.48 cipher relaxation, fixed this specific unit. See
  [RECOVERY.md](../../RECOVERY.md) for the full UART-level procedure. Since
  this only reproduced on the one unit with a broken on-flash identity, a
  base that already has a valid certificate should not be affected either
  way by the TLS cap or the cipher relaxation.

## 1.0.46

- Fix a false-alarm warning introduced in 1.0.45: on every start, the log
  showed a scary `POZOR: chybí soubory z firmwaru ...` line naming every
  stock Lua file (rules, internal libraries) the add-on itself does not
  serve, as if the base station were stuck redownloading its configuration
  in a loop. It was not - the 1.0.45 rewrite of this check accidentally
  dropped the `manifest_from_base` condition that used to pick a milder,
  correct message for this case (`Základna je má na sobě, potřeba budou až
  po továrním resetu.`). The base already has these files from when the
  original cloud provisioned it and only re-downloads a manifest entry
  whose *file name changed* - since every manifest is now always the base's
  own (there is no more built-in fallback manifest to distinguish it from),
  this milder message is now used unconditionally.

## 1.0.45

- Support more than one base station at once. Previously the add-on assumed
  exactly one base: commands were queued in a single shared list handed to
  whichever base happened to poll `/gwctl` first, and there was only one CRE
  manifest, shared by every connecting base - both would misroute commands
  and, worse, risk serving one base's manifest to another and destroying its
  rules (a base deletes every Lua file its manifest does not name).
  - Commands are now routed to the base station a sensor was last seen on
    (learned automatically from its events); base-station-scoped commands
    (pairing, `reglist`, alarm mode) can take an explicit `"base"` field in
    `control.json` requests, required once more than one base is connected.
  - Each base station now needs its own manifest file, named after its own
    address: `/share/gigaset/cre_manifest.<base_key>.json` instead of the
    single `cre_manifest.json`. **Upgrading from a single-base install
    requires renaming that file once** - see `DOCS.md` /
    `README.md` for the exact steps; the add-on will not start without it and
    names the exact file it expects.
  - The optional `base_id` override now only applies while a single base
    station is connected, so it can no longer silently collapse two physical
    bases into one Home Assistant device once a second one shows up.
  - The add-on's own control library (`gwctl`) and its "manifest changed,
    please reload" nudge are now applied to every base station's manifest
    individually, instead of only the one the old single-manifest code
    happened to look at - each base gets the add-on's libraries injected into
    its own file and is only asked to reload when its own manifest actually
    changed.

## 1.0.44

- `gwctl`'s "Párování zapnout" now sends `regoff` immediately before
  `regon`, instead of just `regon`. This is a plausible partial fix, not a
  confirmed one: pairing a node sometimes silently fails (node just blinks,
  nothing reaches the gateway at all - confirmed on live MQTT capture, and
  the node isn't in the base's own `reglist` either, so it isn't a stale
  registration on our side). The one reliable workaround found so far is to
  let the pairing window expire on its own (which also ends in a `regoff`)
  before starting it again, together with a battery pull on the node - so
  repeating `regon` while a window is already open, without an intervening
  `regoff`, may be a no-op that leaves the base's internal scan state stuck.
  Sending `regoff` first should rule that part out; the node-side battery
  pull may still be needed on top of it, since we don't have visibility
  into what's actually happening inside `UleApp` or the node's own DECT
  join state.

## 1.0.43

- Fix `um01` pressure never updating in Home Assistant even though
  temperature does. The node's periodic response to `temp` isn't just
  `temp=...` - in normal operation it comes back as `tp=<celsius>,<hpa>`,
  and `gigaset_gateway.py` only ever read the first (temperature) field of
  that pair, silently dropping the second. `um01-9.lua`'s hourly `measure()`
  tried to work around this by also sending a separate `press` command
  right after `temp`, but the node only reliably answers the first of two
  commands sent back-to-back without waiting for a reply - the log shows
  `press=...` responses only from isolated, manually-sent `press` requests,
  never from the automatic pairing. `gigaset_gateway.py` now reads the
  second field of `tp` as pressure, and `um01-9.lua` no longer sends the
  redundant, unreliable second command - one `temp` request is enough for
  both readings.

## 1.0.42

- Stop `ws02-16.lua`/`ds02-134.lua` answering a node's `calreq` with `cal`
  on their own. The official pairing flow (open the window to wake the
  sensor, close it, then press the button) exists so calibration captures
  the sensor in its real, mounted, closed position; answering
  automatically - whether immediately or after some fixed delay - could
  capture whatever position it happened to be in while still being handled
  or placed. That is the likely explanation for window/door sensors that
  report `open`/`close` correctly at first and then silently get stuck
  after a while - tilt keeps working because it doesn't depend on
  calibration, only open/close does. The libraries now send `cal` only in
  response to the node's own `button` event (the same pairing/confirm
  button the app tells you to press) or an explicit manual command from
  Home Assistant - never on a timer, and never without that confirmation.

## 1.0.41

- Stop `ws02-15.lua` guessing `temp`/`press` at real `ws02` nodes once an
  hour. Only nodes built on `um01` hardware answer those commands (see
  `UM01_FIRMWARE.md`); a genuine `ws02` never responds, so the hourly
  request was pure overhead for the common case. `um01-8.lua` (1.0.40)
  covers the case that made this worth doing in the first place - a
  `um01` running under its own, correct type.

## 1.0.40

- Fix the `um01` pressure sensor never appearing in Home Assistant for
  anyone who didn't manually request it. `ws02-15.lua` has always asked
  its nodes for `temp`/`press` once an hour; `um01-8.lua` never did, so a
  freshly paired `um01` would only get a pressure reading (and the MQTT
  discovery message that makes it show up as an entity in the first
  place) if something happened to send the `press` command by hand.
  `um01-8.lua` now polls the same way `ws02-15.lua` does, piggybacking on
  the node's own wake-ups rather than adding a schedule of its own.

## 1.0.39

- Fix `log_heartbeats` doing nothing. The 1.0.38 option was added to the
  schema and read by `gigaset_gateway.py`, but `run.sh` builds the runtime
  config from the add-on options field by field, and the line that carries
  `log_heartbeats` through was never added - every other option that changed
  around the same time made it in, this one didn't. The Python side always
  saw the default (`true`) regardless of what the toggle in the UI said, so
  turning it off had no effect. `run.sh` now passes it through like every
  other option.

## 1.0.38

- Add `log_heartbeats` (default on, matching the previous behaviour). The
  base station keeps a long-poll connection open for cloud events and every
  empty poll - roughly every 20 seconds, per connection - logged an `EVENT
  HEARTBEAT` line whether or not anything was happening. Turn it off to cut
  most of the log volume on an otherwise idle base.

## 1.0.37

- Calibrate the `um01` universal sensor, for real this time. The first step
  is not a command at all: it is a side effect of `cfgclose`, which triggers
  a restart of the sensor's application MCU. On the next boot the firmware
  automatically captures whatever position the sensor is in as the *closed*
  reference, without being asked for it. The second step is `cal2`, as
  originally found in 1.0.34, and finally does what it always should have:
  `mx2..mx4` and `mx5..mx7` end up distinct instead of copies of each other.
  Verified end to end on real hardware, including a working `Opening`
  sensor in Home Assistant. *Kalibrace 1 - zavřeno* now sends `cfgclose`
  instead of the `cal` command the sensor ignores in this mode; *Kalibrace 2
  - otevřeno* is unchanged. See `UM01_FIRMWARE.md` for the full story,
  including a caveat for anyone repeating the SWD readout with a probe still
  attached during the restart.
- Fix the `um01`/`ws02` air pressure sensor never appearing in Home
  Assistant. It used `device_class: atmospheric_pressure`, which older
  Home Assistant releases do not know and silently drop from MQTT
  discovery; switched to the long-established `pressure` class, which
  accepts the same `hPa` unit.

## 1.0.36

- Withdraw the `um01` calibration procedure released in 1.0.35. Converting the
  sensor into a window sensor does finish a calibration, but the sensor then
  reports no position at all: it keeps two reference positions per axis and a
  single-step calibration writes the same one into both, so nothing can ever be
  classified as open. Calibrating an `um01` is unsolved again; what was learned
  is written down instead of being presented as a working procedure.
- Keep the temperature, the air pressure and the `preopen` position from 1.0.35 -
  those work regardless.

## 1.0.35

- Make the `um01` universal sensor usable. It ships as a *umos* sensor whose
  two-step calibration only the original cloud could drive, and its firmware
  ignores `cal` while it is configured that way. Reconfiguring it as a window
  sensor with `nvm=0e-7773` and `nvm=0f-3032` makes it calibrate itself in one
  step like any `ws02`. Verified on real hardware.
- Report the temperature and the air pressure the sensor measures. Neither is
  ever sent unprompted, so the window library asks for both once an hour.
- Report `preopen`, the intermediate position only this hardware knows.

## 1.0.34

- Calibrate the `um01` universal sensor. The first step is confirmed with the
  command `cal`, not `cal1` - that string exists in the sensor only as part of
  event names, never as a command, which is why guessing it from the event
  never worked. The second step is `cal2`.
- Read the command names out of the sensor instead of guessing them. Nothing on
  the base station contains them: it forwards the text to the node untouched.
  `UM01_FIRMWARE.md` and `extract_um01_commands.py` document and repeat the
  readout, so the result can be verified or applied to other node types.
- Report the intermediate calibration states the sensor sends while a step is
  running, so it is visible that a step was accepted and not just ignored.

## 1.0.33

- Stop warning about missing firmware files when the manifest came from the
  base station itself. It carries those files, so nothing is missing; they are
  only needed again after a factory reset.

## 1.0.32

- Build the upload request in Lua instead of in the shell. The base station's
  BusyBox has no `printf`, so the HTTP headers were never written and what
  reached the add-on was the bare file contents, answered with 400.

## 1.0.31

- Log every request on the control port other than the routine poll, so it is
  possible to tell whether what the base station sends actually arrives.

## 1.0.30

- Refuse to start without the base station's own CRE manifest. A base deletes
  every Lua file its manifest does not name, so handing it someone else's would
  destroy rules that cannot be downloaded again now that the cloud is gone.
- Ship the tools and the procedure for reading that manifest out of a base:
  a read-only UART dump of the flash (`gigaset_uart_dump.py`) and
  `extract_base_manifest.py`, which carves the data partitions and unpacks them
  with `jefferson`. It is needed once; afterwards the control library keeps the
  manifest up to date by itself.
- Send the manifest with `nc` only. The base station's `wget` is a BusyBox
  build with no long options, so it can never POST.

## 1.0.29

- Leave a base station's rule engine alone until its own manifest is available.
  A fresh installation used to hand the base a manifest naming files nobody can
  serve, which left it re-reading its configuration every half minute forever.
  Sensors, battery, positions and MQTT discovery work either way - none of that
  goes through the rule engine - so the add-on now simply runs read-only and
  says what it needs for commands and calibration.

## 1.0.28

- Never bootstrap a base station automatically. A base deletes every Lua file
  its manifest does not name, so serving it anything but its own manifest
  destroys the rules its owner created - and with the cloud gone, those cannot
  be downloaded again. The add-on now says what it needs instead of guessing.
- Read the base station's manifest from `/mnt/data/cfg/cre`, which is where it
  actually is.

## 1.0.27

- Send the base station's rule engine inventory with `wget`, which is known to
  work on it, and keep `nc` only as a fallback. The result of the attempt now
  travels back on the next poll, so it is visible even while the base is being
  bootstrapped and the logging module is not loaded yet.

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
