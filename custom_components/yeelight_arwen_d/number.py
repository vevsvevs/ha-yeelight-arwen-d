"""Default transition of the lamp as a configuration number."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ArwenConfigEntry
from .const import GU10_MODELS, MODEL_MONOB
from .coordinator import ArwenCoordinator
from .entity import ArwenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArwenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the model's transition setting or the bulb's shutoff timer."""
    coordinator = entry.runtime_data
    if coordinator.model in GU10_MODELS:
        async_add_entities([OffDelay(coordinator)])
    else:
        async_add_entities([DefaultTransition(coordinator)])


class DefaultTransition(ArwenEntity, NumberEntity):
    """Fade the lamp uses when a command carries no transition (Mi Home app: dimming time)."""

    _attr_name = "Default transition"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    # 0 changes at once; 10 s is the longest value tested.
    _attr_native_min_value = 0
    _attr_native_max_value = 10000
    _attr_native_step = 10

    def __init__(self, coordinator: ArwenCoordinator) -> None:
        super().__init__(coordinator, "default_transition")

    @property
    def native_value(self) -> int | None:
        value = self.coordinator.data["trans_interval_dflt"]
        return int(value) if value else None

    async def async_set_native_value(self, value: float) -> None:
        # trans_default also sets power_on_effect, so the current value is written back unchanged.
        effect = self.coordinator.data["power_on_effect"] or "1"
        await self.coordinator.async_send([("set_ps", ["trans_default", f"{int(value)},{effect}"])])


class OffDelay(ArwenEntity, NumberEntity):
    """Start or cancel the shutoff timer that runs on the bulb."""

    _attr_name = "Off delay"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_max_value = 120
    _attr_native_step = 1

    def __init__(self, coordinator: ArwenCoordinator) -> None:
        super().__init__(coordinator, "off_delay")

    @property
    def available(self) -> bool:
        return super().available and "off_delay" in self.coordinator.data

    @property
    def native_value(self) -> int | None:
        value = self.coordinator.data.get("off_delay")
        return int(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        minutes = int(value)
        if self.coordinator.model == MODEL_MONOB:
            await self.coordinator.async_set_monob(off_delay=minutes)
        elif minutes:
            await self.coordinator.async_send([("cron_add", [0, minutes])])
        elif self.native_value:
            await self.coordinator.async_send([("cron_del", [0])])
