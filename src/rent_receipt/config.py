"""Load settings.

Ordinary settings (names, addresses, rent, late fee rules) come from a TOML
file. Secrets (the Discord bot token and the email password) come only from
environment variables, so the config file can be shared or backed up without
leaking them.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rent_receipt.money import InvalidAmountError, parse_amount
from rent_receipt.period import InvalidPeriodError, Period

DISCORD_TOKEN_ENV = "DISCORD_BOT_TOKEN"
SMTP_PASSWORD_ENV = "SMTP_PASSWORD"
GMAIL_SMTP_HOST = "smtp.gmail.com"

PAYMENT_METHODS: tuple[str, ...] = (
    "Cash",
    "Check",
    "Cashier's check",
    "Money order",
    "Zelle",
    "Venmo",
    "Bank transfer",
    "Other",
)


class ConfigError(Exception):
    """Raised when the settings are missing or invalid."""


@dataclass(frozen=True)
class Landlord:
    name: str
    email: str
    phone: str | None = None


@dataclass(frozen=True)
class OpeningItem:
    """Something already owed when the bot starts tracking, e.g. last month's rent."""

    description: str
    amount_cents: int


@dataclass(frozen=True)
class Tenant:
    name: str
    email: str
    property_address: str
    monthly_rent_cents: int
    rent_due_day: int
    """Day of the month rent is due, e.g. 1."""
    late_fee_day: int
    """First day of the month the late fee applies, e.g. 6 ("late from the 6th")."""
    late_fee_cents: int
    default_payment_method: str
    billing_start: Period
    """First month the bot charges rent for."""
    opening_balance: tuple[OpeningItem, ...] = ()
    """What was already owed before ``billing_start``, oldest first."""
    phone: str | None = None
    lease_start: date | None = None
    lease_end: date | None = None
    security_deposit_cents: int | None = None


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str


@dataclass(frozen=True)
class DiscordSettings:
    token: str
    owner_user_id: int
    guild_id: int


@dataclass(frozen=True)
class ReminderSettings:
    enabled: bool
    send_time: time
    """Local time of day reminders go out."""
    email_tenant_late_notice: bool


@dataclass(frozen=True)
class Settings:
    landlord: Landlord
    tenant: Tenant
    smtp: SmtpSettings
    discord: DiscordSettings
    reminders: ReminderSettings
    data_dir: Path
    timezone: ZoneInfo


def load_settings(config_path: Path, env: Mapping[str, str] | None = None) -> Settings:
    """Read the TOML file at ``config_path`` plus secrets from ``env``."""
    env = os.environ if env is None else env
    try:
        with config_path.open("rb") as file:
            raw = tomllib.load(file)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file {config_path} is not valid TOML: {exc}") from exc

    landlord = _table(raw, "landlord")
    smtp = _table(raw, "smtp")
    discord = _table(raw, "discord")
    reminders = raw.get("reminders", {})
    if not isinstance(reminders, dict):
        raise ConfigError("[reminders] must be a section.")

    smtp_host = _str(smtp, "host", "smtp")
    smtp_password = _secret(env, SMTP_PASSWORD_ENV)
    if smtp_host == GMAIL_SMTP_HOST:
        # Google shows app passwords in groups of four ("abcd efgh ijkl mnop");
        # the spaces aren't part of the password.
        smtp_password = smtp_password.replace(" ", "")

    timezone_name = _str(raw, "timezone", "top level")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Unknown timezone {timezone_name!r}.") from exc

    return Settings(
        landlord=Landlord(
            name=_str(landlord, "name", "landlord"),
            email=_str(landlord, "email", "landlord"),
            phone=_optional_str(landlord, "phone", "landlord"),
        ),
        tenant=_load_tenant(_table(raw, "tenant")),
        smtp=SmtpSettings(
            host=smtp_host,
            port=_int(smtp, "port", "smtp"),
            username=_str(smtp, "username", "smtp"),
            password=smtp_password,
        ),
        discord=DiscordSettings(
            token=_secret(env, DISCORD_TOKEN_ENV),
            owner_user_id=_int(discord, "owner_user_id", "discord"),
            guild_id=_int(discord, "guild_id", "discord"),
        ),
        reminders=ReminderSettings(
            enabled=_bool(reminders, "enabled", "reminders", default=True),
            send_time=_time(reminders, "send_time", "reminders", default=time(9, 0)),
            email_tenant_late_notice=_bool(
                reminders, "email_tenant_late_notice", "reminders", default=True
            ),
        ),
        data_dir=Path(_str(raw, "data_dir", "top level")),
        timezone=timezone,
    )


