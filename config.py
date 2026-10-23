from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr
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
    POSTGRESQL_DATABASE_REAL_BABY: str
    POSTGRESQL_DATABASE_AMA_LIVING: str
    POSTGRESQL_DATABASE_WAYFS: str
    POSTGRESQL_DATABASE_FERALRU: str
    POSTGRESQL_DATABASE_SHELWEAR: str
    TILDA_APEX_DATABASE_TARGET: str = "apex"
    TILDA_REAL_BABY_DATABASE_TARGET: str = "real_baby"
    TILDA_AMA_LIVING_DATABASE_TARGET: str = "ama_living"
    TILDA_WAYFS_DATABASE_TARGET: str = "wayfs"
    TILDA_FERALRU_DATABASE_TARGET: str = "feralru"
    TILDA_SHELWEAR_DATABASE_TARGET: str = "shelwear"
    POSTGRESQL_SSLMODE: str = "disable"
    POSTGRESQL_POOL_TIMEOUT_SECONDS: float = Field(default=3, gt=0, le=30)
    POSTGRESQL_CONNECT_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=30)
    POSTGRESQL_STATEMENT_TIMEOUT_MS: int = Field(default=10000, ge=100, le=120000)
    TILDA_QUEUE_PATH: str = "var/tilda-outbox.sqlite3"
    
    SMTP_PASSWORD: SecretStr
    
    POSTGRESQL_DATABASES_JSON: str = ""
    CDEK_ACCOUNTS_JSON: str = ""
    TILDA_PROJECTS_JSON: str = ""

    VOLLEYBALL_ENABLED: bool = True
    VOLLEYBALL_MAX_ROOMS: int = Field(default=100, ge=1, le=500)
    VOLLEYBALL_MAX_CONNECTIONS: int = Field(default=300, ge=2, le=2000)
    VOLLEYBALL_MAX_CONNECTIONS_PER_IP: int = Field(default=6, ge=2, le=50)
    
    model_config: SettingsConfigDict = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )
    
config = Settings()
