"""Local control of Yeelight lights over miIO."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType

from . import lan
from .assist import async_setup_assist
from .const import CONF_DID, DOMAIN, MODEL_CEILING3
from .coordinator import ArwenCoordinator
from .miio import MiioClient, MiioError

PLATFORMS = [Platform.BUTTON, Platform.LIGHT, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

type ArwenConfigEntry = ConfigEntry[ArwenCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the shared Assist commands once for all lamps."""
    async_setup_assist(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ArwenConfigEntry) -> bool:
    """Connect to the lamp and start polling; for the ceiling3 also listen for pushed state changes."""
    coordinator = ArwenCoordinator(hass, entry, MiioClient(entry.data[CONF_HOST], entry.data[CONF_TOKEN]))
    try:
        await coordinator.async_refresh_info()
    except MiioError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="connection_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    if entry.data.get(CONF_DID) != coordinator.client.did:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DID: coordinator.client.did})

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    if coordinator.model == MODEL_CEILING3:
        # Cancelled automatically when the entry is unloaded.
        entry.async_create_background_task(
            hass,
            lan.listen(
                lambda: coordinator.client.host,
                coordinator.async_handle_push,
                coordinator.async_set_push_connected,
            ),
            "yeelight_arwen_d LAN push",
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ArwenConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
