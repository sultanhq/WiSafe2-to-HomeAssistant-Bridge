import ast
import asyncio
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components/wisafe2"
spec = importlib.util.spec_from_file_location("protocol", ROOT / "protocol.py")
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)

# Exercise production class definitions without requiring a running HA installation.
def load_class(filename, name, namespace):
    tree = ast.parse((ROOT / filename).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), filename, "exec"), namespace)
    return namespace[name]

class PassiveTests(unittest.TestCase):
    def device(self):
        ns = {"dt_util": SimpleNamespace(utcnow=lambda: datetime.now(timezone.utc)), "DEVICE_MODELS": {}}
        return load_class("coordinator.py", "WiSafe2Device", ns)("707401")

    def test_serial_split_at_every_byte(self):
        message = b'{"device":"707401","base":"OFF","battery":"LOW"}\r\n'
        for split in range(len(message)):
            buf = protocol.LineBuffer()
            lines = buf.feed(message[:split]) + buf.feed(message[split:])
            self.assertEqual(len(lines), 1)
            self.assertEqual(protocol.parse_message(lines[0])["device"], "707401")

    def test_oversized_and_invalid_utf8_recover(self):
        buf = protocol.LineBuffer(limit=20)
        self.assertEqual(buf.feed(b'x'*30+b'\n'+b'\xff\n'+b'{"heartBeat":"3"}\n'), ['{"heartBeat":"3"}'])

    def test_bad_ids_and_shapes_rejected(self):
        for line in ['{"device":"ab813","event":"MISSING"}', '{"device":"zzzzzz","base":"ON"}',
                     '{"device":"707401","battery":null}', '[]', '{"heartBeat":null}',
                     '{"device":"707401","event":"invented"}', '{"device":"707401"}']:
            self.assertIsNone(protocol.parse_message(line), line)
        self.assertEqual(protocol.parse_message('{"device":"4A4304","base":"ON"}')["device"], '4a4304')

    def test_no_initial_healthy_state(self):
        d = self.device()
        self.assertIsNone(d.is_online)
        self.assertIsNone(d.smoke_active)
        self.assertIsNone(d.co_active)
        self.assertIsNone(d.last_seen)

    def test_alarm_latches_survive_unrelated_messages(self):
        d = self.device()
        d.update_from_message({"event":"FIRE EMERGENCY"})
        d.update_from_message({"event":"CARBON MONOXIDE TEST", "result":"PASS"})
        self.assertTrue(d.smoke_active)
        self.assertFalse(d.co_active)
        d.update_from_message({"base":"OFF"})
        self.assertTrue(d.smoke_active)
        seen = d.last_seen
        d.update_from_message({"event":"MISSING"})
        self.assertEqual(d.last_seen, seen)
        d.update_from_message({"event":"SILENCE"})
        self.assertFalse(d.smoke_active)

    def test_reader_is_background_task(self):
        ns = {"DataUpdateCoordinator": object}
        cls = load_class("coordinator.py", "WiSafe2Coordinator", ns)
        c = object.__new__(cls)
        async def executor(func): return object()
        created = []
        def background(coro, name):
            created.append(name)
            coro.close()
            return object()
        c.hass = SimpleNamespace(async_add_executor_job=executor, async_create_background_task=background)
        c._open_serial_connection = lambda: None
        c._async_read_serial = lambda: asyncio.sleep(0)
        c.serial_port = "simulated"
        import logging
        ns["_LOGGER"] = logging.getLogger("test")
        self.assertTrue(asyncio.run(c.async_start()))
        self.assertEqual(created, ["wisafe2_serial_reader"])

    def test_command_guard_never_writes(self):
        ns = {"DataUpdateCoordinator": object, "json": json}
        cls = load_class("coordinator.py", "WiSafe2Coordinator", ns)
        c = object.__new__(cls)
        c.config_entry = SimpleNamespace(options={})
        async def run():
            with self.assertRaises(PermissionError):
                await c.async_send_command("blocked")
        asyncio.run(run())

    def test_bridge_and_unconfigured_ids_do_not_discover_alarms(self):
        cls = load_class("coordinator.py", "WiSafe2Coordinator", {"DataUpdateCoordinator": object, "json": json})
        c = object.__new__(cls)
        c.bridge_radio_id = "a5b813"
        c._devices = {}
        c.auto_discover = False
        c.unconfigured_devices = {}
        async def run():
            await c._handle_json_message({"device":"a5b813", "event":"MISSING"})
            await c._handle_json_message({"device":"efa5b8", "base":"ON"})
        asyncio.run(run())
        self.assertFalse(c._devices)
        self.assertEqual(c.unconfigured_devices, {"efa5b8": None})

    def test_problem_recognises_off_base(self):
        ns = {"CoordinatorEntity": object, "BinarySensorEntity": type("Entity", (), {}),
              "WiSafe2DeviceBinarySensorBase": object, "BinarySensorDeviceClass": SimpleNamespace(PROBLEM="problem")}
        cls = load_class("binary_sensor.py", "WiSafe2DeviceProblemSensor", ns)
        sensor = object.__new__(cls)
        sensor._device = self.device()
        sensor._device.base_status = "OFF"
        self.assertTrue(sensor.is_on)

if __name__ == "__main__": unittest.main()
