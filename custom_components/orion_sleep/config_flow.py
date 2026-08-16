"""Config flow for Orion Sleep integration."""

from __future__ import annotations

import logging
import re
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
    CONF_PARTNER_ACCESS_TOKEN,
    CONF_PARTNER_AUTH_METHOD,
    CONF_PARTNER_AUTH_VALUE,
    CONF_PARTNER_CONFIGURED,
    CONF_PARTNER_EXPIRES_AT,
    CONF_PARTNER_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_ZONE_LEFT,
    DEFAULT_INSIGHTS_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_ZONE_LEFT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

AUTH_METHOD_EMAIL = "email"
AUTH_METHOD_PHONE = "phone"

# Orion's auth endpoint requires a full US phone number including the leading
# country code ("1"), e.g. 15132015808. Anything shorter is rejected server-side.
_PHONE_RE = re.compile(r"^1\d{10}$")


def _normalize_phone(raw: str) -> str:
    """Strip spaces, dashes, parens and a leading + from a phone number."""
    return re.sub(r"[\s\-\(\)\+]", "", raw or "")


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
        """Step 1: User picks login method (email or phone)."""
        if user_input is not None:
            self._auth_method = user_input[CONF_AUTH_METHOD]
            if self._auth_method == AUTH_METHOD_EMAIL:
                return await self.async_step_email()
            return await self.async_step_phone()

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
                }
            ),
        )

    async def _async_send_code(self, auth_value: str) -> ConfigFlowResult | None:
        """Send verification code. Returns None on success, or a step result with errors."""
        self._auth_value = auth_value.strip()

        unique_id = self._auth_value.lower()
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        session = async_get_clientsession(self.hass)
        client = OrionApiClient(session=session)

        email = self._auth_value if self._auth_method == AUTH_METHOD_EMAIL else None
        phone = self._auth_value if self._auth_method == AUTH_METHOD_PHONE else None
        success = await client.request_auth_code(email=email, phone=phone)
        if not success:
            raise OrionConnectionError("API returned success=false")
        return None

    async def async_step_email(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1a: User enters email address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                result = await self._async_send_code(user_input["email"])
                if result is None:
                    return await self.async_step_verify()
            except OrionConnectionError:
                errors["base"] = "cannot_connect"
            except OrionApiError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="email",
            data_schema=vol.Schema(
                {
                    vol.Required("email"): str,
                }
            ),
            errors=errors,
        )

    async def async_step_phone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1b: User enters phone number."""
        errors: dict[str, str] = {}
        phone_default = ""

        if user_input is not None:
            raw = user_input.get("phone", "")
            phone_default = raw
            phone = _normalize_phone(raw)
            if not _PHONE_RE.match(phone):
                _LOGGER.debug(
                    "Rejected phone number %r (normalized: %r) — must be 11 digits starting with 1",
                    raw,
                    phone,
                )
                errors["base"] = "invalid_phone"
            else:
                try:
                    result = await self._async_send_code(phone)
                    if result is None:
                        return await self.async_step_verify()
                except OrionConnectionError:
                    errors["base"] = "cannot_connect"
                except OrionApiError:
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="phone",
            data_schema=vol.Schema(
                {
                    vol.Required("phone", default=phone_default): str,
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


# Transient partner-action selector values (never stored in options/data).
_PARTNER_ACTION_KEEP = "keep"
_PARTNER_ACTION_ADD = "add"
_PARTNER_ACTION_REMOVE = "remove"
_CONF_PARTNER_ACTION = "partner_action"


class OrionSleepOptionsFlow(OptionsFlow):
    """Handle options flow for Orion Sleep."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        # Transient state used while the multi-step partner auth is in progress.
        self._partner_auth_method: str | None = None
        self._partner_auth_value: str | None = None
        # Non-partner options submitted on the init step, held until partner
        # auth completes so they can be committed together.
        self._pending_options: dict[str, Any] = {}

    # ── Step 1: main options ──────────────────────────────────────────────────

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show polling/insight/zone options plus a partner-account action."""
        has_partner = CONF_PARTNER_ACCESS_TOKEN in self._config_entry.data

        if user_input is not None:
            action = user_input.pop(_CONF_PARTNER_ACTION, _PARTNER_ACTION_KEEP)

            if action == _PARTNER_ACTION_REMOVE and has_partner:
                new_data = {
                    k: v
                    for k, v in self._config_entry.data.items()
                    if k
                    not in {
                        CONF_PARTNER_ACCESS_TOKEN,
                        CONF_PARTNER_REFRESH_TOKEN,
                        CONF_PARTNER_EXPIRES_AT,
                        CONF_PARTNER_AUTH_METHOD,
                        CONF_PARTNER_AUTH_VALUE,
                    }
                }
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                return self.async_create_entry(
                    data={**user_input, CONF_PARTNER_CONFIGURED: False}
                )

            if action == _PARTNER_ACTION_ADD:
                self._pending_options = {**user_input, CONF_PARTNER_CONFIGURED: True}
                return await self.async_step_partner_method()

            # keep — preserve existing partner state
            return self.async_create_entry(
                data={**user_input, CONF_PARTNER_CONFIGURED: has_partner}
            )

        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        current_insights_days = self._config_entry.options.get(
            CONF_INSIGHTS_DAYS, DEFAULT_INSIGHTS_DAYS
        )
        current_zone_left = self._config_entry.options.get(CONF_ZONE_LEFT, DEFAULT_ZONE_LEFT)

        partner_actions: dict[str, str] = {
            _PARTNER_ACTION_KEEP: (
                "Partner configured — keep as-is" if has_partner else "No partner account"
            ),
            _PARTNER_ACTION_ADD: (
                "Replace partner account" if has_partner else "Add partner account"
            ),
        }
        if has_partner:
            partner_actions[_PARTNER_ACTION_REMOVE] = "Remove partner account"

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
                    vol.Required(
                        CONF_ZONE_LEFT, default=current_zone_left
                    ): vol.In({"zone_a": "Zone A", "zone_b": "Zone B"}),
                    vol.Required(
                        _CONF_PARTNER_ACTION, default=_PARTNER_ACTION_KEEP
                    ): vol.In(partner_actions),
                }
            ),
        )

    # ── Partner auth: method ──────────────────────────────────────────────────

    async def async_step_partner_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step P1: choose email or phone for the partner account."""
        if user_input is not None:
            self._partner_auth_method = user_input[CONF_AUTH_METHOD]
            if self._partner_auth_method == AUTH_METHOD_EMAIL:
                return await self.async_step_partner_email()
            return await self.async_step_partner_phone()

        return self.async_show_form(
            step_id="partner_method",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AUTH_METHOD, default=AUTH_METHOD_EMAIL
                    ): vol.In(
                        {
                            AUTH_METHOD_EMAIL: "Email",
                            AUTH_METHOD_PHONE: "Phone",
                        }
                    ),
                }
            ),
        )

    # ── Partner auth: email ───────────────────────────────────────────────────

    async def async_step_partner_email(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step P2a: collect partner email and send verification code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._partner_auth_value = user_input["email"].strip()
            try:
                session = async_get_clientsession(self.hass)
                client = OrionApiClient(session=session)
                await client.request_auth_code(email=self._partner_auth_value)
                return await self.async_step_partner_verify()
            except OrionConnectionError:
                errors["base"] = "cannot_connect"
            except OrionApiError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="partner_email",
            data_schema=vol.Schema({vol.Required("email"): str}),
            errors=errors,
        )

    # ── Partner auth: phone ───────────────────────────────────────────────────

    async def async_step_partner_phone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step P2b: collect partner phone and send verification code."""
        errors: dict[str, str] = {}
        phone_default = ""

        if user_input is not None:
            raw = user_input.get("phone", "")
            phone_default = raw
            phone = _normalize_phone(raw)
            if not _PHONE_RE.match(phone):
                errors["base"] = "invalid_phone"
            else:
                self._partner_auth_value = phone
                try:
                    session = async_get_clientsession(self.hass)
                    client = OrionApiClient(session=session)
                    await client.request_auth_code(phone=self._partner_auth_value)
                    return await self.async_step_partner_verify()
                except OrionConnectionError:
                    errors["base"] = "cannot_connect"
                except OrionApiError:
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="partner_phone",
            data_schema=vol.Schema(
                {vol.Required("phone", default=phone_default): str}
            ),
            errors=errors,
        )

    # ── Partner auth: verify code ─────────────────────────────────────────────

    async def async_step_partner_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step P3: verify partner code, store tokens, complete options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["code"].strip()
            session = async_get_clientsession(self.hass)
            client = OrionApiClient(session=session)
            try:
                email = (
                    self._partner_auth_value
                    if self._partner_auth_method == AUTH_METHOD_EMAIL
                    else None
                )
                phone = (
                    self._partner_auth_value
                    if self._partner_auth_method == AUTH_METHOD_PHONE
                    else None
                )
                tokens = await client.verify_auth_code(code=code, email=email, phone=phone)
            except OrionAuthError:
                errors["base"] = "invalid_code"
            except OrionConnectionError:
                errors["base"] = "cannot_connect"
            except OrionApiError:
                errors["base"] = "unknown"
            else:
                # Persist partner tokens in entry.data (not options — tokens
                # are auth state, not user-visible settings).
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={
                        **self._config_entry.data,
                        CONF_PARTNER_AUTH_METHOD: self._partner_auth_method,
                        CONF_PARTNER_AUTH_VALUE: self._partner_auth_value,
                        CONF_PARTNER_ACCESS_TOKEN: tokens["access_token"],
                        CONF_PARTNER_REFRESH_TOKEN: tokens["refresh_token"],
                        CONF_PARTNER_EXPIRES_AT: tokens["expires_at"],
                    },
                )
                return self.async_create_entry(data=self._pending_options)

        return self.async_show_form(
            step_id="partner_verify",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )
