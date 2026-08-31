from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ai_brief:ai_brief@db:5432/ai_brief"
    slack_webhook_url: SecretStr | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"
    github_token: SecretStr | None = None
    huggingface_token: SecretStr | None = None
    reddit_client_id: SecretStr | None = None
    reddit_client_secret: SecretStr | None = None
    reddit_user_agent: str = "glean-ai/0.1"
    x_bearer_token: SecretStr | None = None
    threads_access_token: SecretStr | None = None
    github_enabled: bool = True
    huggingface_enabled: bool = True
    reddit_enabled: bool = True
    x_enabled: bool = False
    threads_enabled: bool = False
    source_limit: int = 100
    request_timeout_seconds: float = 20
    timezone: str = "Asia/Seoul"
    report_hour: int = 8
    report_top_n: int = 10
    interest_config_path: Path = Path("config/interests.yaml")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def interests(self) -> dict[str, list[str]]:
        with self.interest_config_path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return {key: list(value) for key, value in data.items()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
