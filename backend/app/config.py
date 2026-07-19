import os
from pydantic_settings import BaseSettings, SettingsConfigDict

# Get the path to .env in the project root relative to this file
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
env_file_path = os.path.join(root_dir, ".env")

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./sales_intel.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    
    JWT_SECRET_KEY: str = "supersecretkeychangeinproduction"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    @property
    def async_database_url(self) -> str:
        url = self.DATABASE_URL
        # Convert standard postgres URIs to asyncpg
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    model_config = SettingsConfigDict(
        env_file=env_file_path,
        extra="ignore"
    )

settings = Settings()