def _load_tenant(table: Mapping[str, Any]) -> Tenant:
    rent_due_day = _day_of_month(table, "rent_due_day")
    late_fee_day = _day_of_month(table, "late_fee_day")
    if late_fee_day <= rent_due_day:
        raise ConfigError("tenant.late_fee_day must come after tenant.rent_due_day.")

    method = _optional_str(table, "default_payment_method", "tenant") or "Other"
    if method not in PAYMENT_METHODS:
        raise ConfigError(
            f"tenant.default_payment_method must be one of: {', '.join(PAYMENT_METHODS)}."
        )

    deposit = _optional_str(table, "security_deposit", "tenant")
    return Tenant(
        name=_str(table, "name", "tenant"),
        email=_str(table, "email", "tenant"),
        property_address=_str(table, "property_address", "tenant"),
        monthly_rent_cents=_amount(table, "monthly_rent"),
        rent_due_day=rent_due_day,
        late_fee_day=late_fee_day,
        late_fee_cents=_amount(table, "late_fee", allow_zero=True),
        default_payment_method=method,
        billing_start=_period(table, "billing_start"),
        opening_balance=_opening_balance(table),
        phone=_optional_str(table, "phone", "tenant"),
        lease_start=_optional_date(table, "lease_start"),
        lease_end=_optional_date(table, "lease_end"),
        security_deposit_cents=None if deposit is None else _amount(table, "security_deposit"),
    )


def _period(table: Mapping[str, Any], key: str) -> Period:
    text = _str(table, key, "tenant")
    try:
        return Period.parse(text)
    except InvalidPeriodError as exc:
        raise ConfigError(f"tenant.{key}: {exc}") from exc


def _opening_balance(table: Mapping[str, Any]) -> tuple[OpeningItem, ...]:
    items = table.get("opening_balance", [])
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ConfigError(
            "tenant.opening_balance must be a list of [[tenant.opening_balance]] sections."
        )
    result: list[OpeningItem] = []
    for item in items:
        where = "[[tenant.opening_balance]]"
        try:
            amount = parse_amount(_str(item, "amount", where))
        except InvalidAmountError as exc:
            raise ConfigError(f"{where} amount: {exc}") from exc
        result.append(OpeningItem(_str(item, "description", where), amount))
    return tuple(result)


def _table(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"Missing [{name}] section.")
    return value


def _str(table: Mapping[str, Any], key: str, where: str) -> str:
    value = _optional_str(table, key, where)
    if value is None:
        raise ConfigError(f"Missing text setting {key!r} in {where}.")
    return value


def _optional_str(table: Mapping[str, Any], key: str, where: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"Setting {key!r} in {where} must be text in quotes.")
    return value.strip() or None


def _int(table: Mapping[str, Any], key: str, where: str) -> int:
    value = table.get(key)
    # bool is a subclass of int in Python, so rule it out explicitly.
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"Setting {key!r} in {where} must be a positive whole number.")
    return value


def _bool(table: Mapping[str, Any], key: str, where: str, *, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"Setting {key!r} in {where} must be true or false.")
    return value


def _time(table: Mapping[str, Any], key: str, where: str, *, default: time) -> time:
    value = table.get(key)
    if value is None:
        return default
    if isinstance(value, time):  # TOML local time without quotes: send_time = 09:00:00
        return value
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            pass
    raise ConfigError(f'Setting {key!r} in {where} must look like "09:00".')


def _day_of_month(table: Mapping[str, Any], key: str) -> int:
    value = _int(table, key, "tenant")
    # Capping at 28 means the day exists in every month, February included.
    if value > 28:
        raise ConfigError(f"tenant.{key} must be between 1 and 28.")
    return value


def _amount(table: Mapping[str, Any], key: str, *, allow_zero: bool = False) -> int:
    try:
        return parse_amount(_str(table, key, "tenant"), allow_zero=allow_zero)
    except InvalidAmountError as exc:
        raise ConfigError(f"tenant.{key}: {exc}") from exc


def _optional_date(table: Mapping[str, Any], key: str) -> date | None:
    value = table.get(key)
    if value is None:
        return None
    # TOML has a native date type (lease_start = 2026-01-01), parsed as date.
    if not isinstance(value, date):
        raise ConfigError(f"tenant.{key} must be a date like 2026-01-01 (no quotes).")
    return value


def _secret(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Environment variable {name} is not set.")
    return value
