"""Assist intent for the effects supported by locally controlled Yeelight lamps."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re

from homeassistant.components.homeassistant import async_should_expose
from homeassistant.components.light import ATTR_EFFECT, ATTR_EFFECT_LIST, EFFECT_OFF
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    entity_registry as er,
    intent,
    translation,
)
from snowballstemmer import stemmer

from .const import DOMAIN

INTENT_EFFECT = "YeelightLightEffect"
ASSIST = "conversation"
RUSSIAN_EFFECT_ALIASES = {
    "флоу": "Flow",
    "волна": "Flow",
    "переход цвета": "Flow",
    "цветовой переход": "Flow",
    "плавная смена цвета": "Flow",
    "ночной режим": "Night light",
    "мерцание": "Glittering",
    "морская глубина": "Deep Sea",
    "растущая луна": "Waxing Moon",
    "зелёные холмы": "Green Hills",
    "фантазия": "Fantasy",
}
RUSSIAN_WORDS = re.compile(r"[\w]+")
RUSSIAN_LIGHT_PREFIX = re.compile(
    r"^(?:светильник(?:а|е|ом|у)?|ламп(?:а|е|ы|у|ой)|"
    r"лампочк(?:а|е|и|у|ой)|устройств(?:о|е|а|у|ом))\s+"
)
RUSSIAN_AREA_PREFIX = re.compile(r"^(?:комнат(?:а|е|ы|у)|зон(?:а|е|ы|у)|помещени(?:е|и|я|ю))\s+")
_RUSSIAN_STEMMER = stemmer("russian")


def russian_form(text: str) -> tuple[str, ...]:
    """Normalize Russian inflections for a fallback match."""
    return tuple(_RUSSIAN_STEMMER.stemWords(RUSSIAN_WORDS.findall(text.casefold().replace("ё", "е"))))


@dataclass(frozen=True)
class EffectTarget:
    """One light and the effect names it advertises."""

    state: State
    labels: dict[str, str]

    def match_effect(self, requested: str, russian: bool) -> str | None:
        """Resolve an effect name or a Russian spoken variant."""
        for key, label in self.labels.items():
            if requested in (key.casefold(), label.casefold()):
                return key
        if not russian:
            return None
        spoken = russian_form(requested)
        matches = {
            key for key, label in self.labels.items()
            if spoken in (russian_form(key), russian_form(label))
        }
        matches.update(
            key for alias, key in RUSSIAN_EFFECT_ALIASES.items()
            if key in self.labels and spoken == russian_form(alias)
        )
        return next(iter(matches)) if len(matches) == 1 else None


def effect_lights(hass: HomeAssistant) -> list[State]:
    """Return loaded effect-capable lights from this integration."""
    registry = er.async_get(hass)
    return [
        state for state in hass.states.async_all("light")
        if state.attributes.get(ATTR_EFFECT_LIST)
        and (entry := registry.async_get(state.entity_id)) is not None
        and entry.platform == DOMAIN
    ]


def match_lights(
    hass: HomeAssistant, name: str, area: str, assistant: str, russian: bool,
) -> list[State]:
    """Find one named lamp or the exposed effect-capable lamps of one area."""
    if bool(name) == bool(area):
        return []
    targets = effect_lights(hass)
    if not targets:
        return []

    if area:
        registry = ar.async_get(hass)
        areas = list(intent.find_areas(area, registry))
        if not areas and russian:
            spoken = russian_form(RUSSIAN_AREA_PREFIX.sub("", area.casefold()))
            areas = [
                candidate for candidate in registry.async_list_areas()
                if any(russian_form(label) == spoken for label in (candidate.name, *candidate.aliases))
            ]
        if len(areas) != 1:
            return []
        match = intent.async_match_targets(
            hass, intent.MatchTargetsConstraints(area_name=areas[0].id, assistant=assistant),
            states=targets,
        )
        return match.states if match.is_match and len(match.areas) == 1 else []

    stripped_name = RUSSIAN_LIGHT_PREFIX.sub("", name.casefold()) if russian else name
    for candidate_name in dict.fromkeys((name, stripped_name)):
        match = intent.async_match_targets(
            hass, intent.MatchTargetsConstraints(name=candidate_name, assistant=assistant, single_target=True),
            states=targets,
        )
        if match.is_match:
            return match.states
        if match.no_match_reason != intent.MatchFailedReason.NAME:
            return []
    if not russian:
        return []

    registry = er.async_get(hass)
    spoken = russian_form(stripped_name)
    matches = []
    for state in targets:
        entry = registry.async_get(state.entity_id)
        labels = (state.name, entry.name or "", *entry.aliases)
        if any(russian_form(label) == spoken for label in labels if isinstance(label, str) and label):
            matches.append(state)
    exposed = [state for state in matches if async_should_expose(hass, assistant, state.entity_id)]
    return exposed if len(exposed) == 1 else []


class EffectIntent(intent.IntentHandler):
    """Set, stop, or list an effect by light name or by area."""

    intent_type = INTENT_EFFECT
    platforms = {"light"}
    description = (
        "Control effects on exposed Yeelight lights. Supply either name (one light "
        "or alias) or area (all lights in that area that support the effect). "
        "Use effect 'off' to stop effects without turning lights off. "
        "Use effect 'list' to list available effects."
    )
    slot_schema = {"name": cv.string, "area": cv.string, "effect": cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Resolve the target and effect, then call the existing light action."""
        hass = intent_obj.hass
        response = intent_obj.create_response()
        slots = self.async_validate_slots(intent_obj.slots)
        name = slots.get("name", {}).get("value", "").strip()
        area = slots.get("area", {}).get("value", "").strip()
        requested = slots.get("effect", {}).get("value", "").strip().casefold()
        language = intent_obj.language
        language_code = language.lower().split("-")[0]
        messages = SPEECH.get(language_code, SPEECH["en"])
        russian = language_code == "ru"

        def message(key: str, **values: str) -> str:
            return messages[key].format(**values)

        lights = match_lights(hass, name, area, intent_obj.assistant or ASSIST, russian)
        if not lights:
            response.async_set_error(intent.IntentResponseErrorCode.NO_VALID_TARGETS, message("area" if area else "target"))
            return response

        registry = er.async_get(hass)
        translations = await translation.async_get_translations(hass, language, "entity", [DOMAIN])
        targets = []
        for light in lights:
            entry = registry.async_get(light.entity_id)
            prefix = f"component.{DOMAIN}.entity.light.{entry.translation_key}.state_attributes.effect.state."
            labels = {key: translations.get(prefix + key, key) for key in light.attributes[ATTR_EFFECT_LIST]}
            targets.append(EffectTarget(light, labels))

        choices = ", ".join(dict.fromkeys(
            label for target in targets for key, label in target.labels.items() if key != EFFECT_OFF
        ))
        if requested == "list":
            response.async_set_speech(message("list", effects=choices))
            return response

        selected = [
            (target, key) for target in targets
            if (key := target.match_effect(requested, russian)) is not None
        ]
        if not selected:
            response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, message("effect", effects=choices))
            return response

        async def apply_effect(target: EffectTarget, key: str) -> str | None:
            try:
                await hass.services.async_call(
                    "light", SERVICE_TURN_ON,
                    {ATTR_ENTITY_ID: target.state.entity_id, ATTR_EFFECT: key},
                    blocking=True, context=intent_obj.context,
                )
            except HomeAssistantError:
                return None
            return target.labels[key]

        commands = []
        for target, key in selected:
            light = target.state
            if key == EFFECT_OFF and (
                light.state != STATE_ON or light.attributes.get(ATTR_EFFECT) in (None, EFFECT_OFF)
            ):
                continue
            commands.append(apply_effect(target, key))

        results = await asyncio.gather(*commands)
        successes = [label for label in results if label is not None]
        failures = len(results) - len(successes)
        if failures and not successes:
            response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, message("failed"))
        elif failures:
            response.async_set_speech(message("partial"))
        elif not successes:
            response.async_set_speech(message("already_off"))
        elif all(key == EFFECT_OFF for _, key in selected):
            response.async_set_speech(message("stopped"))
        else:
            response.async_set_speech(message("started", effect=", ".join(dict.fromkeys(successes))))
        return response


