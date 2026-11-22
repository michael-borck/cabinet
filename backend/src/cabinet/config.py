from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./cabinet.db"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    # Shared secret required on every API request (except /health) when set.
    # The Electron main process generates one per app launch and injects it
    # into both the backend process and the renderer.
    api_token: str | None = None
    log_level: str = "info"
    extraction_max_chars: int = 50000
    categorization_batch_size: int = 10
    # Folder watching (Phase 1b). The master switch only gates the watcher
    # service; per-source ``watch_enabled`` still decides what is watched.
    watch_enabled: bool = True
    watch_debounce_seconds: float = 3.0
    watch_max_wait_seconds: float = 15.0
    # Periodic reconcile safety net for watch-enabled sources; 0 disables.
    watch_poll_interval_seconds: int = 1800
    # Phase 2a facts pass: text budget sent to the summarisation LLM.
    facts_input_max_chars: int = 12000
    # Phase 3: distinct documents before a provisional concept auto-approves.
    concept_promotion_threshold: int = 3

    model_config = {"env_prefix": "CABINET_"}


def get_settings() -> Settings:
    return Settings()
