"""Gateway settings, read from the environment.

Secrets (the Bright Data key, the KV token) exist only here, in the project's
environment variables. Nothing in this package logs a value from this object.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional

from .errors import ConfigError

# The dataset `config.py` in the main repo defaults to.
DEFAULT_DATASET_ID = "gd_l1viktl72bvl7bjuj0"


def _int(env: Mapping[str, str], name: str, default: str, minimum: int = 0) -> int:
    raw = (env.get(name) or "").strip() or default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} is not a whole number") from None
    if value < minimum:
        raise ConfigError(f"{name} is below its minimum")
    return value


def _decimal(env: Mapping[str, str], name: str, default: str, minimum: str = "0") -> Decimal:
    raw = (env.get(name) or "").strip() or default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ConfigError(f"{name} is not a number") from None
    if not value.is_finite() or value < Decimal(minimum):
        raise ConfigError(f"{name} is out of range")
    return value


@dataclass(frozen=True)
class Config:
    brightdata_api_key: Optional[str]
    brightdata_dataset_id: str
    kv_url: Optional[str]
    kv_token: Optional[str]
    allow_memory_store: bool
    daily_quota: int
    monthly_cap_usd: Decimal
    cost_per_record_usd: Decimal
    ip_per_minute: int
    auth_cache_seconds: int
    github_timeout: float
    upstream_timeout: float

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        cost = _decimal(env, "ART_GATEWAY_COST_PER_RECORD_USD", "0.0015")
        if cost <= 0:
            raise ConfigError("ART_GATEWAY_COST_PER_RECORD_USD must be above zero")
        return cls(
            brightdata_api_key=(env.get("BRIGHTDATA_API_KEY") or "").strip() or None,
            brightdata_dataset_id=(env.get("BRIGHTDATA_LINKEDIN_DATASET_ID") or "").strip()
            or DEFAULT_DATASET_ID,
            # Vercel's KV / Upstash integrations set one of these two pairs.
            kv_url=(env.get("KV_REST_API_URL") or env.get("UPSTASH_REDIS_REST_URL") or "").strip()
            or None,
            kv_token=(env.get("KV_REST_API_TOKEN") or env.get("UPSTASH_REDIS_REST_TOKEN") or "").strip()
            or None,
            allow_memory_store=(env.get("ART_GATEWAY_ALLOW_MEMORY_STORE") or "") == "1",
            daily_quota=_int(env, "ART_GATEWAY_DAILY_QUOTA", "3"),
            monthly_cap_usd=_decimal(env, "ART_GATEWAY_MONTHLY_CAP_USD", "20"),
            cost_per_record_usd=cost,
            ip_per_minute=_int(env, "ART_GATEWAY_IP_PER_MINUTE", "10"),
            auth_cache_seconds=_int(env, "ART_GATEWAY_AUTH_CACHE_SECONDS", "300"),
            github_timeout=float(_decimal(env, "ART_GATEWAY_GITHUB_TIMEOUT", "8", "1")),
            upstream_timeout=float(_decimal(env, "ART_GATEWAY_UPSTREAM_TIMEOUT", "280", "1")),
        )

    @property
    def max_calls_per_month(self) -> int:
        """How many records the monthly cap pays for."""
        return int(self.monthly_cap_usd // self.cost_per_record_usd)
