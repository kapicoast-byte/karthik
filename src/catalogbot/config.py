"""Runtime configuration, loaded from the environment (see .env.example)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Slack
    slack_bot_token: str = ""
    slack_user_token: str = ""
    slack_signing_secret: str = ""
    slack_app_token: str = ""
    slack_use_socket_mode: bool = True
    slack_owner_user_id: str = ""
    slack_channel_allowlist: str = ""

    # Language model. `llm_provider` picks the backend in llm.py:
    #   anthropic  → the Anthropic API
    #   openrouter → OpenRouter, or any OpenAI-compatible endpoint
    llm_provider: str = "anthropic"
    classifier_model: str = "claude-opus-5"

    anthropic_api_key: str = ""

    openai_api_key: str = ""
    openai_base_url: str = "https://openrouter.ai/api/v1"

    # Plane
    plane_base_url: str = "https://api.plane.so"
    plane_api_key: str = ""
    plane_workspace_slug: str = ""
    plane_project_id: str = ""

    # Runtime
    database_url: str = "sqlite:///./catalogbot.db"
    timezone: str = "Asia/Kolkata"
    daily_digest_cron: str = "0 8 * * 1-5"
    follow_up_after_days: int = Field(default=2, ge=1)
    log_level: str = "INFO"

    @property
    def channel_allowlist(self) -> set[str]:
        """Channel IDs we are permitted to read. Empty set means 'no filter'."""
        return {c.strip() for c in self.slack_channel_allowlist.split(",") if c.strip()}


settings = Settings()
