"""Register built-in Assist phrases without changing the user's configuration."""

from homeassistant.components.conversation import async_get_agent
from homeassistant.components.conversation.agent_manager import get_agent_manager
from homeassistant.components.conversation.models import ConversationInput
from homeassistant.core import HomeAssistant, callback
from hassil.recognize import RecognizeResult
from homeassistant.helpers import device_registry as dr, entity_registry as er, intent

from .effect_intent import ASSIST, INTENT_EFFECT, EffectIntent

RU_TARGET = "(для (комнаты|зоны|помещения) {area}|(на|для|у) {name}|(в|во|по) {area})"
RU_START = "(включи|запусти|установи|поставь|активируй|переключи на)"
RU_START_POLITE = "(включите|запустите|установите|поставьте|активируйте)"
RU_STOP = "(выключи|отключи|останови|убери|выключите|отключите|остановите|уберите) [текущий]"
START = [
    f"{RU_START} (эффект {{effect}} [{RU_TARGET}]|режим {{effect}} {RU_TARGET})",
    f"{RU_START_POLITE} (эффект {{effect}} [{RU_TARGET}]|режим {{effect}} {RU_TARGET})",
    f"(смени|поменяй|переключи) (эффект|режим) на {{effect}} {RU_TARGET}",
    "(turn on|start|set) [the] effect {effect} (on|for) [the] {name}",
    "(aktiviere|starte) [den] Effekt {effect} (auf|für) {name}",
]
STOP = [
    f"{RU_STOP} ((эффект|эффекты) [{RU_TARGET}]|режим {RU_TARGET})",
    f"(верни|верните|включи) обычный (свет|режим) {RU_TARGET}",
    "(turn off|stop|disable) [the] effect (on|for) [the] {name}",
    "(deaktiviere|stoppe) [den] Effekt (auf|für) {name}",
]
LIST = [
    f"(какие эффекты [есть|доступны]|[покажи|назови] список эффектов|(покажи|назови) доступные эффекты) {RU_TARGET}",
    "(list effects|what effects are available) (on|for) [the] {name}",
    "(welche Effekte gibt es|liste Effekte) (auf|für) {name}",
]


def source_area_id(hass: HomeAssistant, user_input: ConversationInput) -> str | None:
    """Use the voice satellite's area, then its device's area."""
    device_id = user_input.device_id
    satellite_id = getattr(user_input, "satellite_id", None)
    if satellite_id and (satellite := er.async_get(hass).async_get(satellite_id)):
        if satellite.area_id:
            return satellite.area_id
        device_id = satellite.device_id or device_id
    if device_id and (device := dr.async_get(hass).async_get(device_id)):
        # Older Core only has the area assigned directly to the device.
        if effective_area := getattr(dr, "async_get_effective_area_id", None):
            return effective_area(hass, device)
        return device.area_id
    return None


@callback
def async_setup_assist(hass: HomeAssistant) -> None:
    """Register the shared intent and route spoken phrases to it."""
    intent.async_register(hass, EffectIntent())
    manager = get_agent_manager(hass)
    # Core 2025.8 registers triggers on the default agent. Newer Core uses the manager.
    registrar = manager if hasattr(manager, "register_trigger") else async_get_agent(hass)

    def register(sentences: list[str], fixed_effect: str | None = None) -> None:
        async def handle(user_input: ConversationInput, result: RecognizeResult) -> str:
            slots = {name: {"value": item.value} for name, item in result.entities.items()}
            if "name" not in slots and "area" not in slots:
                if not (area_id := source_area_id(hass, user_input)):
                    return "Назовите комнату или светильник."
                slots["area"] = {"value": area_id}
            if fixed_effect:
                slots["effect"] = {"value": fixed_effect}
            response = await intent.async_handle(
                hass, ASSIST, INTENT_EFFECT, slots, context=user_input.context,
                language=user_input.language, assistant=ASSIST,
            )
            return response.speech["plain"]["speech"]

        registrar.register_trigger(sentences, handle)

    register(START)
    register(STOP, "off")
    register(LIST, "list")
