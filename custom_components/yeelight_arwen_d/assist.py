"""Register built-in Assist phrases without changing the user's configuration."""

from homeassistant.components.conversation import async_get_agent
from homeassistant.components.conversation.agent_manager import get_agent_manager
from homeassistant.components.conversation.models import ConversationInput
from homeassistant.core import HomeAssistant, callback
from hassil.recognize import RecognizeResult
from homeassistant.helpers import intent

from .effect_intent import ASSIST, INTENT_EFFECT, EffectIntent

START = [
    "(включи|запусти|установи|поставь) эффект {effect} (на|для) {name}",
    "(turn on|start|set) [the] effect {effect} (on|for) [the] {name}",
    "(aktiviere|starte) [den] Effekt {effect} (auf|für) {name}",
]
STOP = [
    "(выключи|отключи|останови) эффект (на|для) {name}",
    "верни обычный свет (на|для) {name}",
    "(turn off|stop|disable) [the] effect (on|for) [the] {name}",
    "(deaktiviere|stoppe) [den] Effekt (auf|für) {name}",
]
LIST = [
    "(какие эффекты|список эффектов) [доступны] (на|для) {name}",
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
