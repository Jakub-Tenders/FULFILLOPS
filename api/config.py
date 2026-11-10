from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    database_url: str = "postgresql+psycopg://fulfillops:fulfillops@localhost:5432/fulfillops"
    worker_poll_interval_seconds: float = 1.0
    worker_max_attempts: int = 3

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
