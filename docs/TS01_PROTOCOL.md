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
| 1 | `ok` | thermostat status | the stock TS01 library names it `st`; the original app defines installation, adapt, OK, boost and mechanical/mount/move/boot error states |
| 2 | `2113` | current temperature, 21.13 °C | the stock TS01 library names it `temp`; the stock climate rule converts it to its milli-Celsius representation by multiplying by 10 |
| 3 | `2100` | target temperature, 21.00 °C | the stock library divides the reported value by 100 and multiplies commands by 100 |
| 4 | `2370,2855,2955` | battery voltage samples in mV | same three-value format and range as the base's `ba` sink; the loaded value changes during motor activity while the resting values stay stable |
| 5 | `0,2920` | unknown | preserved in the climate entity's JSON attributes |
| 6 | `0,0` | unknown | preserved in the climate entity's JSON attributes |
| 7 | `71` | unknown, transient in the available samples | preserved in the climate entity's JSON attributes |

The 5–30 °C setpoint range is also defined as `MIN_TEMPERATURE` and
`MAX_TEMPERATURE` in the original Gigaset app. Physical adjustment uses 0.5 °C
steps.

Battery saver does not come from one of the unknown state fields. The stock
`ts01_runtime_configuration` library reports it in a separate `mreport`:

```text
runtime_cfg_ctx;hbtime/900,hbdiv/1,lcdoff/on   # enabled
runtime_cfg_ctx;hbtime/150,hbdiv/6,lcdoff/off  # disabled
```

The original stock Lua and app are evidence used during development and are not
distributed by this project. Unknown fields should remain unnamed until a
controlled experiment or endpoint firmware establishes their meaning.
