from pydantic_settings import BaseSettings
from functools import lru_cache


import os as _os
from pydantic import ConfigDict

_ENV_FILE = _os.path.join(_os.path.dirname(__file__), "..", ".env")


class Settings(BaseSettings):
    model_config = ConfigDict(extra="ignore", env_file=_ENV_FILE, env_file_encoding="utf-8")

    ave_api_key: str = ""
    ave_base_url: str = "https://bot-api.ave.ai"
    wallet_mnemonic: str = ""           # 旧字段保留兼容，新方案用 DB 存加密助记词
    wallet_master_password: str = "holdo_default_change_me"  # 加密钱包的主密码
    ca_ws_url: str = "ws://43.254.167.238:3000/token"
    backend_port: int = 9000
    database_url: str = "sqlite+aiosqlite:///./meme_trader.db"

    # ── 管理员认证 ────────────────────────────────────────────────
    admin_password: str = ""            # 管理员密码，空=禁用管理员保护（开发模式）
    jwt_secret: str = "holdo_jwt_secret_change_me"  # JWT 签名密钥，生产必须修改
    jwt_expire_hours: int = 24          # token 有效期（小时）

    # ── 演示钱包 ──────────────────────────────────────────────────
    demo_wallet_mnemonic: str = ""      # 演示钱包助记词，用 WALLET_MASTER_PASSWORD 加密后存 DB


@lru_cache()
def get_settings() -> Settings:
    return Settings()
