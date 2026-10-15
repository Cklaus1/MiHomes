"""User-facing settings allowlist (2026-10-15).

The web settings page used to write any key. `validate_user_setting` is now the only gate, so
these tests pin both halves: backend keys are refused whatever the value, and every user key
validates its value.
"""

from __future__ import annotations

import pytest

from mihomes.services.config_service import (
    DEFAULTS,
    USER_SETTING_KEYS,
    SettingError,
    timezone_choices,
    validate_user_setting,
)


@pytest.mark.parametrize(
    "key",
    [
        "ai.provider",
        "ai.model",
        "ai.nim_model",
        "ai.claude_api_key",
        "ai.max_context_tokens",
        "retention.audit_years",
        "calendar.provider",
        "telegram.bot_token",
        "telegram.chat_links",
        "notifications.format",
        # Removed 2026-10-15: weather comes from each property's own address, with no fallback.
        "weather.default_location",
        "made.up.key",
        "",
    ],
)
def test_backend_and_unknown_keys_are_refused(key):
    with pytest.raises(SettingError):
        validate_user_setting(key, "anything")


def test_every_user_key_has_a_default():
    for key in USER_SETTING_KEYS:
        assert key in DEFAULTS, f"{key} has no default, so the page would render it blank"


def test_every_default_validates():
    for key in USER_SETTING_KEYS:
        validate_user_setting(key, DEFAULTS[key])


class TestCurrency:
    def test_known_currency_is_normalised_to_upper_case(self):
        assert validate_user_setting("currency.default", " gbp ") == "GBP"

    @pytest.mark.parametrize("value", ["", "XYZ", "dollars", "US"])
    def test_unknown_currency_is_refused(self, value):
        with pytest.raises(SettingError):
            validate_user_setting("currency.default", value)


class TestTimezone:
    def test_canonical_zone_is_accepted(self):
        assert validate_user_setting("locale.timezone", "Europe/London") == "Europe/London"

    @pytest.mark.parametrize("value", ["", "EST", "Mars/Olympus", "US/Eastern", "Etc/GMT+5"])
    def test_unknown_or_alias_zone_is_refused(self, value):
        with pytest.raises(SettingError):
            validate_user_setting("locale.timezone", value)

    def test_choices_are_sorted_and_exclude_aliases(self):
        zones = timezone_choices()
        assert zones == sorted(zones)
        assert "America/New_York" in zones
        assert not any(z.startswith(("Etc/", "US/")) for z in zones)


class TestNotifications:
    @pytest.mark.parametrize("value", ["none", "telegram", "email"])
    def test_known_channel_is_accepted(self, value):
        assert validate_user_setting("notifications.channel", value) == value

    def test_unknown_channel_is_refused(self):
        with pytest.raises(SettingError):
            validate_user_setting("notifications.channel", "sms")

    def test_events_are_stored_in_a_fixed_order(self):
        assert (
            validate_user_setting("notifications.events", "budget, issues")
            == "issues,budget"
        )

    def test_no_events_is_allowed(self):
        assert validate_user_setting("notifications.events", "") == ""

    def test_unknown_event_is_refused(self):
        with pytest.raises(SettingError):
            validate_user_setting("notifications.events", "issues,birthdays")

    @pytest.mark.parametrize("value", ["22:00", "07:30", "00:00", "23:59", ""])
    def test_valid_quiet_hours(self, value):
        assert validate_user_setting("notifications.quiet_start", value) == value

    @pytest.mark.parametrize("value", ["24:00", "7:30", "10pm", "12:60"])
    def test_invalid_quiet_hours_are_refused(self, value):
        with pytest.raises(SettingError):
            validate_user_setting("notifications.quiet_end", value)
