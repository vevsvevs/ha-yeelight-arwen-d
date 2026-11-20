"""Buttons for toggling and relative changes the lamp computes itself, like the remote's keys."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ArwenConfigEntry
from .const import GU10_MODELS, MODEL_MONOB
from .coordinator import ArwenCoordinator
from .entity import ArwenEntity
from .light import num

# key -> (entity name, miIO method, params).
# Brightness moves by 10 points; below 1 % the lamp turns off. set_adjust for brightness jumps
# in large fixed steps (50 -> 100 -> 40), so it is only used for color temperature, where it
# steps through 2700/4000/5200/6500 K.
BUTTONS: dict[str, tuple[str, str, list[Any]]] = {
    "toggle": ("Toggle", "toggle", []),
    "brightness_up": ("Brightness up", "adjust_bright", [10]),
    "brightness_down": ("Brightness down", "adjust_bright", [-10]),
    "color_temp_up": ("Color temperature up", "set_adjust", ["increase", "ct"]),
    "color_temp_down": ("Color temperature down", "set_adjust", ["decrease", "ct"]),
}
MONOB_ACTIONS = {"toggle": 1, "brightness_up": 3, "brightness_down": 2}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArwenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one button per relative command of the primary light."""
    coordinator = entry.runtime_data
    if coordinator.model in GU10_MODELS:
        buttons = [MonobButton(coordinator, key) for key in MONOB_ACTIONS] if coordinator.model == MODEL_MONOB else []
        async_add_entities([*buttons, SaveDefaultLight(coordinator)])
    else:
        async_add_entities(ArwenButton(coordinator, key) for key in BUTTONS)


class ArwenButton(ArwenEntity, ButtonEntity):
    """Send one fixed command to the primary light."""

    def __init__(self, coordinator: ArwenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_name, self._method, self._params = BUTTONS[key]

    async def async_press(self) -> None:
        params = self._params
        if self._method == "adjust_bright":
            # adjust_bright needs a duration; use the lamp's default transition.
            params = [*params, num(self.coordinator.data["trans_interval_dflt"])]
        await self.coordinator.async_send([(self._method, params)])


class MonobButton(ArwenEntity, ButtonEntity):
    """Expose a native MIoT action on the GU10 dimmable bulb."""

    def __init__(self, coordinator: ArwenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_name = BUTTONS[key][0]
        self._aiid = MONOB_ACTIONS[key]

    async def async_press(self) -> None:
        await self.coordinator.async_monob_action(self._aiid)


class SaveDefaultLight(ArwenEntity, ButtonEntity):
    """Save the current light state as the bulb's startup preset."""

    _attr_name = "Save current light as default"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ArwenCoordinator) -> None:
        super().__init__(coordinator, "save_default")

    async def async_press(self) -> None:
        data = self.coordinator.data
        if data["power"] != "on" or str(data.get("save_state")) == "1":
            raise HomeAssistantError("Turn the bulb on and disable Remember last state before saving")
        await self.coordinator.async_send([("set_default", [])])
