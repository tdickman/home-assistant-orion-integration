"""Config flow for Orion Sleep integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OrionApiClient, OrionApiError, OrionAuthError, OrionConnectionError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_AUTH_METHOD,
    CONF_AUTH_VALUE,
    CONF_EXPIRES_AT,
    CONF_INSIGHTS_DAYS,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_INSIGHTS_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

AUTH_METHOD_EMAIL = "email"
AUTH_METHOD_PHONE = "phone"


class OrionSleepConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Orion Sleep."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._auth_method: str | None = None
        self._auth_value: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OrionSleepOptionsFlow:
        """Return the options flow handler."""
        return OrionSleepOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: User enters auth method and value, we send the code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._auth_method = user_input[CONF_AUTH_METHOD]
            self._auth_value = user_input[CONF_AUTH_VALUE].strip()

            # Set unique ID to prevent duplicate entries
            unique_id = self._auth_value.lower()
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            # Send verification code
            session = async_get_clientsession(self.hass)
            client = OrionApiClient(session=session)

            try:
                email = (
                    self._auth_value if self._auth_method == AUTH_METHOD_EMAIL else None
                )
                phone = (
                    self._auth_value if self._auth_method == AUTH_METHOD_PHONE else None
                )
                success = await client.request_auth_code(email=email, phone=phone)
                if success:
                    return await self.async_step_verify()
                errors["base"] = "cannot_connect"
            except OrionConnectionError:
                errors["base"] = "cannot_connect"
            except OrionApiError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AUTH_METHOD, default=AUTH_METHOD_EMAIL): vol.In(
                        {
                            AUTH_METHOD_EMAIL: "Email",
                            AUTH_METHOD_PHONE: "Phone",
                        }
                    ),
                    vol.Required(CONF_AUTH_VALUE): str,
                }
            ),
            errors=errors,
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: User enters the verification code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["code"].strip()

            session = async_get_clientsession(self.hass)
            client = OrionApiClient(session=session)

            try:
                email = (
                    self._auth_value if self._auth_method == AUTH_METHOD_EMAIL else None
                )
                phone = (
                    self._auth_value if self._auth_method == AUTH_METHOD_PHONE else None
                )
                tokens = await client.verify_auth_code(
                    code=code, email=email, phone=phone
                )
            except OrionAuthError:
                errors["base"] = "invalid_code"
            except OrionConnectionError:
                errors["base"] = "cannot_connect"
            except OrionApiError:
                errors["base"] = "unknown"
            else:
                data = {
                    CONF_AUTH_METHOD: self._auth_method,
                    CONF_AUTH_VALUE: self._auth_value,
                    CONF_ACCESS_TOKEN: tokens["access_token"],
                    CONF_REFRESH_TOKEN: tokens["refresh_token"],
                    CONF_EXPIRES_AT: tokens["expires_at"],
                }

                if self._reauth_entry:
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry, data=data
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")

                return self.async_create_entry(
                    title=f"Orion Sleep ({self._auth_value})",
                    data=data,
                )

        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema(
                {
                    vol.Required("code"): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth triggered by ConfigEntryAuthFailed."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._auth_method = entry_data.get(CONF_AUTH_METHOD)
        self._auth_value = entry_data.get(CONF_AUTH_VALUE)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth and send a new verification code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = OrionApiClient(session=session)

            try:
                email = (
                    self._auth_value if self._auth_method == AUTH_METHOD_EMAIL else None
                )
                phone = (
                    self._auth_value if self._auth_method == AUTH_METHOD_PHONE else None
                )
                success = await client.request_auth_code(email=email, phone=phone)
                if success:
                    return await self.async_step_verify()
                errors["base"] = "cannot_connect"
            except OrionConnectionError:
                errors["base"] = "cannot_connect"
            except OrionApiError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
        )


class OrionSleepOptionsFlow(OptionsFlow):
    """Handle options flow for Orion Sleep."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        current_insights_days = self._config_entry.options.get(
            CONF_INSIGHTS_DAYS, DEFAULT_INSIGHTS_DAYS
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                        vol.Coerce(int), vol.Range(min=60, max=3600)
                    ),
                    vol.Required(
                        CONF_INSIGHTS_DAYS, default=current_insights_days
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                }
            ),
        )
