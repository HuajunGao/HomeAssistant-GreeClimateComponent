"""Gree climate integration init."""

from __future__ import annotations

# Standard library imports
import logging

# Third-party imports
import voluptuous as vol

# Home Assistant imports
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PORT,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

# Local imports
from .const import (
    CONF_DISABLE_AVAILABLE_CHECK,
    CONF_ENCRYPTION_KEY,
    CONF_ENCRYPTION_VERSION,
    CONF_FAN_MODES,
    CONF_HVAC_MODES,
    CONF_SWING_HORIZONTAL_MODES,
    CONF_SWING_MODES,
    CONF_TEMP_SENSOR_OFFSET,
    CONF_UID,
    CONF_ZONE_CONTROLLER,
    CONF_ZONE_COUNT,
    DEFAULT_FAN_MODES,
    DEFAULT_HVAC_MODES,
    DEFAULT_PORT,
    DEFAULT_SWING_HORIZONTAL_MODES,
    DEFAULT_SWING_MODES,
    DOMAIN,
    OPTION_KEYS,
)

PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.NUMBER, Platform.SELECT, Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)

# YAML configuration schema
CLIMATE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_MAC): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_ENCRYPTION_KEY): cv.string,
        vol.Optional(CONF_UID): cv.positive_int,
        vol.Optional(CONF_ENCRYPTION_VERSION, default=1): vol.In([1, 2]),
        vol.Optional(CONF_HVAC_MODES, default=DEFAULT_HVAC_MODES): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_FAN_MODES, default=DEFAULT_FAN_MODES): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_SWING_MODES, default=DEFAULT_SWING_MODES): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_SWING_HORIZONTAL_MODES, default=DEFAULT_SWING_HORIZONTAL_MODES): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_DISABLE_AVAILABLE_CHECK, default=False): cv.boolean,
        vol.Optional(CONF_TEMP_SENSOR_OFFSET): cv.boolean,
    }
)

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.All(cv.ensure_list, [CLIMATE_SCHEMA])}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Gree component from yaml."""
    if DOMAIN not in config:
        return True

    for climate_config in config[DOMAIN]:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data=climate_config,
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gree from a config entry."""
    _LOGGER.debug(
        "Gree async_setup_entry called for %s, zone_controller=%s",
        entry.data.get("host"),
        entry.data.get("zone_controller"),
    )
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    # Combine entry data with options
    combined_data = {**entry.data}
    for key, value in entry.options.items():
        if key not in OPTION_KEYS:
            _LOGGER.debug("Ignoring unexpected option key %s", key)
            continue
        if value is None:
            combined_data.pop(key, None)
        else:
            combined_data[key] = value

    # Create the Gree device instance here and store it
    from .climate import create_gree_device

    # Auto-detect zone controller if not already set in config
    if not combined_data.get(CONF_ZONE_CONTROLLER):
        try:
            from .gree_protocol import get_zone_controller_count
            mac = combined_data.get(CONF_MAC, "").replace(":", "").replace("-", "").lower()
            ip = combined_data.get(CONF_HOST)
            port = combined_data.get(CONF_PORT, DEFAULT_PORT)
            enc_ver = combined_data.get(CONF_ENCRYPTION_VERSION, 1)
            zone_count = await get_zone_controller_count(mac, ip, port, enc_ver, None)
            if zone_count > 0:
                combined_data[CONF_ZONE_CONTROLLER] = True
                combined_data[CONF_ZONE_COUNT] = zone_count
                _LOGGER.info(f"Gree: Auto-detected zone controller with {zone_count} zones for {mac}")
        except Exception as e:
            _LOGGER.warning(f"Gree zone controller auto-detection failed for {combined_data.get(CONF_HOST)}: {e}")

    device = await create_gree_device(hass, combined_data)

    # Zone controller returns a list [master, zone1..zoneN].
    # Non-climate platforms need only the master; climate platform gets all.
    if isinstance(device, list):
        master_device = device[0]
        climate_devices = device
    else:
        master_device = device
        climate_devices = device

    hass.data[DOMAIN][entry.entry_id] = {
        "config": combined_data,
        "device": master_device,
        "climate_devices": climate_devices,
    }

    _LOGGER.debug("Setting up config entry %s with data: %s", entry.entry_id, combined_data)
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        _LOGGER.debug("Unloaded config entry %s", entry.entry_id)
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.debug("Options updated for entry %s: %s", entry.entry_id, entry.options)
    _LOGGER.debug("Reloading config entry %s after options update", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
