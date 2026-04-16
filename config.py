from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr
from typing import List

class Settings(BaseSettings):
    TURN_SECRET: SecretStr
    CAPABILITY_SECRET: SecretStr | None = None
    ALLOWED_ORIGINS: List[str] = ["http://localhost:8000", "http://127.0.0.1:8000","https://2p2p.ru"]
    
    model_config: SettingsConfigDict = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8'
    )
    
config = Settings()