SPEECH = {
    "en": {
        "target": "Name one Yeelight light with effects that is exposed to Assist.",
        "area": "Name one area with Yeelight lights that are exposed to Assist.",
        "partial": "The command worked on some lights, but others did not respond.",
        "list": "Available effects: {effects}.",
        "effect": "That effect is unavailable. Available effects: {effects}.",
        "already_off": "There is no active effect.",
        "failed": "The light could not change its effect.",
        "stopped": "Effect stopped.",
        "started": "Effect enabled: {effect}.",
    },
    "ru": {
        "target": "Назовите один светильник Yeelight с эффектами, доступный для Assist.",
        "area": "Назовите одну комнату со светильниками Yeelight, доступными для Assist.",
        "partial": "Команда выполнилась на части светильников. Остальные не ответили.",
        "list": "Доступные эффекты: {effects}.",
        "effect": "Этот эффект недоступен. Доступные эффекты: {effects}.",
        "already_off": "Сейчас эффект выключен.",
        "failed": "Не удалось изменить эффект светильника.",
        "stopped": "Эффект выключен.",
        "started": "Эффект включён: {effect}.",
    },
    "de": {
        "target": "Nenne eine Yeelight-Lampe mit Effekten, die für Assist freigegeben ist.",
        "area": "Nenne einen Bereich mit Yeelight-Lampen, die für Assist freigegeben sind.",
        "partial": "Der Befehl hat bei einigen Lampen funktioniert, andere haben nicht reagiert.",
        "list": "Verfügbare Effekte: {effects}.",
        "effect": "Dieser Effekt ist nicht verfügbar. Verfügbare Effekte: {effects}.",
        "already_off": "Es ist kein Effekt aktiv.",
        "failed": "Der Lichteffekt konnte nicht geändert werden.",
        "stopped": "Effekt ausgeschaltet.",
        "started": "Effekt eingeschaltet: {effect}.",
    },
}
