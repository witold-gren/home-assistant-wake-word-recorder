"""Set up Wake Word Recorder: the folder with household.yaml of the wakeword toolkit.

When the folder has no household.yaml yet, a second step creates one with the people of Home
Assistant (person entities); words are added later with the toolkit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector
from homeassistant.util import slugify
import voluptuous as vol

from .const import CONF_HOUSEHOLD, DEFAULT_HOUSEHOLD, DOMAIN
from .household import HouseholdError, HouseholdMissing, create, load

CONF_PEOPLE = "people"
CONF_CHILDREN = "children"


def speaker_id(person: str) -> str:
    """person.tomek -> tomek (lowercase letters, digits and _ as the toolkit wants)."""
    return slugify(person.split(".", 1)[-1]) or "person"


class RecorderConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._path = DEFAULT_HOUSEHOLD

    def _done(self, name: str) -> ConfigFlowResult:
        return self.async_create_entry(title=f"Wake Word Recorder ({name})", data={CONF_HOUSEHOLD: self._path})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            self._path = user_input[CONF_HOUSEHOLD].rstrip("/") or "/"
            try:
                household = await self.hass.async_add_executor_job(load, Path(self._path))
            except HouseholdMissing:
                return await self.async_step_create()
            except HouseholdError as err:
                errors["base"] = "household"
                placeholders["error"] = str(err)
            else:
                return self._done(household.name)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOUSEHOLD, default=self._path): str}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_create(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """No household.yaml yet: create it with the chosen people."""
        people = sorted(self.hass.states.async_entity_ids("person"))
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = user_input.get(CONF_PEOPLE) or []
            children = set(user_input.get(CONF_CHILDREN) or [])
            speakers = [(speaker_id(p), "child" if p in children else "adult") for p in chosen]
            ids = [sid for sid, _ in speakers]
            if not speakers:
                errors["base"] = "no_people"
            elif len(set(ids)) != len(ids):
                errors["base"] = "duplicate_people"
            else:
                language = (self.hass.config.language or "en").split("-")[0]
                household = await self.hass.async_add_executor_job(create, Path(self._path), speakers, language)
                return self._done(household.name)
        people_selector = selector.EntitySelector(selector.EntitySelectorConfig(domain="person", multiple=True))
        return self.async_show_form(
            step_id="create",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PEOPLE, default=(user_input or {}).get(CONF_PEOPLE, people)): people_selector,
                    vol.Optional(CONF_CHILDREN, default=(user_input or {}).get(CONF_CHILDREN, [])): people_selector,
                }
            ),
            errors=errors,
            description_placeholders={"path": self._path},
        )
