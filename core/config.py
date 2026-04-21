from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://bps:bps@localhost:5432/brandprotection"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    secret_key: str = "change-me-in-production"

    # Screenshots
    screenshot_dir: str = "/tmp/screenshots"
    screenshot_base_url: str = "http://localhost:8000/screenshots"

    # Integrations
    abuseipdb_api_key: str = ""
    certstream_url: str = "wss://certstream.calidog.io"

    # Logging
    log_level: str = "INFO"


settings = Settings()
