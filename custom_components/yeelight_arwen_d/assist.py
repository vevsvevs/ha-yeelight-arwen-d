"""Register built-in Assist phrases without changing the user's configuration."""

from homeassistant.components.conversation import async_get_agent
from homeassistant.components.conversation.agent_manager import get_agent_manager
from homeassistant.components.conversation.models import ConversationInput
from homeassistant.components.homeassistant import async_should_expose
from homeassistant.core import HomeAssistant, callback
from hassil.recognize import RecognizeResult
from homeassistant.helpers import device_registry as dr, entity_registry as er, intent

from .effect_intent import (
    ASSIST,
    INTENT_EFFECT,
    RUSSIAN_AREA_PREFIX,
    RUSSIAN_LIGHT_PREFIX,
    SPEECH,
    EffectIntent,
    find_target_areas,
    russian_form,
)

RU_PREPOSITIONS = {
    "на": "on", "для": "for", "у": "by", "в": "in", "во": "inside", "по": "across",
}
RU_AREA_PREPOSITIONS = {"в", "во", "по"}
RU_TARGET = "(" + "|".join(
    f"{word} {{target_{slot}}}" for word, slot in RU_PREPOSITIONS.items()
) + ")"
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


def resolve_russian_target(
    hass: HomeAssistant, preposition: str, target: str,
) -> tuple[str, str] | str:
    """Check the literal light name and the grammatical reading of a spoken target."""
    target = target.strip()
    light_marker = RUSSIAN_LIGHT_PREFIX.match(target.casefold())
    area_marker = RUSSIAN_AREA_PREFIX.match(target.casefold())
    area_preposition = preposition in RU_AREA_PREPOSITIONS
    area_name = target[area_marker.end():] if area_marker else target if area_preposition else ""
    if light_marker:
        light_names = (target[light_marker.end():],)
    elif area_marker:
        light_names = ()
    elif area_preposition:
        light_names = (f"{preposition} {target}",)
    else:
        light_names = (target, f"{preposition} {target}")

    areas = find_target_areas(hass, area_name, russian=True) if area_name and not light_marker else []
    wanted_names = {russian_form(name) for name in light_names}
    lights = []
    if wanted_names:
        registry = er.async_get(hass)
        get_aliases = getattr(intent, "async_get_entity_aliases", None)
        for state in hass.states.async_all("light"):
            if not async_should_expose(hass, ASSIST, state.entity_id):
                continue
            entry = registry.async_get(state.entity_id)
            if get_aliases:
                labels = get_aliases(hass, entry, state=state)
            else:
                labels = [state.name, entry.name, *entry.aliases] if entry else [state.name]
            if any(russian_form(label) in wanted_names for label in (state.entity_id, *labels) if label):
                lights.append(state.entity_id)

    if len(lights) + len(areas) > 1:
        return "Подходит несколько целей. Уточните комнату или полное имя светильника."
    if lights:
        return "name", lights[0]
    if areas:
        return "area", areas[0].id
    return SPEECH["ru"]["area" if area_name and not light_marker else "target"]


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
            for word, slot in RU_PREPOSITIONS.items():
                if spoken_target := slots.pop(f"target_{slot}", None):
                    resolved = resolve_russian_target(hass, word, spoken_target["value"])
                    if isinstance(resolved, str):
                        return resolved
                    key, value = resolved
                    slots[key] = {"value": value}
                    break
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
