"""Database connection settings, read from the same env vars as `compose.yaml`."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    database: str
    user: str
    password: str

    @property
    def url(self) -> str:
        return (
            f"postgresql+psycopg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


def load_database_settings() -> DatabaseSettings:
    """Defaults match `.env.example`: a non-secret password for local development only."""
    return DatabaseSettings(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ.get("POSTGRES_DB", "company_memory"),
        user=os.environ.get("POSTGRES_USER", "company_memory"),
        password=os.environ.get("POSTGRES_PASSWORD", "local_development_only"),
    )
