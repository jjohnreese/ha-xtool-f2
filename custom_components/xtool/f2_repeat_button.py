"""A separate Repeat last job button for the verified regular F2."""

from aiohttp import ClientError
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import XtoolEntity
from .f2_repeat import F2RepeatClient, F2RepeatController, RepeatJobError


class F2RepeatJobButton(XtoolEntity, ButtonEntity):
    """Re-prepare the matching retained job and start it exactly once."""

    _attr_icon = "mdi:repeat-once"
    _attr_translation_key = "repeat_last_job"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("repeat_last_job")
        self._repeat = F2RepeatController(lambda: F2RepeatClient(
            coordinator.host, coordinator.hass.config.time_zone,
        ))

    async def async_press(self) -> None:
        if not self.available:
            raise HomeAssistantError("The F2 is unavailable.")
        try:
            await self._repeat.run()
        except (RepeatJobError, TimeoutError, ConnectionError, ClientError) as err:
            raise HomeAssistantError(str(err) or "F2 repeat timed out; check the machine before retrying.") from err
        finally:
            await self.coordinator.async_request_refresh()
