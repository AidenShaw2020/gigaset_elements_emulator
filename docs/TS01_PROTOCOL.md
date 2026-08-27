# TS01 thermostat protocol notes

The base forwards a TS01 `state` as a semicolon-separated payload. This table
records what the emulator can support without silently assigning guessed
meanings to fields.

Example captured from a paired thermostat:

```text
ok;2113;2100;2370,2855,2955;0,2920;0,0;71
```

| field | example | interpretation | evidence |
|---|---:|---|---|
| 1 | `ok` | thermostat status | the stock TS01 library names it `st`; live hardware has confirmed `inst`, `ok` and `errmech` |
| 2 | `2113` | current temperature, 21.13 °C | the stock TS01 library names it `temp`; the stock climate rule converts it to its milli-Celsius representation by multiplying by 10 |
| 3 | `2100` | target temperature, 21.00 °C | the stock library divides the reported value by 100 and multiplies commands by 100 |
| 4 | `2370,2855,2955` | battery voltage samples in mV | same three-value format and range as the base's `ba` sink; the loaded value changes during motor activity while the resting values stay stable |
| 5 | `0,2920` | unknown | preserved in the climate entity's JSON attributes |
| 6 | `0,0` | unknown | preserved in the climate entity's JSON attributes |
| 7 | `71` | inferred valve/actuator position, raw 0-255 | live values `0`, `71`, `157` and `255` followed motor movement; exposed as `round(raw * 100 / 255)` percent, but not named by the stock Lua |

The 5–30 °C setpoint range is also defined as `MIN_TEMPERATURE` and
`MAX_TEMPERATURE` in the original Gigaset app. Physical adjustment uses 0.5 °C
steps.

## Confirmed live status behavior

- `inst` is sent after battery insertion. A captured `inst;0;0;...` uses both
  temperature zeroes as sentinels, so the gateway keeps the last valid current
  and target values instead of replacing them with 0 °C.
- `errmech` corresponds to the thermostat's LCD `E-2` mechanical fault. The
  thermostat still communicates and accepts commands, so the climate entity
  remains available and a separate mechanical-problem diagnostic turns on.
- A later non-`errmech` state clears that diagnostic.

## Setpoint acknowledgement

After accepting a target, the stock TS01 rule reports:

```text
setpoint,rule,2500
```

The final value is the confirmed target in hundredths of a degree Celsius. The
gateway accepts only exactly three fields, a safe source token and a value from
`500` through `3000`, then immediately retains `25.00` on the MQTT setpoint
topic. This avoids waiting for the thermostat's state heartbeat, which can take
about 15 minutes.

## Valve-position confidence

The final field is exposed as an **inferred** diagnostic. Its 0-255 range and
the observed progression during motor movement strongly indicate actuator
position, but the stock TS01 Lua does not read or name this field. Consumers
should therefore treat the percentage as useful telemetry rather than a
firmware-confirmed semantic definition.

Battery saver does not come from one of the unknown state fields. The stock
`ts01_runtime_configuration` library reports it in a separate `mreport`:

```text
runtime_cfg_ctx;hbtime/900,hbdiv/1,lcdoff/on   # enabled
runtime_cfg_ctx;hbtime/150,hbdiv/6,lcdoff/off  # disabled
```

The original stock Lua and app are evidence used during development and are not
distributed by this project. The two remaining compound fields (fields 5 and
6) should remain unnamed until a controlled experiment or endpoint firmware
establishes their meaning.
