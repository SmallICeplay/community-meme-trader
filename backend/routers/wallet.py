"""
钱包管理接口：新建、导入、查看地址、删除
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db, ConfigModel
from services.wallet_manager import (
    wallet_manager,
    generate_mnemonic,
    validate_mnemonic,
    encrypt_mnemonic,
    decrypt_mnemonic,
    derive_all_addresses,
)
from config import get_settings

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


def _get_password() -> str:
    pw = get_settings().wallet_master_password
    if not pw:
        raise HTTPException(500, "WALLET_MASTER_PASSWORD 未配置")
    return pw


async def _get_encrypted(db: AsyncSession) -> str | None:
    result = await db.execute(
        select(ConfigModel).where(ConfigModel.key == "wallet_encrypted_mnemonic")
    )
    row = result.scalar_one_or_none()
    return row.value if row else None


async def _save_encrypted(db: AsyncSession, ciphertext: str):
    result = await db.execute(
        select(ConfigModel).where(ConfigModel.key == "wallet_encrypted_mnemonic")
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = ciphertext
    else:
        db.add(ConfigModel(key="wallet_encrypted_mnemonic", value=ciphertext))
    await db.commit()
    # 清空内存缓存，下次交易重新加载
    wallet_manager.clear_cache()


# ── 新建钱包 ──────────────────────────────────────────────────

@router.post("/create")
async def create_wallet(db: AsyncSession = Depends(get_db)):
    """生成新助记词并加密保存，返回助记词（仅此一次，请立即备份）"""
    existing = await _get_encrypted(db)
    if existing:
        raise HTTPException(400, "钱包已存在，请先删除再新建（操作不可逆）")

    mnemonic = generate_mnemonic()
    ciphertext = encrypt_mnemonic(mnemonic, _get_password())
    await _save_encrypted(db, ciphertext)

    # 派生地址
    addresses = derive_all_addresses(mnemonic)
    return {
        "success": True,
        "mnemonic": mnemonic,   # 仅此一次返回，请备份！
        "addresses": addresses,
        "warning": "请立即抄写助记词并妥善保管，系统不会再次显示！",
    }


# ── 导入钱包 ──────────────────────────────────────────────────

class ImportRequest(BaseModel):
    mnemonic: str
    force: bool = False  # 强制覆盖已有钱包


@router.post("/import")
async def import_wallet(body: ImportRequest, db: AsyncSession = Depends(get_db)):
    """导入已有助记词"""
    mnemonic = body.mnemonic.strip()
    if not validate_mnemonic(mnemonic):
        raise HTTPException(400, "助记词无效，请检查单词数量和拼写")

    existing = await _get_encrypted(db)
    if existing and not body.force:
        raise HTTPException(400, "钱包已存在，传入 force=true 覆盖（旧钱包资产请先转出）")

    ciphertext = encrypt_mnemonic(mnemonic, _get_password())
    await _save_encrypted(db, ciphertext)

    addresses = derive_all_addresses(mnemonic)
    return {"success": True, "addresses": addresses}


# ── 查看地址 ──────────────────────────────────────────────────

@router.get("/addresses")
async def get_addresses(db: AsyncSession = Depends(get_db)):
    """获取各链钱包地址（不含私钥）"""
    encrypted = await _get_encrypted(db)
    if not encrypted:
        return {"exists": False, "addresses": {}}
    try:
        mnemonic = decrypt_mnemonic(encrypted, _get_password())
        addresses = derive_all_addresses(mnemonic)
        return {"exists": True, "addresses": addresses}
    except Exception as e:
        raise HTTPException(500, f"解密失败，请检查 WALLET_MASTER_PASSWORD: {e}")


# ── 删除钱包 ──────────────────────────────────────────────────

@router.delete("/delete")
async def delete_wallet(db: AsyncSession = Depends(get_db)):
    """删除钱包（操作不可逆，请确保资产已转出）"""
    result = await db.execute(
        select(ConfigModel).where(ConfigModel.key == "wallet_encrypted_mnemonic")
    )
    row = result.scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    wallet_manager.clear_cache()
    return {"success": True}


# ── 检查钱包是否存在 ──────────────────────────────────────────

@router.get("/status")
async def wallet_status(db: AsyncSession = Depends(get_db)):
    encrypted = await _get_encrypted(db)
    if not encrypted:
        return {"exists": False}
    try:
        mnemonic = decrypt_mnemonic(encrypted, _get_password())
        addresses = derive_all_addresses(mnemonic)
        return {"exists": True, "addresses": addresses}
    except Exception:
        return {"exists": True, "addresses": {}, "error": "解密失败，主密码可能已变更"}
