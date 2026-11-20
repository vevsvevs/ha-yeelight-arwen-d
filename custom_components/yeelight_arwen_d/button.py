"""Buttons for toggling, relative adjustments and saving a startup preset."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ArwenConfigEntry
from .const import COLORC_TRANSITION_MS, GU10_MODELS, MIN_KELVIN, MODEL_MONOB, POWER_MODE_CT
from .coordinator import ArwenCoordinator
from .entity import ArwenEntity
from .light import clamp_kelvin, fade, num

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
COLORC_CT_STEP = 500


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArwenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one button per relative command of the primary light."""
    coordinator = entry.runtime_data
    if coordinator.model in GU10_MODELS:
        if coordinator.model == MODEL_MONOB:
            buttons = [MonobButton(coordinator, key) for key in MONOB_ACTIONS]
        else:
            buttons = [ColorcButton(coordinator, key) for key in BUTTONS]
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

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ArwenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_name = BUTTONS[key][0]
        self._aiid = MONOB_ACTIONS[key]

    async def async_press(self) -> None:
        await self.coordinator.async_monob_action(self._aiid)


class ColorcButton(ArwenEntity, ButtonEntity):
    """Use the multicolor bulb's absolute controls for relative adjustments."""

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ArwenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_name = BUTTONS[key][0]
        self._key = key

    async def async_press(self) -> None:
        data = self.coordinator.data
        is_on = data["power"] == "on"
        smooth = fade({}, COLORC_TRANSITION_MS)

        if self._key == "toggle":
            await self.coordinator.async_send([("set_power", ["off" if is_on else "on", *smooth])])
            return

        if self._key in ("brightness_up", "brightness_down"):
            current = num(data["bright"]) if is_on else 0
            step = 10 if self._key == "brightness_up" else -10
            target = min(100, current + step)
            if target <= 0:
                if is_on:
                    await self.coordinator.async_send([("set_power", ["off", *smooth])])
                return
            commands = [] if is_on else [("set_power", ["on", *smooth])]
            commands.append(("set_bright", [target, *smooth]))
        else:
            step = COLORC_CT_STEP if self._key == "color_temp_up" else -COLORC_CT_STEP
            target = clamp_kelvin(num(data["ct"], MIN_KELVIN) + step)
            commands = [] if is_on else [("set_power", ["on", *smooth, POWER_MODE_CT])]
            commands.append(("set_ct_abx", [target, *smooth]))
        await self.coordinator.async_send(commands)


class SaveDefaultLight(ArwenEntity, ButtonEntity):
    """Save the current light state as the bulb's startup preset."""

    _attr_name = "Save current light as default"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ArwenCoordinator) -> None:
        super().__init__(coordinator, "save_default")

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data["power"] == "on" and str(data.get("save_state")) != "1"

    async def async_press(self) -> None:
        if not self.available:
            raise HomeAssistantError("Turn the bulb on and disable Remember last state before saving")
        await self.coordinator.async_send([("set_default", [])])
