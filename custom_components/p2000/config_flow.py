from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_ICON, CONF_NAME
from homeassistant.helpers.selector import (
    BooleanSelector,
    IconSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

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
    DOMAIN,
)

_DIENSTEN_OPTIONS = [
    {"value": "1", "label": "Politie"},
    {"value": "2", "label": "Brandweer"},
    {"value": "3", "label": "Ambulance"},
    {"value": "4", "label": "Kustwacht"},
]


def _schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): TextSelector(),
            vol.Optional(CONF_ICON, default=defaults.get(CONF_ICON, DEFAULT_ICON)): IconSelector(),
            vol.Optional(CONF_GEMEENTEN, default=defaults.get(CONF_GEMEENTEN, "")): TextSelector(),
            vol.Optional(CONF_WOONPLAATSEN, default=defaults.get(CONF_WOONPLAATSEN, "")): TextSelector(),
            vol.Optional(CONF_CAPCODES, default=defaults.get(CONF_CAPCODES, "")): TextSelector(),
            vol.Optional(CONF_REGIOS, default=defaults.get(CONF_REGIOS, "")): TextSelector(),
            vol.Optional(CONF_DIENSTEN, default=defaults.get(CONF_DIENSTEN, [])): SelectSelector(
                SelectSelectorConfig(
                    options=_DIENSTEN_OPTIONS,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(CONF_PRIO1, default=defaults.get(CONF_PRIO1, False)): BooleanSelector(),
            vol.Optional(CONF_LIFE, default=defaults.get(CONF_LIFE, False)): BooleanSelector(),
        }
    )


class P2000FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return P2000OptionsFlow(config_entry)


class P2000OptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
