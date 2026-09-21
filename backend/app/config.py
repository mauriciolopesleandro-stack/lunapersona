from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    comfyui_url: str = "http://127.0.0.1:8188"
    runpod_pod_id: str = ""
    comfyui_api_key: str = ""

    llm_api_url: str = ""
    llm_api_key: str = ""

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    comfyui_connect_timeout: float = 10.0
    comfyui_generation_timeout: float = 300.0

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def workflows_dir(self) -> Path:
        return REPO_ROOT / "workflows"

    @property
    def models_registry_path(self) -> Path:
        return REPO_ROOT / "models" / "registry.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
