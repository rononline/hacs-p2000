"""Regression tests for the P2000 sensor entity."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock


def _install_homeassistant_stubs() -> None:
    if "homeassistant" in sys.modules:
        return

    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor = types.ModuleType("homeassistant.components.sensor")
    config_entries = types.ModuleType("homeassistant.config_entries")
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    restore_state = types.ModuleType("homeassistant.helpers.restore_state")

    class SensorEntity:
        pass

    class ConfigEntry:
        pass

    class HomeAssistant:
        pass

    class RestoreEntity:
        async def async_added_to_hass(self):
            return None

        async def async_get_last_state(self):
            return None

    def async_get_clientsession(hass):
        return None

    sensor.SensorEntity = SensorEntity
    config_entries.ConfigEntry = ConfigEntry
    const.CONF_ICON = "icon"
    const.CONF_NAME = "name"
    const.STATE_UNAVAILABLE = "unavailable"
    const.STATE_UNKNOWN = "unknown"
    core.HomeAssistant = HomeAssistant
    aiohttp_client.async_get_clientsession = async_get_clientsession
    entity_platform.AddEntitiesCallback = object
    restore_state.RestoreEntity = RestoreEntity

    homeassistant.components = components
    homeassistant.config_entries = config_entries
    homeassistant.const = const
    homeassistant.core = core
    homeassistant.helpers = helpers

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.sensor"] = sensor
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
    sys.modules["homeassistant.helpers.restore_state"] = restore_state


_install_homeassistant_stubs()
SENSOR_MODULE = importlib.import_module("custom_components.p2000.sensor")


class FakeApi:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = []

    async def get_data(self, api_filter):
        self.calls.append(api_filter)
        if self._error is not None:
            raise self._error
        return self._result


class TestSensorRestore(unittest.IsolatedAsyncioTestCase):
    async def test_restores_last_known_state_and_attributes(self):
        sensor = SENSOR_MODULE.P2000Sensor(
            FakeApi(),
            "P2000",
            "mdi:alert",
            {},
            "entry-id",
        )
        sensor.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(
                state="Brandweer naar Oudewater",
                attributes={
                    "friendly_name": "P2000",
                    "icon": "mdi:old",
                    "melding": "Brandweer naar Oudewater",
                    "capcode": "BRAND",
                },
            )
        )

        await sensor.async_added_to_hass()

        self.assertEqual("Brandweer naar Oudewater", sensor._attr_native_value)
        self.assertEqual("BRAND", sensor._attr_extra_state_attributes["capcode"])
        self.assertNotIn("friendly_name", sensor._attr_extra_state_attributes)
        self.assertNotIn("icon", sensor._attr_extra_state_attributes)
        self.assertTrue(sensor._attr_available)

    async def test_ignores_unknown_restored_state(self):
        sensor = SENSOR_MODULE.P2000Sensor(
            FakeApi(),
            "P2000",
            "mdi:alert",
            {},
            "entry-id",
        )
        sensor.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(
                state="unknown",
                attributes={"melding": "ignored"},
            )
        )

        await sensor.async_added_to_hass()

        self.assertIsNone(sensor._attr_native_value)
        self.assertEqual({}, sensor._attr_extra_state_attributes)

    async def test_communication_error_keeps_last_known_state(self):
        sensor = SENSOR_MODULE.P2000Sensor(
            FakeApi(error=SENSOR_MODULE.P2000CommunicationError("down")),
            "P2000",
            "mdi:alert",
            {},
            "entry-id",
        )
        sensor._attr_native_value = "Laatste melding"
        sensor._attr_extra_state_attributes = {"melding": "Laatste melding"}
        sensor._attr_available = True

        await sensor.async_update()

        self.assertEqual("Laatste melding", sensor._attr_native_value)
        self.assertTrue(sensor._attr_available)
        self.assertEqual({"melding": "Laatste melding"}, sensor._attr_extra_state_attributes)

    async def test_communication_error_marks_missing_state_unavailable(self):
        sensor = SENSOR_MODULE.P2000Sensor(
            FakeApi(error=SENSOR_MODULE.P2000CommunicationError("down")),
            "P2000",
            "mdi:alert",
            {},
            "entry-id",
        )

        await sensor.async_update()

        self.assertFalse(sensor._attr_available)
        self.assertIsNone(sensor._attr_native_value)


if __name__ == "__main__":
    unittest.main()
