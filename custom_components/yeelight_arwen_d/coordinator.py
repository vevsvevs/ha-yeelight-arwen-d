"""Poll the lamp state over the LAN."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    COLOR_MODE_FLOW,
    CONF_DID,
    DOMAIN,
    INFO_INTERVAL,
    MODEL_ARWEN_D,
    MODEL_CEILING3,
    MODEL_COLORC,
    MODEL_MONOB,
    MONOB_LIGHT_SIID,
    MONOB_PROPERTIES,
    POWER_ON_OPTION_BY_VALUE,
    POWER_ON_PROPERTY,
    PROPS,
    PROPS_CEILING3,
    PROPS_COLORC,
    PUSH_SCAN_INTERVAL,
    REDISCOVERY_INTERVAL,
    SCAN_INTERVAL,
    STARTUP_PROPS,
)
from .miio import MiioClient, MiioError, MiioInvalidReply, MiioTimeout, discover

_LOGGER = logging.getLogger(__name__)
READ_METHODS = {"get_prop", "get_properties", "miIO.info"}


def monob_property(key: str) -> dict[str, Any]:
    """Address a GU10 dimmable light property in a local MIoT request."""
    return {"did": key, "siid": MONOB_LIGHT_SIID, "piid": MONOB_PROPERTIES[key]}


class ArwenCoordinator(DataUpdateCoordinator[dict[str, str]]):
    """Read lamp properties over miIO.

    The Arwen D sends no local push updates and is polled every 3 s. The ceiling3 pushes its
    changes over the Yeelight LAN protocol (lan.py); while that connection is open it is polled
    once a minute.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: MiioClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.client = client
        self._io_lock = asyncio.Lock()
        self.info: dict[str, Any] = {}
        self.boot_time: datetime | None = None
        # monotonic() starts near 0 at host boot, so 0.0 would block discovery for the first minute.
        self._last_discovery = -REDISCOVERY_INTERVAL
        self._last_info = 0.0
        self._effect_brightness: str | None = None

    @property
    def model(self) -> str:
        return self.info["model"]

    @property
    def props(self) -> tuple[str, ...]:
        if self.model == MODEL_COLORC:
            return PROPS_COLORC
        return PROPS_CEILING3 if self.model == MODEL_CEILING3 else PROPS

    @property
    def power_property(self) -> str:
        return "main_power" if self.model == MODEL_ARWEN_D else "power"

    @property
    def power_on_option(self) -> str | None:
        """Return the current startup mode if the firmware reports it."""
        return POWER_ON_OPTION_BY_VALUE.get(str(self.data.get(POWER_ON_PROPERTY)))

    @callback
    def async_set_push_connected(self, connected: bool) -> None:
        """Poll rarely while the lamp pushes its state, every 3 s otherwise.

        The state is read at once, because changes before the connection opened were not pushed
        and a lost connection may mean the lamp lost power.
        """
        self.update_interval = PUSH_SCAN_INTERVAL if connected else SCAN_INTERVAL
        self._async_refresh_now()

    @callback
    def async_handle_push(self, params: dict[str, Any]) -> None:
        """Read the state after the lamp reports a change.

        The pushed values are not applied: a push arrives about 0.2 s after the change, so while
        a knob is turned quickly it can be older than the state already read after a later command.
        """
        self._async_refresh_now()

    @callback
    def _async_refresh_now(self) -> None:
        self.config_entry.async_create_background_task(self.hass, self.async_refresh(), "yeelight_arwen_d refresh")

    async def async_call(self, method: str, params: Any) -> Any:
        """Send one command; if the lamp does not answer, look for it under a new IP and retry once."""
        try:
            return await self.hass.async_add_executor_job(self.client.send, method, params)
        except MiioTimeout:
            if not await self._async_rediscover() or method == "action":
                raise
        except MiioInvalidReply:
            # A new device may already own the old address. Recover through a read, without
            # replaying a write whose reply was unreadable.
            if method not in READ_METHODS or not await self._async_rediscover():
                raise
        return await self.hass.async_add_executor_job(self.client.send, method, params)

    async def async_refresh_info(self) -> None:
        """Read miIO.info (model, firmware, Wi-Fi, uptime) without keeping the token it contains."""
        info = await self.async_call("miIO.info", [])
        info.pop("token", None)
        self.info = info
        self._last_info = time.monotonic()
        # Uptime is whole seconds read with network delay, so each computation differs by about 1 s.
        # Only a difference of more than a minute is taken as a real restart.
        boot = dt_util.utcnow() - timedelta(seconds=info.get("life", 0))
        if self.boot_time is None or abs(boot - self.boot_time) > timedelta(minutes=1):
            self.boot_time = boot.replace(microsecond=0)

    async def _async_rediscover(self) -> bool:
        """Find and verify the same lamp before replacing its saved IP address."""
        did = self.config_entry.data.get(CONF_DID)
        if did is None:
            return False
        if time.monotonic() - self._last_discovery < REDISCOVERY_INTERVAL:
            return False
        self._last_discovery = time.monotonic()
        found = await self.hass.async_add_executor_job(partial(discover, target_did=did))
        host = found.get(did)
        if host is None or host == self.client.host:
            _LOGGER.debug("No new miIO address found for device %s", did)
            return False
        candidate = MiioClient(host, self.config_entry.data[CONF_TOKEN])
        try:
            info = await self.hass.async_add_executor_job(candidate.send, "miIO.info", [])
        except MiioError as err:
            _LOGGER.debug("Lamp at rediscovered address %s is not ready: %s", host, err)
            return False
        if (
            candidate.did != did
            or not isinstance(info, dict)
            or not info.get("model")
            or (self.info and info["model"] != self.model)
        ):
            _LOGGER.debug("Rediscovered address %s did not identify the configured lamp", host)
            return False
        _LOGGER.info("Lamp moved from %s to %s", self.client.host, host)
        self.client = candidate
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_HOST: host}
        )
        return True

    def _normalize_effect_state(self, state: dict[str, str]) -> None:
        """Keep the selected brightness while the device renders a color effect."""
        if self.model == MODEL_COLORC:
            effect_running = str(state["flowing"]) == "1"
            if effect_running:
                state["power"] = "on"
        elif self.model == MODEL_ARWEN_D:
            effect_running = state["main_power"] == "on" and str(state["color_mode"]) == str(COLOR_MODE_FLOW)
        else:
            return

        if not effect_running:
            self._effect_brightness = None
            return

        if self._effect_brightness is None:
            previous = self.data.get("bright") if self.data else None
            self._effect_brightness = next(
                (str(value) for value in (previous, state["bright"])
                 if str(value).isdecimal() and 1 <= int(value) <= 100),
                None,
            )
        if self._effect_brightness is not None:
            state["bright"] = self._effect_brightness

    async def _async_update_data(self) -> dict[str, str]:
        async with self._io_lock:
            return await self._async_read()

    async def _async_read(self) -> dict[str, str]:
        try:
            if self.model == MODEL_MONOB:
                state = await self._async_read_monob()
            else:
                values = await self.async_call("get_prop", list(self.props))
                if not isinstance(values, list) or len(values) != len(self.props):
                    raise MiioError("get_prop: unexpected property list")
                state = dict(zip(self.props, values))
            if self.model in (MODEL_MONOB, MODEL_CEILING3):
                state.update(await self._async_read_startup_settings(STARTUP_PROPS))
            elif self.model == MODEL_ARWEN_D:
                state.update(await self._async_read_startup_settings((POWER_ON_PROPERTY,)))
            if self.model == MODEL_COLORC and state["power"] not in ("on", "off"):
                raise MiioError("get_prop: missing GU10 power state")
            self._normalize_effect_state(state)
            if self.model != MODEL_MONOB:
                state.update(await self._async_read_timer(state[self.power_property] == "on"))
        except MiioError as err:
            raise UpdateFailed(str(err)) from err
        # Counted by time, not by polls: the state is also read after every command and on every push.
        if time.monotonic() - self._last_info >= INFO_INTERVAL:
            try:
                await self.async_refresh_info()
            except MiioError as err:
                _LOGGER.debug("miIO.info failed: %s", err)
        return state

    async def _async_read_monob(self) -> dict[str, str]:
        results = await self.async_call(
            "get_properties", [monob_property(key) for key in MONOB_PROPERTIES]
        )
        if not isinstance(results, list):
            raise MiioError("get_properties: unexpected result")
        values = {
            item.get("piid"): item.get("value")
            for item in results
            if isinstance(item, dict) and item.get("code") == 0
        }
        power = values.get(MONOB_PROPERTIES["power"])
        brightness = values.get(MONOB_PROPERTIES["bright"])
        if not isinstance(power, bool) or type(brightness) is not int:
            raise MiioError("get_properties: missing MIoT power or brightness")
        state = {"power": "on" if power else "off", "bright": str(brightness)}
        delay = values.get(MONOB_PROPERTIES["off_delay"])
        if type(delay) is int and 0 <= delay <= 120:
            state["off_delay"] = str(delay)
        return state

    async def _async_read_startup_settings(self, props: tuple[str, ...]) -> dict[str, str]:
        # The main poll already reached this host. Optional reads need no IP rediscovery.
        try:
            settings = await self.hass.async_add_executor_job(self.client.send, "get_prop", list(props))
        except MiioError as err:
            _LOGGER.debug("Optional startup settings unavailable: %s", err)
        else:
            if isinstance(settings, list):
                return dict(zip(props, settings))
        return {}

    async def _async_read_timer(self, is_on: bool) -> dict[str, str]:
        if not is_on:
            return {"off_delay": "0"}
        try:
            jobs = await self.hass.async_add_executor_job(self.client.send, "cron_get", [0])
        except MiioError as err:
            _LOGGER.debug("Shutdown timer unavailable: %s", err)
            return {}
        if jobs == []:
            return {"off_delay": "0"}
        if isinstance(jobs, list) and jobs and isinstance(jobs[0], dict):
            delay = str(jobs[0].get("delay", ""))
            if delay.isdecimal() and 0 <= int(delay) <= 120:
                return {"off_delay": delay}
        return {}

    async def async_set_monob(self, **values: bool | int) -> None:
        """Send MIoT property writes in order so power has applied before brightness."""
        await self.async_send([
            ("set_properties", [{**monob_property(key), "value": value}])
            for key, value in values.items()
        ])

    async def async_monob_action(self, aiid: int) -> None:
        """Call one of the dimmable bulb's MIoT light actions."""
        await self.async_send([
            ("action", {"did": f"light-{aiid}", "siid": MONOB_LIGHT_SIID, "aiid": aiid, "in": []})
        ])

    async def async_send(
        self, commands: list[tuple[str, Any]], *, flow_brightness: int | None = None,
    ) -> None:
        """Send commands in order, then read the state; a failed command raises in the service call.

        The state is read before the action returns, so the next step of a knob turned quickly
        sees the new value instead of the one from before the previous step. Push updates arrive
        only about 0.2 s later.
        """
        try:
            async with self._io_lock:
                for method, params in commands:
                    if (
                        method == "adjust_bright"
                        and self.model == MODEL_ARWEN_D
                        and self._effect_brightness is not None
                    ):
                        level = min(100, int(self._effect_brightness) + params[0])
                        transition = ["smooth", params[1]] if params[1] > 0 else ["sudden", 0]
                        method, params = (
                            ("set_bright", [level, *transition]) if level > 0
                            else ("set_power", ["off", *transition])
                        )
                    _LOGGER.debug("%s %s", method, params)
                    result = await self.async_call(method, params)
                    if self.model in (MODEL_ARWEN_D, MODEL_COLORC) and method == "set_bright":
                        self._effect_brightness = str(params[0])
                    if method == "set_default" and result != ["ok"]:
                        raise MiioError(f"{method}: unexpected result {result}")
                    if method == "action":
                        if not isinstance(result, dict) or result.get("code") != 0:
                            raise MiioError(f"{method}: {result}")
                    elif method == "set_properties":
                        if (
                            not isinstance(result, list)
                            or len(result) != len(params)
                            or any(
                                not isinstance(item, dict) or item.get("code") not in (0, 1)
                                for item in result
                            )
                        ):
                            raise MiioError(f"{method}: {result}")
                if flow_brightness is not None:
                    self._effect_brightness = str(flow_brightness)
        except MiioError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            await self.async_refresh()
