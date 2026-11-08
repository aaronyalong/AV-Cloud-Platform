"""API settings, read from environment variables (run with --env-file .env)."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Connects as av_app, which is subject to row-level security.
    app_database_url: str
    mongo_url: str
    mongo_db: str
