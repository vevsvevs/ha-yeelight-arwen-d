"""Assist intent for the effects supported by locally controlled Yeelight lamps."""

from __future__ import annotations

from homeassistant.components.light import ATTR_EFFECT, ATTR_EFFECT_LIST, EFFECT_OFF
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er, intent, translation

from .const import DOMAIN

INTENT_EFFECT = "YeelightLightEffect"
ASSIST = "conversation"
RUSSIAN_EFFECT_ALIASES = {
    "радугу": "rainbow",
    "флоу": "flow",
    "ночной режим": "night light",
}


def effect_lights(hass: HomeAssistant) -> list[State]:
    """Return loaded effect-capable lights from this integration."""
    registry = er.async_get(hass)
    return [
        state for state in hass.states.async_all("light")
        if state.attributes.get(ATTR_EFFECT_LIST)
        and (entry := registry.async_get(state.entity_id)) is not None
        and entry.platform == DOMAIN
    ]


class EffectIntent(intent.IntentHandler):
    """Set, stop, or list an effect on one exposed light."""

    intent_type = INTENT_EFFECT
    platforms = {"light"}
    description = (
        "Control effects on an exposed Yeelight light by name or alias. "
        "Use effect 'off' to stop the effect and return to normal lighting without "
        "turning the light off. Use effect 'list' to list its available effects."
    )
    slot_schema = {"name": cv.string, "effect": cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Resolve the target and effect, then call the existing light action."""
        hass = intent_obj.hass
        response = intent_obj.create_response()
        slots = self.async_validate_slots(intent_obj.slots)
        name = slots.get("name", {}).get("value", "").strip()
        requested = slots.get("effect", {}).get("value", "").strip().casefold()
        language = intent_obj.language
        language_code = language.lower().split("-")[0]
        messages = SPEECH.get(language_code, SPEECH["en"])
        if language_code == "ru":
            requested = RUSSIAN_EFFECT_ALIASES.get(requested, requested)

        def message(key: str, **values: str) -> str:
            return messages[key].format(**values)

        targets = effect_lights(hass)
        if not name or not targets:
            response.async_set_error(intent.IntentResponseErrorCode.NO_VALID_TARGETS, message("target"))
            return response
        match = intent.async_match_targets(
            hass,
            intent.MatchTargetsConstraints(
                name=name, domains={"light"}, assistant=intent_obj.assistant or ASSIST,
                single_target=True,
            ),
            states=targets,
        )
        if not match.is_match:
            response.async_set_error(intent.IntentResponseErrorCode.NO_VALID_TARGETS, message("target"))
            return response

        light = match.states[0]
        entry = er.async_get(hass).async_get(light.entity_id)
        translations = await translation.async_get_translations(hass, language, "entity", [DOMAIN])
        prefix = f"component.{DOMAIN}.entity.light.{entry.translation_key}.state_attributes.effect.state."
        labels = {effect: translations.get(prefix + effect, effect) for effect in light.attributes[ATTR_EFFECT_LIST]}
        choices = ", ".join(label for effect, label in labels.items() if effect != EFFECT_OFF)
        if requested == "list":
            response.async_set_speech(message("list", effects=choices))
            return response

        effect = next(
            (key for key, label in labels.items() if requested in (key.casefold(), label.casefold())),
            None,
        )
        if effect is None:
            response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, message("effect", effects=choices))
            return response

        if effect == EFFECT_OFF and (
            light.state != STATE_ON or light.attributes.get(ATTR_EFFECT) in (None, EFFECT_OFF)
        ):
            response.async_set_speech(message("already_off"))
            return response

        try:
            await hass.services.async_call(
                "light", SERVICE_TURN_ON, {ATTR_ENTITY_ID: light.entity_id, ATTR_EFFECT: effect},
                blocking=True, context=intent_obj.context,
            )
        except HomeAssistantError:
            response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, message("failed"))
        else:
            response.async_set_speech(message("stopped" if effect == EFFECT_OFF else "started", effect=labels[effect]))
        return response


SPEECH = {
    "en": {
        "target": "Name one Yeelight light with effects that is exposed to Assist.",
        "list": "Available effects: {effects}.",
        "effect": "That effect is unavailable. Available effects: {effects}.",
        "already_off": "There is no active effect.",
        "failed": "The light could not change its effect.",
        "stopped": "Effect stopped.",
        "started": "Effect enabled: {effect}.",
    },
    "ru": {
        "target": "Назовите один светильник Yeelight с эффектами, доступный для Assist.",
        "list": "Доступные эффекты: {effects}.",
        "effect": "Этот эффект недоступен. Доступные эффекты: {effects}.",
        "already_off": "Сейчас эффект выключен.",
        "failed": "Не удалось изменить эффект светильника.",
        "stopped": "Эффект выключен.",
        "started": "Эффект включён: {effect}.",
    },
    "de": {
        "target": "Nenne eine Yeelight-Lampe mit Effekten, die für Assist freigegeben ist.",
        "list": "Verfügbare Effekte: {effects}.",
        "effect": "Dieser Effekt ist nicht verfügbar. Verfügbare Effekte: {effects}.",
        "already_off": "Es ist kein Effekt aktiv.",
        "failed": "Der Lichteffekt konnte nicht geändert werden.",
        "stopped": "Effekt ausgeschaltet.",
        "started": "Effekt eingeschaltet: {effect}.",
    },
}
