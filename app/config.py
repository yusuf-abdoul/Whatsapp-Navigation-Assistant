from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    channel_provider: Literal["twilio", "meta"] = "twilio"

    meta_phone_number_id: str = ""
    meta_access_token: str = ""
    meta_webhook_verify_token: str = ""
    meta_app_secret: str = ""

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""

    locationiq_key: str = ""

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://wna:wna_dev@localhost:5432/wna"

    session_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    rate_limit_per_hour: int = Field(default=30, ge=1)
    rate_limit_per_day: int = Field(default=100, ge=1)
    identical_query_cooldown_seconds: int = Field(default=5, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
