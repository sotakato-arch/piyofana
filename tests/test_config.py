from datetime import date
from pathlib import Path

import pytest

from collector.config import ConfigError, load_config

SECRET_URL = "https://feed.piyolog.com/v1/feed/24h/fakeid/fakesecret"
ENV = {"PIYOLOG_FEED_URL": SECRET_URL, "DATABASE_URL": "postgresql://piyolog:hunter2@postgres/piyolog"}


def test_defaults():
    c = load_config(ENV)
    assert c.feed_url == SECRET_URL
    assert c.raw_dir == Path("/data/raw")
    assert c.interval_s == 600
    assert c.feed_expires_on is None


def test_overrides():
    c = load_config({**ENV, "RAW_DIR": "/x", "POLL_INTERVAL_S": "900", "FEED_EXPIRES_ON": "2027-02-13"})
    assert (c.raw_dir, c.interval_s, c.feed_expires_on) == (Path("/x"), 900, date(2027, 2, 13))


@pytest.mark.parametrize("name", ["PIYOLOG_FEED_URL", "DATABASE_URL"])
def test_config_requires(name):
    env = {k: v for k, v in ENV.items() if k != name}
    with pytest.raises(ConfigError) as ei:
        load_config(env)
    assert name in str(ei.value)


def test_config_error_never_echoes_values():
    with pytest.raises(ConfigError) as ei:
        load_config({**ENV, "FEED_EXPIRES_ON": "fakesecret"})
    assert "fakesecret" not in str(ei.value)


def test_config_repr_hides_secrets():
    r = repr(load_config(ENV))
    assert "fakesecret" not in r and "fakeid" not in r
    assert "hunter2" not in r


def test_config_interval_floor():
    with pytest.raises(ConfigError):
        load_config({**ENV, "POLL_INTERVAL_S": "10"})


def test_grafana_ro_password_optional_and_hidden():
    assert load_config(ENV).grafana_ro_password is None
    c = load_config({**ENV, "GRAFANA_RO_PASSWORD": "ro-secret"})
    assert c.grafana_ro_password == "ro-secret"
    assert "ro-secret" not in repr(c)
