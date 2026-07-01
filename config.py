import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr
from typing import List

class Settings(BaseSettings):
    TURN_SECRET: SecretStr
    CAPABILITY_SECRET: SecretStr | None = None
    ALLOWED_ORIGINS: List[str] = ["http://localhost:8000", "http://127.0.0.1:8000","https://2p2p.ru"]
    TRUST_PROXY_HEADERS: bool = True
    RELAY_LIMIT_ENABLED: bool = False
    RELAY_WINDOW_SECONDS: int = 10
    RELAY_MAX_BYTES_PER_WINDOW: int = 100 * 1024 * 1024 * 1024
    
    POSTGRESQL_HOST: str
    POSTGRESQL_DATABASE: str
    POSTGRESQL_USER: str
    POSTGRESQL_PASSWORD: SecretStr
    POSTGRESQL_DATABASE_APEX: str
    POSTGRESQL_SSLMODE: str = "disable"

    @property
    def TILDA_APEX_DATABASE_TARGET(self) -> str:
        return os.getenv("TILDA_APEX_DATABASE_TARGET", "apex").strip() or "apex"
    
    model_config: SettingsConfigDict = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )
    
config = Settings()