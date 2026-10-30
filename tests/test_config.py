"""Configuration safety tests for development and production runtimes."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

import cc_config  # noqa: E402


def _set_production(monkeypatch, data, backups, **extra):
    values = {
        "CC_ENV": "production",
        "CC_DATA_DIR": str(data),
        "CC_BACKUP_DIR": str(backups),
        "CC_EXPECTED_TS_LOGIN": "synthetic@example.invalid",
        "CC_REQUIRE_TS_IDENTITY": "true",
        "CC_BIND_HOST": "127.0.0.1",
    }
    values.update(extra)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_production_requires_existing_storage(monkeypatch, tmp_path):
    _set_production(monkeypatch, tmp_path / "missing-data", tmp_path / "missing-backups")

    with pytest.raises(cc_config.ConfigError, match="existing directory"):
        cc_config.load()


def test_container_production_allows_internal_wildcard_bind(monkeypatch, tmp_path):
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    data.mkdir()
    backups.mkdir()
    _set_production(monkeypatch, data, backups, CC_CONTAINER="true", CC_BIND_HOST="0.0.0.0")

    config = cc_config.load()

    assert config.production
    assert config.bind_host == "0.0.0.0"
    assert config.data_home == str(data)
    assert config.backup_home == str(backups)


def test_non_container_production_rejects_wildcard_bind(monkeypatch, tmp_path):
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    data.mkdir()
    backups.mkdir()
    _set_production(monkeypatch, data, backups, CC_BIND_HOST="0.0.0.0")

    with pytest.raises(cc_config.ConfigError, match="loopback"):
        cc_config.load()
