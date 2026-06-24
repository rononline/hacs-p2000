import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.const import CONF_NAME, CONF_ICON
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .api import P2000Api

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "p2000"
SCAN_INTERVAL = timedelta(seconds=30)

CONF_GEMEENTEN = "gemeenten"
CONF_CAPCODES = "capcodes"
CONF_DIENSTEN = "diensten"
CONF_WOONPLAATSEN = "woonplaatsen"
CONF_REGIOS = "regios"
CONF_PRIO1 = "prio1"
CONF_LIFE = "lifeliners"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_ICON, default="mdi:fire-truck"): cv.icon,
    vol.Optional(CONF_WOONPLAATSEN): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_GEMEENTEN): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_CAPCODES): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_REGIOS): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_DIENSTEN): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_PRIO1, default=False): cv.boolean,
    vol.Optional(CONF_LIFE, default=False): cv.boolean,
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Setup the P2000 sensor platform (async)."""
    name = config.get(CONF_NAME)
    icon = config.get(CONF_ICON)

    api_filter = {}

    for prop in [CONF_WOONPLAATSEN, CONF_GEMEENTEN, CONF_CAPCODES, CONF_DIENSTEN, CONF_REGIOS]:
        if prop in config:
            api_filter[prop] = config[prop]

    for prop in [CONF_PRIO1, CONF_LIFE]:
        if config.get(prop):
            api_filter[prop] = 1

    session = async_get_clientsession(hass)
    api = P2000Api(session)

    async_add_entities([P2000Sensor(api, name, icon, api_filter)], update_before_add=True)


class P2000Sensor(SensorEntity):
    def __init__(self, api, name, icon, api_filter):
        self.api = api
        self.api_filter = api_filter
        self._name = name
        self._icon = icon
        self._state = None
        self._attr_extra_state_attributes = {}
        self._attr_unique_id = f"p2000_{name.lower().replace(' ', '_')}"

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def icon(self):
        return self._icon

    @property
    def device_info(self):
        return {
            "identifiers": {("p2000", self._name)},
            "name": self._name,
            "manufacturer": "AlarmeringDroid",
            "model": "P2000 Sensor",
        }

    async def async_update(self):
        """Fetch new state data asynchronously."""
        data = await self.api.get_data(self.api_filter)

        if not data:
            return

        if "melding" not in data or not data["melding"]:
            _LOGGER.warning("P2000: wel data maar geen 'melding' veld, update overgeslagen.")
            return

        self._attr_extra_state_attributes = data
        self._state = data.get("melding", "onbekend")
