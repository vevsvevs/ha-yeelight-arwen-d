"""On/off settings of the Mi Home app as configuration switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ArwenConfigEntry
from .const import DOMAIN, MODEL_ARWEN_D, POWER_ON_PROPERTY, POWER_ON_SETTING
from .coordinator import ArwenCoordinator
from .entity import ArwenEntity

# key -> (property, set_ps key, value written when turned off).
SWITCH_SETTINGS = {
    "ambient_follows": ("bg_proact", "cfg_bg_proact", "0"),
    "remember_state": ("save_state", "cfg_save_state", "0"),
    "wall_switch": ("smart_switch", "cfg_smart_switch", "0"),
    "power_recovery": (POWER_ON_PROPERTY, POWER_ON_SETTING, "2"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArwenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one switch per setting."""
    coordinator = entry.runtime_data
    if coordinator.model == MODEL_ARWEN_D:
        entities: list[SwitchEntity] = [
            SettingSwitch(coordinator, "ambient_follows"),
            SettingSwitch(coordinator, "wall_switch"),
            FadeInSwitch(coordinator),
        ]
        if coordinator.power_on_option is None:
            entities.append(SettingSwitch(coordinator, "remember_state"))
        async_add_entities(entities)
    else:
        async_add_entities(SettingSwitch(coordinator, key) for key in ("remember_state", "power_recovery"))


class SettingSwitch(ArwenEntity, SwitchEntity):
    """Setting read with get_prop and written with set_ps."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ArwenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._prop, self._setting, self._off_value = SWITCH_SETTINGS[key]
        self._verify_write = key in ("remember_state", "power_recovery")

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        value = str(self.coordinator.data.get(self._prop))
        return value == "1" if value in {"0", "1", self._off_value} else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set("1")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(self._off_value)

    async def _async_set(self, value: str) -> None:
        await self.coordinator.async_send([("set_ps", [self._setting, value])])
        if self._verify_write and (
            not self.available or str(self.coordinator.data.get(self._prop)) != value
        ):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="setting_not_confirmed")


class FadeInSwitch(ArwenEntity, SwitchEntity):
    """Fade in when the lamp is turned on (power_on_effect), written together with the default transition."""

    _attr_translation_key = "fade_in"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ArwenCoordinator) -> None:
        super().__init__(coordinator, "fade_in")

    @property
    def is_on(self) -> bool | None:
        # 0 turns on at once, 1-5 fade in.
        value = self.coordinator.data["power_on_effect"]
        return value != "0" if value else None

    async def _async_set(self, effect: str) -> None:
        # set_ps trans_default takes "<default transition ms>,<power_on_effect>".
        ms = self.coordinator.data["trans_interval_dflt"]
        await self.coordinator.async_send([("set_ps", ["trans_default", f"{ms},{effect}"])])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set("1")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set("0")
