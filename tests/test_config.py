from pathlib import Path

import pytest

from rent_receipt.config import ConfigError, load_settings

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.toml"
SECRETS = {"DISCORD_BOT_TOKEN": "token", "SMTP_PASSWORD": "app-password"}


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def filled_example() -> str:
    return (
        EXAMPLE.read_text()
        .replace("owner_user_id = 0", "owner_user_id = 111")
        .replace("guild_id = 0", "guild_id = 222")
    )


def test_example_config_loads(tmp_path: Path) -> None:
    settings = load_settings(write_config(tmp_path, filled_example()), env=SECRETS)
    assert settings.tenant.monthly_rent_cents == 100_000
    assert settings.tenant.late_fee_cents == 5_000
    assert settings.tenant.phone is None
    assert settings.discord.owner_user_id == 111
    assert settings.reminders.send_time.hour == 9
    assert settings.smtp.password == "app-password"


def test_missing_secret(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="SMTP_PASSWORD"):
        load_settings(write_config(tmp_path, filled_example()), env={"DISCORD_BOT_TOKEN": "token"})


def test_late_day_must_follow_due_day(tmp_path: Path) -> None:
    text = filled_example().replace("late_fee_day = 6", "late_fee_day = 1")
    with pytest.raises(ConfigError, match="late_fee_day"):
        load_settings(write_config(tmp_path, text), env=SECRETS)


def test_unknown_payment_method(tmp_path: Path) -> None:
    text = filled_example().replace('"Zelle"', '"Bitcoin"')
    with pytest.raises(ConfigError, match="default_payment_method"):
        load_settings(write_config(tmp_path, text), env=SECRETS)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.toml", env=SECRETS)
