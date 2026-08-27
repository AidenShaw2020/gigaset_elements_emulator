from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "gigaset_gateway" / "gigaset_gateway.py"
SPEC = importlib.util.spec_from_file_location("gigaset_gateway_module", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)


class FakeMqttClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []
        self.subscribed: list[str] = []

    def publish(self, topic: str, payload: str, retain: bool = True) -> None:
        self.published.append((topic, payload, retain))

    def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)


def bridge(command_handler=None):
    instance = gateway.MqttBridge(
        {"enabled": False, "base_topic": "gigaset", "discovery_prefix": "homeassistant"},
        command_handler,
    )
    instance.client = FakeMqttClient()
    return instance


class Ts01ParserTests(unittest.TestCase):
    SAMPLE = "ok;2113;2100;2370,2855,2955;0,2920;0,0;71"

    def test_parses_confirmed_fields_and_preserves_unknown_fields(self) -> None:
        state = gateway.ts01_state_from_payload(self.SAMPLE)
        assert state is not None
        self.assertEqual(state["status"], "ok")
        self.assertEqual(state["current_temperature"], 21.13)
        self.assertEqual(state["target_temperature"], 21.0)
        self.assertEqual(state["battery_values_mv"], [2370, 2855, 2955])
        self.assertEqual(state["extra_fields"], ["2370,2855,2955", "0,2920", "0,0", "71"])
        self.assertEqual(state["raw"], self.SAMPLE)

    def test_rejects_invalid_or_out_of_range_state(self) -> None:
        for payload in ("", "ok;x;2100", "ok;2113;499", "ok;2113;3001"):
            with self.subTest(payload=payload):
                self.assertIsNone(gateway.ts01_state_from_payload(payload))

    def test_decodes_stock_battery_saver_reports(self) -> None:
        self.assertTrue(
            gateway.ts01_battery_saver_from_payload(
                "runtime_cfg_ctx;hbtime/900,hbdiv/1,lcdoff/on"
            )
        )
        self.assertIs(
            gateway.ts01_battery_saver_from_payload(
                "runtime_cfg_ctx;lcdoff/off,hbdiv/6,hbtime/150"
            ),
            False,
        )
        self.assertIsNone(
            gateway.ts01_battery_saver_from_payload("runtime_cfg_ctx;future/value")
        )


class Ts01ControlTests(unittest.TestCase):
    def test_normalizes_and_serializes_setpoint(self) -> None:
        request = gateway.normalize_control_request(
            {
                "id": "mqtt-1",
                "action": "thermostat_setpoint",
                "device_type": "TS01",
                "device_id": "0387A36430",
                "command": "21.50",
            }
        )
        self.assertEqual(request["command"], "21.5")
        self.assertEqual(request["device_type"], "ts01")
        self.assertEqual(
            gateway.control_poll_line(request),
            "thermostat|mqtt-1/thermostat_setpoint|21.5|0387a36430|ts01",
        )

    def test_rejects_wrong_type_range_and_injection(self) -> None:
        for device_type, value in (
            ("ws02", "21.5"),
            ("ts01", "4.5"),
            ("ts01", "30.5"),
            ("ts01", "21;os.execute"),
            ("ts01", "nan"),
        ):
            with self.subTest(device_type=device_type, value=value):
                with self.assertRaises(ValueError):
                    gateway.normalize_control_request(
                        {
                            "id": "mqtt-1",
                            "action": "thermostat_setpoint",
                            "device_type": device_type,
                            "device_id": "0387a36430",
                            "command": value,
                        }
                    )

    def test_generated_gwctl_uses_stock_ts01_function(self) -> None:
        source = gateway.control_lua_source("http://192.168.137.1:8080/gwctl")
        self.assertIn("ts01.set_setpoint(dev, value, 0)", source)
        self.assertIn("thermostat = thermostat", source)

    def test_mqtt_setpoint_routes_value_to_command_handler(self) -> None:
        calls: list[tuple[str, str, str, str, str]] = []
        mqtt = bridge(lambda *args: calls.append(args))
        mqtt._on_message(
            None,
            None,
            SimpleNamespace(
                topic="gigaset/ts01/0387a36430/setpoint/set",
                payload=b"22.5",
                retain=False,
            ),
        )
        self.assertEqual(
            calls,
            [("thermostat_setpoint", "ts01", "0387a36430", "", "22.5")],
        )

    def test_setpoint_routes_to_base_that_last_reported_thermostat(self) -> None:
        instance = gateway.Gateway.__new__(gateway.Gateway)
        instance.device_base = {"ts01/0387a36430": "192.0.2.10"}
        instance.base_keys_seen = {
            "192.0.2.10": "192_0_2_10",
            "192.0.2.20": "192_0_2_20",
        }
        self.assertEqual(
            instance._resolve_target_peers(
                {
                    "action": "thermostat_setpoint",
                    "device_type": "ts01",
                    "device_id": "0387a36430",
                }
            ),
            ["192.0.2.10"],
        )


