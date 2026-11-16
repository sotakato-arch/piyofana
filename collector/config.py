"""Collector configuration from environment variables.

Error messages name the variable at fault but never echo its value, and the
repr hides the feed URL and the database password.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

MIN_INTERVAL_S = 60


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    feed_url: str = field(repr=False)
    database_url: str = field(repr=False)
    raw_dir: Path = Path("/data/raw")
    interval_s: int = 600
    feed_expires_on: date | None = None
    grafana_ro_password: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        db = urlsplit(self.database_url)
        return (
            f"Config(feed_url=[REDACTED], database={db.hostname}/{db.path.lstrip('/')},"
            f" raw_dir={self.raw_dir}, interval_s={self.interval_s}, feed_expires_on={self.feed_expires_on})"
        )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def load_config(env: Mapping[str, str]) -> Config:
    try:
        interval = int(env.get("POLL_INTERVAL_S", "600"))
    except ValueError:
        raise ConfigError("POLL_INTERVAL_S must be an integer") from None
    if interval < MIN_INTERVAL_S:
        raise ConfigError(f"POLL_INTERVAL_S must be at least {MIN_INTERVAL_S}")

    expires = env.get("FEED_EXPIRES_ON", "").strip()
    try:
        feed_expires_on = date.fromisoformat(expires) if expires else None
    except ValueError:
        raise ConfigError("FEED_EXPIRES_ON must be an ISO date (YYYY-MM-DD)") from None

    return Config(
        feed_url=_required(env, "PIYOLOG_FEED_URL"),
        database_url=_required(env, "DATABASE_URL"),
        raw_dir=Path(env.get("RAW_DIR", "/data/raw")),
        interval_s=interval,
        feed_expires_on=feed_expires_on,
        grafana_ro_password=env.get("GRAFANA_RO_PASSWORD", "").strip() or None,
    )
