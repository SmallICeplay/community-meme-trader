"""
钱包加密存储与管理
- 助记词用 Fernet（AES-128-CBC + HMAC）加密，密钥由系统主密码派生
- 私钥只在内存中临时解密，不落盘
"""
import hashlib
import hmac
import struct
import logging
import os
import base64
from cryptography.fernet import Fernet
from mnemonic import Mnemonic

logger = logging.getLogger(__name__)

EVM_CHAINS = {"BSC", "ETH", "XLAYER"}

# ── 加密工具 ──────────────────────────────────────────────────

def _make_fernet(master_password: str) -> Fernet:
    """从主密码派生 Fernet 密钥（PBKDF2-SHA256，32字节）"""
    key = hashlib.pbkdf2_hmac(
        "sha256",
        master_password.encode(),
        b"holdo_wallet_salt_v1",
        iterations=200_000,
        dklen=32,
    )
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_mnemonic(mnemonic: str, master_password: str) -> str:
    """加密助记词，返回 base64 密文字符串"""
    f = _make_fernet(master_password)
    return f.encrypt(mnemonic.encode()).decode()


def decrypt_mnemonic(ciphertext: str, master_password: str) -> str:
    """解密助记词"""
    f = _make_fernet(master_password)
    return f.decrypt(ciphertext.encode()).decode()


# ── 助记词生成与验证 ──────────────────────────────────────────

def generate_mnemonic() -> str:
    """生成新的 12 词助记词"""
    return Mnemonic("english").generate(strength=128)


def validate_mnemonic(mnemonic: str) -> bool:
    return Mnemonic("english").check(mnemonic.strip())


# ── 派生逻辑（与旧 wallet_manager 相同，提取为独立函数）──────

def _derive_ed25519_slip10(seed: bytes, path: list[int]) -> bytes:
    def hmac_sha512(key: bytes, data: bytes) -> bytes:
        return hmac.new(key, data, hashlib.sha512).digest()
    result = hmac_sha512(b"ed25519 seed", seed)
    key, chain = result[:32], result[32:]
    for segment in path:
        hardened = segment | 0x80000000
        data = b"\x00" + key + struct.pack(">I", hardened)
        result = hmac_sha512(chain, data)
        key, chain = result[:32], result[32:]
    return key


def derive_sol_wallet(mnemonic: str) -> dict:
    import base58
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    seed = Mnemonic("english").to_seed(mnemonic.strip())
    priv_bytes = _derive_ed25519_slip10(seed, [44, 501, 0, 0])
    priv_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    pub_bytes = priv_key.public_key().public_bytes_raw()
    return {"address": base58.b58encode(pub_bytes).decode(), "private_key": priv_bytes.hex()}


def derive_evm_wallet(mnemonic: str) -> dict:
    from eth_account import Account
    Account.enable_unaudited_hdwallet_features()
    acct = Account.from_mnemonic(mnemonic.strip(), account_path="m/44'/60'/0'/0/0")
    return {"address": acct.address, "private_key": acct.key.hex()}


def derive_wallet(mnemonic: str, chain: str) -> dict:
    if chain == "SOL":
        return derive_sol_wallet(mnemonic)
    elif chain in EVM_CHAINS:
        return derive_evm_wallet(mnemonic)
    raise ValueError(f"不支持的链: {chain}")


def derive_all_addresses(mnemonic: str) -> dict[str, str]:
    result = {}
    for chain in ["SOL", "BSC", "ETH", "XLAYER"]:
        try:
            result[chain] = derive_wallet(mnemonic, chain)["address"]
        except Exception as e:
            result[chain] = f"Error: {e}"
    return result


# ── WalletManager：从 DB 读取加密助记词 ───────────────────────

class WalletManager:
    def __init__(self):
        self._cache: dict[str, dict] = {}  # chain -> {address, private_key}
        self._mnemonic_cache: str | None = None  # 解密后的明文助记词（内存中）

    def clear_cache(self):
        """钱包更换时清空缓存"""
        self._cache.clear()
        self._mnemonic_cache = None

    async def _load_mnemonic(self) -> str:
        """从 DB 读取并解密助记词"""
        if self._mnemonic_cache:
            return self._mnemonic_cache

        from database import AsyncSessionLocal, ConfigModel
        from sqlalchemy import select
        from config import get_settings

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ConfigModel).where(ConfigModel.key == "wallet_encrypted_mnemonic")
            )
            row = result.scalar_one_or_none()
            if not row or not row.value:
                raise ValueError("系统内尚未配置钱包，请先在「钱包管理」页面新建或导入")

            password = get_settings().wallet_master_password
            if not password:
                raise ValueError("WALLET_MASTER_PASSWORD 未设置，请在 .env 中配置")

            self._mnemonic_cache = decrypt_mnemonic(row.value, password)
            return self._mnemonic_cache

    async def get_wallet_async(self, chain: str) -> dict:
        if chain in self._cache:
            return self._cache[chain]
        mnemonic = await self._load_mnemonic()
        wallet = derive_wallet(mnemonic, chain)
        self._cache[chain] = wallet
        return wallet

    async def get_address_async(self, chain: str) -> str:
        return (await self.get_wallet_async(chain))["address"]

    async def get_all_addresses_async(self) -> dict[str, str]:
        try:
            mnemonic = await self._load_mnemonic()
            return derive_all_addresses(mnemonic)
        except Exception as e:
            return {c: f"Error: {e}" for c in ["SOL", "BSC", "ETH", "XLAYER"]}

    # 同步接口保留兼容（供非 async 场景）
    def get_address(self, chain: str) -> str:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self.get_address_async(chain))


wallet_manager = WalletManager()
