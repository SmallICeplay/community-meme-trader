from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    ave_api_key: str = ""
    ave_base_url: str = "https://bot-api.ave.ai"
    wallet_mnemonic: str = ""           # 旧字段保留兼容，新方案用 DB 存加密助记词
    wallet_master_password: str = "holdo_default_change_me"  # 加密钱包的主密码
    ca_ws_url: str = "ws://43.254.167.238:3000/token"
    backend_port: int = 8000
    database_url: str = "sqlite+aiosqlite:///./meme_trader.db"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
