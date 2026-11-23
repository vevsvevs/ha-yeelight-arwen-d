"""Register built-in Assist phrases without changing the user's configuration."""

from homeassistant.components.conversation import async_get_agent
from homeassistant.components.conversation.agent_manager import get_agent_manager
from homeassistant.components.conversation.models import ConversationInput
from homeassistant.core import HomeAssistant, callback
from hassil.recognize import RecognizeResult
from homeassistant.helpers import intent

from .effect_intent import ASSIST, INTENT_EFFECT, EffectIntent

RU_TARGET = "(для (комнаты|зоны|помещения) {area}|(на|для|у) {name}|(в|во|по) {area})"
START = [
    f"(включи|запусти|установи|поставь|активируй|переключи на) (эффект|режим) {{effect}} {RU_TARGET}",
    f"(включите|запустите|установите|поставьте|активируйте) (эффект|режим) {{effect}} {RU_TARGET}",
    f"(смени|поменяй|переключи) (эффект|режим) на {{effect}} {RU_TARGET}",
    "(turn on|start|set) [the] effect {effect} (on|for) [the] {name}",
    "(aktiviere|starte) [den] Effekt {effect} (auf|für) {name}",
]
STOP = [
    f"(выключи|отключи|останови|убери|выключите|отключите|остановите) [текущий] (эффект|эффекты|режим) {RU_TARGET}",
    f"(верни|верните|включи) обычный (свет|режим) {RU_TARGET}",
    "(turn off|stop|disable) [the] effect (on|for) [the] {name}",
    "(deaktiviere|stoppe) [den] Effekt (auf|für) {name}",
]
LIST = [
    f"(какие эффекты [есть|доступны]|[покажи|назови] список эффектов|(покажи|назови) доступные эффекты) {RU_TARGET}",
    "(list effects|what effects are available) (on|for) [the] {name}",
    "(welche Effekte gibt es|liste Effekte) (auf|für) {name}",
]


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
