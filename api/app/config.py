"""API settings, read from environment variables (run with --env-file .env)."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Connects as av_app, which is subject to row-level security.
    app_database_url: str
    mongo_url: str
    mongo_db: str
    # Local stand-in for S3. Without a signing key, a random one is used, so
    # download links stop working when the API restarts.
    media_dir: Path = Path("media")
    media_signing_key: str | None = None
