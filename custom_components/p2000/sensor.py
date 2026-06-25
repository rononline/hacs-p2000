import logging
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ICON,
    CONF_NAME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api import P2000Api, P2000CommunicationError
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


class P2000Sensor(RestoreEntity, SensorEntity):
    def __init__(self, api, name, icon, api_filter, entry_id):
        self._api = api
        self._api_filter = api_filter
        self._attr_icon = icon
        self._attr_native_value = None
        self._attr_name = name
        self._attr_unique_id = f"p2000_{entry_id}"
        self._attr_available = True
        self._attr_extra_state_attributes = {}
        self._communication_failed = False
        self._attr_device_info = {
            "identifiers": {("p2000", entry_id)},
            "name": name,
            "manufacturer": "AlarmeringDroid",
            "model": "P2000 Sensor",
        }

    async def async_added_to_hass(self) -> None:
        """Restore the latest alert after a restart or config reload."""
        await super().async_added_to_hass()

        if self._attr_native_value is not None:
            return

        last_state = await self.async_get_last_state()
        if (
            last_state is None
            or last_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        ):
            return

        self._attr_native_value = last_state.state
        self._attr_extra_state_attributes = {
            key: value
            for key, value in last_state.attributes.items()
            if key not in ("friendly_name", "icon")
        }
        self._attr_available = True

    async def async_update(self):
        try:
            data = await self._api.get_data(self._api_filter)
        except P2000CommunicationError as exc:
            if self._attr_native_value is None:
                self._attr_available = False
            else:
                # Een tijdelijke storing mag de laatst ontvangen melding niet
                # vervangen door unavailable.
                self._attr_available = True

            if not self._communication_failed:
                _LOGGER.warning("P2000 niet bereikbaar: %s", exc)
                self._communication_failed = True
            return

        if self._communication_failed:
            _LOGGER.info("P2000 verbinding hersteld")
            self._communication_failed = False
        self._attr_available = True

        if not data:
            return

        tekst = data.get("tekstmelding") or data.get("melding")
        if not tekst:
            _LOGGER.warning("P2000: data ontvangen maar geen meldingstekst, update overgeslagen.")
            return

        self._attr_extra_state_attributes = data
        self._attr_native_value = tekst[:_MAX_STATE_LENGTH]
