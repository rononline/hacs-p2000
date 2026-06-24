import logging
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ICON, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import P2000Api
from .const import (
    CONF_CAPCODES,
    CONF_DIENSTEN,
    CONF_GEMEENTEN,
    CONF_LIFE,
    CONF_PRIO1,
    CONF_REGIOS,
    CONF_WOONPLAATSEN,
    DEFAULT_ICON,
    DEFAULT_NAME,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=30)
_MAX_STATE_LENGTH = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    config = {**entry.data, **entry.options}
    name = config.get(CONF_NAME, DEFAULT_NAME)
    icon = config.get(CONF_ICON) or DEFAULT_ICON

    api_filter = {}
    for prop in [CONF_GEMEENTEN, CONF_WOONPLAATSEN, CONF_CAPCODES, CONF_REGIOS]:
        raw = config.get(prop, "")
        if raw:
            parsed = [v.strip() for v in raw.split(",") if v.strip()]
            if parsed:
                api_filter[prop] = parsed

    if config.get(CONF_DIENSTEN):
        api_filter[CONF_DIENSTEN] = config[CONF_DIENSTEN]

    for prop in [CONF_PRIO1, CONF_LIFE]:
        if config.get(prop):
            api_filter[prop] = 1
    session = async_get_clientsession(hass)
    api = P2000Api(session)

    async_add_entities(
        [P2000Sensor(api, name, icon, api_filter, entry.entry_id)],
        update_before_add=True,
    )


class P2000Sensor(SensorEntity):
    def __init__(self, api, name, icon, api_filter, entry_id):
        self._api = api
        self._api_filter = api_filter
        self._attr_icon = icon
        self._attr_native_value = None
        self._attr_name = name
        self._attr_unique_id = f"p2000_{entry_id}"
        self._attr_extra_state_attributes = {}
        self._attr_device_info = {
            "identifiers": {("p2000", entry_id)},
            "name": name,
            "manufacturer": "AlarmeringDroid",
            "model": "P2000 Sensor",
        }

    async def async_update(self):
        data = await self._api.get_data(self._api_filter)

        if not data:
            return

        tekst = data.get("tekstmelding") or data.get("melding")
        if not tekst:
            _LOGGER.warning("P2000: data ontvangen maar geen meldingstekst, update overgeslagen.")
            return

        self._attr_extra_state_attributes = data
        self._attr_native_value = tekst[:_MAX_STATE_LENGTH]
