from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MP2_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./mp2-dev.db"
    object_store: str = "local"
    local_object_root: Path = Path(".mp2-objects")
    ingest_root: Path = Path("/media")
    s3_endpoint: str = "http://localhost:8333"
    s3_access_key: str = "mp2-dev"
    s3_secret_key: str = "change-me"
    s3_region: str = "us-east-1"
    temporal_address: str = "localhost:7233"
    external_models_enabled: bool = False


settings = Settings()
