from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./cabinet.db"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    log_level: str = "info"
    extraction_max_chars: int = 50000
    categorization_batch_size: int = 10

    model_config = {"env_prefix": "CABINET_"}


def get_settings() -> Settings:
    return Settings()
