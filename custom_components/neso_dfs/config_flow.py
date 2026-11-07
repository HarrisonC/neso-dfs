"""UI setup and options for NESO Demand Flexibility."""

from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import FetchError, async_fetch_report
from .const import (
    CONF_PARTICIPANT,
    CONF_POLL_INTERVAL,
    CONF_RETENTION_DAYS,
    CONF_ZONE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)
from .models import Report, participant_key, whole_number


def options_schema(values: dict[str, Any]) -> probatio.Schema:
    """Selectors show defaults; whole-number validation also runs on submit."""
    return probatio.Schema(
        {
            probatio.Required(
                CONF_POLL_INTERVAL,
                default=values.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(min=1, step=1, mode=NumberSelectorMode.BOX)
            ),
            probatio.Required(
                CONF_RETENTION_DAYS,
                default=values.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
            ): NumberSelector(
                NumberSelectorConfig(min=0, step=1, mode=NumberSelectorMode.BOX)
            ),
        }
    )


def validate_options(values: dict[str, Any]) -> dict[str, int]:
    return {
        CONF_POLL_INTERVAL: whole_number(values[CONF_POLL_INTERVAL], 1),
        CONF_RETENTION_DAYS: whole_number(values[CONF_RETENTION_DAYS], 0),
    }


class DfsConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per canonical participant name and numeric zone."""

    VERSION = 1

    def __init__(self) -> None:
        self._report: Report | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> DfsOptionsFlow:
        return DfsOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        try:
            if self._report is None:
                self._report = await async_fetch_report(self.hass)
        except FetchError:
            errors["base"] = "cannot_connect"
        participants = list(self._report.participants) if self._report else []
        if self._report is not None and not participants:
            errors["base"] = "no_participants"
            self._report = None
        if errors:
            # An empty dropdown cannot be submitted. Offer a retry form, then
            # populate the selectors only after discovery succeeds.
            return self.async_show_form(
                step_id="user",
                data_schema=probatio.Schema({}),
                errors=errors,
            )
        if user_input:
            names = {participant_key(name): name for name in participants}
            participant = names.get(
                participant_key(user_input.get(CONF_PARTICIPANT, ""))
            )
            if participant is None:
                errors[CONF_PARTICIPANT] = "invalid_participant"
            try:
                zone = whole_number(user_input[CONF_ZONE], 1)
            except ValueError, KeyError:
                errors[CONF_ZONE] = "invalid_whole_number"
            try:
                options = validate_options(user_input)
            except ValueError, KeyError:
                errors["base"] = "invalid_options"
            if not errors:
                await self.async_set_unique_id(f"{participant_key(participant)}|{zone}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{participant} — Zone {zone}",
                    data={CONF_PARTICIPANT: participant, CONF_ZONE: zone},
                    options=options,
                )
        schema = probatio.Schema(
            {
                probatio.Required(CONF_PARTICIPANT): SelectSelector(
                    SelectSelectorConfig(
                        options=participants,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                probatio.Required(CONF_ZONE): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                **options_schema(user_input or {}).schema,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class DfsOptionsFlow(OptionsFlowWithReload):
    """Change polling and retention while preserving the entry's identity."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                options = validate_options(user_input)
            except ValueError, KeyError:
                errors["base"] = "invalid_options"
            else:
                return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=options_schema(dict(self.config_entry.options)),
            errors=errors,
        )
