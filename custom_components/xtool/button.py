"""Button platform — polymorphic dispatch via coord.build_buttons()."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool button entities."""
    coordinator = entry.runtime_data
    coordinator.register_platform_add("button", async_add_entities)
    entities: list[ButtonEntity] = list(coordinator.build_buttons())
    if (coordinator.model.model_id == "F2"
            and coordinator.model.protocol_version == "V2"
            and not coordinator.model.is_read_only):
        from .f2_repeat_button import F2RepeatJobButton

        entities.append(F2RepeatJobButton(coordinator))
    entities.extend(coordinator.initial_accessory_entities("button"))
    async_add_entities(entities)
