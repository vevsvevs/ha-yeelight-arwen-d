"""Expose the Yeelight effect intent in newer versions of the Assist LLM API."""

from homeassistant.components.homeassistant import async_should_expose
from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.llm import LLM_API_ASSIST, IntentTool, LLMContext

from .const import DOMAIN
from .effect_intent import INTENT_EFFECT, effect_lights


@callback
def async_get_tools(hass: HomeAssistant, llm_context: LLMContext, api_id: str) -> LLMTools | None:
    """Offer the effect tool when a supported light is exposed to this assistant."""
    if api_id != LLM_API_ASSIST or not llm_context.assistant or not any(
        async_should_expose(hass, llm_context.assistant, state.entity_id)
        for state in effect_lights(hass)
    ):
        return None

    return LLMTools(tools=[
        IntentTool(f"{DOMAIN}__{INTENT_EFFECT}", handler, title="Control Yeelight effects", integration=DOMAIN)
        for handler in intent.async_get(hass)
        if handler.intent_type == INTENT_EFFECT
    ])
