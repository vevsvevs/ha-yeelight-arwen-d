"""Arwen D power-on behavior."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ArwenConfigEntry
from .const import DOMAIN, MODEL_ARWEN_D, POWER_ON_OPTIONS, POWER_ON_SETTING
from .coordinator import ArwenCoordinator
from .entity import ArwenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArwenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Expose the three-way power-on setting when the Arwen D reports it."""
    coordinator = entry.runtime_data
    if coordinator.model == MODEL_ARWEN_D and coordinator.power_on_option is not None:
        async_add_entities([ArwenPowerOnState(coordinator)])


class ArwenPowerOnState(ArwenEntity, SelectEntity):
    """Choose what the light does when mains power returns."""

    _attr_translation_key = "power_on_state"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(POWER_ON_OPTIONS)

    def __init__(self, coordinator: ArwenCoordinator) -> None:
        super().__init__(coordinator, "power_on_state")

    @property
    def available(self) -> bool:
        return super().available and self.current_option is not None

    @property
    def current_option(self) -> str | None:
        return self.coordinator.power_on_option

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_send([("set_ps", [POWER_ON_SETTING, POWER_ON_OPTIONS[option]])])
        if not self.available or self.current_option != option:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="setting_not_confirmed")
