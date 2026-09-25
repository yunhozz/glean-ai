from functools import lru_cache
from pathlib import Path
from typing import Any
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
    reddit_user_agent: str = "glean-ai/0.1 (contact: github.com/yunhozz/glean-ai)"
    github_enabled: bool = True
    huggingface_enabled: bool = True
    reddit_enabled: bool = True
    source_limit: int = 100
    request_timeout_seconds: float = 20
    timezone: str = "Asia/Seoul"
    report_hour: int = 8
    report_top_n: int = 10
    interest_config_path: Path = Path("config/interests.yaml")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def _interest_config(self) -> dict[str, Any]:
        with self.interest_config_path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def interests(self) -> dict[str, list[str]]:
        data = self._interest_config()
        return {
            key: list(data.get(key, []))
            for key in ("keywords", "accounts", "repositories", "subreddits")
        }

    def rss_feeds(self) -> list[dict[str, str]]:
        data = self._interest_config()
        return [
            {
                "id": str(source["id"]),
                "name": str(source["name"]),
                "url": str(source["url"]),
            }
            for key in ("ai_news_feeds", "tech_blogs")
            for source in data.get(key, [])
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
