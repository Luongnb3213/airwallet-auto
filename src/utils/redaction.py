"""Scrub secret values out of text headed for a terminal, a log or a dump file.

Why this exists: `flow.py` and `scripts/airwallet_full_flow.py` each redact
secrets in their own exception handler, but the two outermost handlers --
`main.py` and `scripts/airwallet_dom_runner.py:main` -- did not. Those catch
everything raised *outside* the registration flow (proxy preparation, OTP client
setup, Excel reading), and third-party exceptions routinely quote their input: an
HTTP error can echo a URL carrying an API key, and a workbook error can quote a
cell.

Design notes:

* **Fail closed.** Over-redaction is acceptable; a leaked secret is not. A short
  value such as a 4-digit PIN may coincidentally match other text (a timestamp,
  a row number) and will be redacted there too. That is the intended trade.
* **Longest first**, so a password that contains the PIN does not leave the PIN
  fragment behind.
* **Never raises.** A redactor that throws inside an exception handler would
  replace a logged error with a crash, which is strictly worse.
"""
from __future__ import annotations

import os

PLACEHOLDER = "[redacted]"

#: Profile keys whose values must never reach a log.
PROFILE_SECRET_KEYS = ("account_password", "password", "pin", "otp")

#: Settings/config keys holding credentials.
SETTINGS_SECRET_KEYS = ("otpbase_api_key", "sms_api_key", "sms_password",
                        "otp_web_api_key", "catchall_password", "default_password",
                        "imap_password", "api_key")

#: Environment variables holding credentials.
ENV_SECRET_NAMES = ("AIRWALLET_OTPBASE_API_KEY", "OTPBASE_API_KEY",
                    "SMS_API_KEY", "OTP_WEB_API_KEY")

#: Values shorter than this are not worth redacting and would match everywhere.
MIN_LENGTH = 3


class Redactor:
    """Collects secret values, then removes them from arbitrary text."""

    def __init__(self, values=()) -> None:
        self._values: set[str] = set()
        for value in values:
            self.add(value)

    def __len__(self) -> int:
        return len(self._values)

    def add(self, value) -> "Redactor":
        """Register one value. Ignores anything too short to redact safely."""
        try:
            text = str(value or "").strip()
        except Exception:
            return self
        if len(text) >= MIN_LENGTH:
            self._values.add(text)
        return self

    def add_profile(self, profile, keys=PROFILE_SECRET_KEYS) -> "Redactor":
        """Register the secret fields of a registration profile."""
        if not isinstance(profile, dict):
            return self
        for key in keys:
            self.add(profile.get(key))
        return self

    def add_settings(self, settings, keys=SETTINGS_SECRET_KEYS) -> "Redactor":
        """Register credentials held in a config/settings dict."""
        if not isinstance(settings, dict):
            return self
        for key in keys:
            self.add(settings.get(key))
        return self

    def add_env(self, names=ENV_SECRET_NAMES) -> "Redactor":
        """Register credentials supplied through the environment."""
        for name in names:
            self.add(os.environ.get(name))
        return self

    def scrub(self, text) -> str:
        """Return ``text`` with every registered value replaced."""
        try:
            result = text if isinstance(text, str) else str(text)
        except Exception:
            return PLACEHOLDER
        if not self._values:
            return result
        try:
            # Longest first: a password containing the PIN must not leave the PIN.
            for value in sorted(self._values, key=len, reverse=True):
                if value and value in result:
                    result = result.replace(value, PLACEHOLDER)
        except Exception:
            return PLACEHOLDER
        return result

    __call__ = scrub


def redact(text, *, profile=None, settings=None, include_env: bool = True) -> str:
    """One-shot helper for an exception handler.

    ``redact(str(error), profile=profile, settings=config)`` is the intended use
    at a process boundary.
    """
    redactor = Redactor()
    if include_env:
        redactor.add_env()
    redactor.add_profile(profile)
    redactor.add_settings(settings)
    return redactor.scrub(text)