class Ts01DiscoveryTests(unittest.TestCase):
    SAMPLE = "ok;2113;2100;2370,2855,2955;0,2920;0,0;71"

    def test_state_creates_climate_diagnostics_and_removes_legacy_event(self) -> None:
        mqtt = bridge()
        mqtt.publish_event(
            {
                "device_type": "ts01",
                "device_id": "0387a36430",
                "sink": "state",
                "payload": self.SAMPLE,
                "timestamp": 123,
            },
            "base1",
        )
        published = {topic: payload for topic, payload, _retain in mqtt.client.published}
        root = "gigaset/ts01/0387a36430"
        self.assertEqual(published[f"{root}/temperature"], "21.13")
        self.assertEqual(published[f"{root}/setpoint"], "21.00")
        self.assertEqual(published[f"{root}/thermostat_status"], "ok")
        self.assertEqual(published[f"{root}/last_event"], "")
        self.assertIn(f"{root}/battery", published)
        climate = json.loads(
            published["homeassistant/climate/gigaset_ts01_0387a36430_climate/config"]
        )
        self.assertEqual(climate["temperature_command_topic"], f"{root}/setpoint/set")
        self.assertEqual(climate["current_temperature_topic"], f"{root}/temperature")
        self.assertEqual(climate["min_temp"], 5.0)
        self.assertEqual(climate["max_temp"], 30.0)
        self.assertEqual(
            published["homeassistant/sensor/gigaset_ts01_0387a36430_event/config"],
            "",
        )

    def test_runtime_report_creates_battery_saver_entity(self) -> None:
        mqtt = bridge()
        mqtt.publish_event(
            {
                "device_type": "ts01",
                "device_id": "0387a36430",
                "sink": "mreport",
                "payload": "runtime_cfg_ctx;hbtime/900,hbdiv/1,lcdoff/on",
            }
        )
        published = {topic: payload for topic, payload, _retain in mqtt.client.published}
        self.assertEqual(published["gigaset/ts01/0387a36430/battery_saver"], "ON")
        self.assertIn(
            "homeassistant/binary_sensor/gigaset_ts01_0387a36430_battery_saver/config",
            published,
        )

    def test_valid_state_removes_legacy_event_announced_earlier_in_same_run(self) -> None:
        mqtt = bridge()
        mqtt.publish_event(
            {
                "device_type": "ts01",
                "device_id": "0387a36430",
                "sink": "state",
                "payload": "old-unparsed-payload",
            }
        )
        mqtt.publish_event(
            {
                "device_type": "ts01",
                "device_id": "0387a36430",
                "sink": "state",
                "payload": self.SAMPLE,
            }
        )
        event_topic = "homeassistant/sensor/gigaset_ts01_0387a36430_event/config"
        event_payloads = [
            payload
            for topic, payload, _retain in mqtt.client.published
            if topic == event_topic
        ]
        self.assertEqual(len(event_payloads), 2)
        self.assertNotEqual(event_payloads[0], "")
        self.assertEqual(event_payloads[1], "")

    def test_replay_restores_climate_without_momentary_events(self) -> None:
        mqtt = bridge()
        mqtt.announce_known(
            [
                {
                    "device_type": "ts01",
                    "device_id": "0387a36430",
                    "sink": "state",
                    "payload": self.SAMPLE,
                }
            ],
            "base1",
        )
        topics = {topic for topic, _payload, _retain in mqtt.client.published}
        self.assertIn(
            "homeassistant/climate/gigaset_ts01_0387a36430_climate/config",
            topics,
        )
        self.assertIn("gigaset/ts01/0387a36430/setpoint", topics)

    def test_remove_device_cleans_climate_and_new_state_topics(self) -> None:
        mqtt = bridge()
        mqtt.remove_device(
            "gigaset/ts01/0387a36430", "gigaset_ts01_0387a36430"
        )
        published = {topic: payload for topic, payload, _retain in mqtt.client.published}
        self.assertEqual(
            published["homeassistant/climate/gigaset_ts01_0387a36430_climate/config"],
            "",
        )
        for topic in ("setpoint", "thermostat_state", "thermostat_status", "battery_saver"):
            self.assertEqual(published[f"gigaset/ts01/0387a36430/{topic}"], "")


if __name__ == "__main__":
    unittest.main()
