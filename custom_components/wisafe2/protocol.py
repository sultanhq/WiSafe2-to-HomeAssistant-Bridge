"""Passive serial framing and validation; never transmits commands."""
from __future__ import annotations
import json
import re

DEVICE_ID = re.compile(r"[0-9a-fA-F]{6}")
MODEL_ID = re.compile(r"[0-9a-fA-F]{4}")
EVENTS = {"FIRE TEST", "CARBON MONOXIDE TEST", "BUTTON TEST", "TEST",
          "FIRE EMERGENCY", "CARBON MONOXIDE EMERGENCY", "SILENCE", "MISSING"}


def valid_device_id(value):
    return isinstance(value, str) and DEVICE_ID.fullmatch(value) is not None


def parse_message(line):
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if "heartBeat" in data:
        value = data["heartBeat"]
        return data if isinstance(value, str) and value in tuple("0123456789") and len(data) == 1 else None
    if not valid_device_id(data.get("device")):
        return None
    if "model" in data and (not isinstance(data["model"], str) or not MODEL_ID.fullmatch(data["model"])):
        return None
    for key, allowed in (("event", EVENTS), ("base", {"ON", "OFF", "MISSING"}),
                         ("battery", {"OK", "LOW", "MISSING"}), ("result", {"PASS", "FAIL"})):
        if key in data and (not isinstance(data[key], str) or data[key] not in allowed):
            return None
    if not any(key in data for key in ("event", "base", "battery")):
        return None
    return {**data, "device": data["device"].lower()}


class LineBuffer:
    """Keep partial lines; discard an oversized line through its terminator."""
    def __init__(self, limit=2048):
        self.pending = bytearray()
        self.limit = limit
        self.discarding = False

    def feed(self, chunk):
        lines = []
        for byte in chunk:
            if byte == 10:
                if not self.discarding:
                    try:
                        lines.append(self.pending.decode("utf-8").strip())
                    except UnicodeDecodeError:
                        pass
                self.pending.clear()
                self.discarding = False
            elif not self.discarding:
                self.pending.append(byte)
                if len(self.pending) > self.limit:
                    self.pending.clear()
                    self.discarding = True
        return [line for line in lines if line]
