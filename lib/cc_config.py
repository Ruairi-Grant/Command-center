"""Explicit environment configuration for Command Centre.

The production runtime should provide data and security-sensitive settings
explicitly. Local development defaults remain loopback-only and use a
repository-independent data location supplied by CC_HOME or CC_DATA_DIR.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


_ENVIRONMENTS = {"development", "test", "production"}


class ConfigError(ValueError):
    """Raised when an environment setting is invalid or unsafe."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean")


def _port() -> int:
    value = _env("CC_PORT", "8787")
    try:
        port = int(value or "8787")
    except ValueError as exc:
        raise ConfigError("CC_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("CC_PORT must be between 1 and 65535")
    return port


@dataclass(frozen=True)
class AppConfig:
    environment: str
    data_home: str | None
    backup_home: str | None
    log_file: str | None
    bind_host: str
    port: int
    allowed_hosts: tuple[str, ...]
    expected_tailscale_login: str | None
    require_tailscale_identity: bool

    @property
    def production(self) -> bool:
        return self.environment == "production"


def load() -> AppConfig:
    environment = (_env("CC_ENV", "development") or "development").lower()
    if environment not in _ENVIRONMENTS:
        raise ConfigError("CC_ENV must be development, test, or production")

    bind_host = _env("CC_BIND_HOST", "127.0.0.1") or "127.0.0.1"
    if environment == "production" and bind_host not in {"127.0.0.1", "::1"}:
        raise ConfigError("production bind host must be loopback")

    configured_hosts = tuple(
        item.strip().lower()
        for item in (_env("CC_ALLOWED_HOSTS", "") or "").split(",")
        if item.strip()
    )
    expected_login = _env("CC_EXPECTED_TS_LOGIN")
    require_identity = _bool(
        "CC_REQUIRE_TS_IDENTITY",
        default=environment == "production",
    )
    if require_identity and not expected_login:
        raise ConfigError(
            "CC_EXPECTED_TS_LOGIN is required when Tailscale identity verification is enabled"
        )

    return AppConfig(
        environment=environment,
        data_home=_env("CC_DATA_DIR") or _env("CC_HOME"),
        backup_home=_env("CC_BACKUP_DIR"),
        log_file=_env("CC_LOG_FILE"),
        bind_host=bind_host,
        port=_port(),
        allowed_hosts=configured_hosts,
        expected_tailscale_login=expected_login,
        require_tailscale_identity=require_identity,
    )
