"""
持仓接口
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db, Position, CaFeed, TokenDetail, AsyncSessionLocal, Trade
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/positions", tags=["positions"])


async def _get_token_meta(db: AsyncSession, ca: str) -> dict:
    """从 token_detail（AVE真实数据）或 ca_feed 提取代币名称/symbol/logo"""
    try:
        result = await db.execute(
            select(TokenDetail).where(TokenDetail.ca == ca)
            .order_by(TokenDetail.fetched_at.desc()).limit(1)
        )
        td = result.scalars().first()
        if td and (td.token_name or td.symbol):
            return {"token_name": td.token_name or "", "symbol": td.symbol or "", "logo_url": ""}
    except Exception:
        pass

    try:
        result = await db.execute(
            select(CaFeed).where(CaFeed.ca == ca).order_by(CaFeed.received_at.desc()).limit(1)
        )
        feed = result.scalars().first()
        if feed:
            name = (feed.token_name or "").replace("*", "").strip()
            symbol = (feed.symbol or "").replace("*", "").strip()
            logo_url = ""
            if feed.raw_json:
                raw = json.loads(feed.raw_json)
                logo_url = raw.get("logo_url", "") or ""
                if not name:
                    name = (raw.get("name", "") or "").replace("*", "").strip()
                if not symbol:
                    symbol = (raw.get("symbol", "") or "").replace("*", "").strip()
            return {"token_name": name, "symbol": symbol, "logo_url": logo_url}
    except Exception:
        pass
    return {"token_name": "", "symbol": "", "logo_url": ""}


@router.get("")
async def get_open_positions(db: AsyncSession = Depends(get_db)):
    import hashlib
    def _hash(s):
        if not s: return None
        return hashlib.md5(s.encode('utf-8', errors='replace')).hexdigest()[:4].upper()

    result = await db.execute(
        select(Position).where(Position.status == "open").order_by(Position.open_time.desc())
    )
    positions = result.scalars().all()

    # 批量查各持仓 CA 最近3条喊单记录
    ca_list = [p.ca for p in positions]
    callers_map: dict[str, list] = {}
    if ca_list:
        feeds_result = await db.execute(
            select(CaFeed.ca, CaFeed.sender, CaFeed.raw_json)
            .where(CaFeed.ca.in_(ca_list))
            .order_by(CaFeed.received_at.desc())
        )
        for row in feeds_result:
            ca = row.ca
            sender_raw = row.sender or ""
            group_raw = ""
            try:
                raw = json.loads(row.raw_json or "{}")
                if not sender_raw:
                    sender_raw = raw.get("qy_name", "") or ""
                group_raw = raw.get("qun_name", "") or ""
            except Exception:
                pass
            entry = {"s": _hash(sender_raw), "g": _hash(group_raw)}
            if ca not in callers_map:
                callers_map[ca] = []
            if len(callers_map[ca]) < 3:
                callers_map[ca].append(entry)

    out = []
    for p in positions:
        meta = await _get_token_meta(db, p.ca)
        d = _serialize(p, meta)
        d["callers"] = callers_map.get(p.ca, [])
        out.append(d)
    return out


@router.delete("/{position_id}")
async def close_position_manually(position_id: int, db: AsyncSession = Depends(get_db)):
    """手动触发卖出"""
    from services.position_monitor import _sell_position
    from services.ave_client import ave_client

    pos = await db.get(Position, position_id)
    if not pos or pos.status != "open":
        return {"success": False, "error": "持仓不存在或已关闭"}

    current_price = await ave_client.get_price(pos.ca, pos.chain)
    await _sell_position(pos, "manual", current_price or pos.current_price)
    return {"success": True}


# ── 链上余额查询（模块级，供多处复用） ────────────────────────────────────────
async def _get_chain_balance(ca: str, wallet_address: str, chain: str):
    import httpx
    RPC_MAP = {
        "BSC": "https://bsc-dataseed1.binance.org",
        "ETH": "https://ethereum-rpc.publicnode.com",
    }
    rpc = RPC_MAP.get(chain.upper(), "https://bsc-dataseed1.binance.org")
    addr_pad = wallet_address[2:].zfill(64) if wallet_address.startswith("0x") else wallet_address.zfill(64)
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r_bal = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":ca,"data":"0x70a08231"+addr_pad},"latest"],"id":1})
            r_dec = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":ca,"data":"0x313ce567"},"latest"],"id":2})
        bal_hex = r_bal.json().get("result","0x0") or "0x0"
        dec_hex = r_dec.json().get("result","0x12") or "0x12"
        raw = int(bal_hex, 16) if bal_hex and bal_hex != "0x" else 0
        dec = int(dec_hex, 16) if dec_hex and dec_hex != "0x" else 18
        return raw, dec
    except Exception:
        return 0, 18


# ── 扫描状态（防止重复启动） ──────────────────────────────────────────────────
_sweep_running = False


async def _run_sweep_background():
    """后台扫描残留代币并卖出，进度通过 broadcaster 推送到实时日志"""
    global _sweep_running
    if _sweep_running:
        from services.broadcaster import broadcaster
        broadcaster.log("⚠ 扫描已在运行中，请勿重复启动", level="warn")
        return
    _sweep_running = True

    import asyncio
    from services.ave_client import ave_client
    from services.wallet_manager import wallet_manager
    from services.broadcaster import broadcaster

    broadcaster.log("🔍 开始扫描残留代币...", level="info")

    try:
        # 查所有 closed/sell_failed 持仓，按 CA 去重
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Position).where(
                    Position.status.in_(["closed", "sell_failed"])
                ).order_by(Position.open_time.desc())
            )
            all_positions = result.scalars().all()

        seen = set()
        candidates = []
        for p in all_positions:
            key = (p.ca.lower(), p.chain.upper())
            if key not in seen and p.chain.upper() in ("BSC", "ETH"):
                seen.add(key)
                candidates.append(p)

        broadcaster.log(f"🔍 共 {len(candidates)} 个CA待查（已按CA去重）", level="info")

        sold_count = 0
        fail_count = 0
        found_count = 0

        for i, pos in enumerate(candidates):
            ca = pos.ca
            chain = pos.chain

            try:
                wallet_address = await wallet_manager.get_address_async(chain)
            except Exception:
                continue

            raw_balance, decimals = await _get_chain_balance(ca, wallet_address, chain)

            if raw_balance <= 0:
                continue  # 余额为0，跳过

            actual_balance = raw_balance / (10 ** decimals)
            found_count += 1
            broadcaster.log(f"💰 [{i+1}/{len(candidates)}] 发现余额: {ca[:12]}... {actual_balance:.4f} tokens [{chain}]", level="warn")

            # 尝试卖出
            try:
                result_sell = await ave_client.sell(
                    ca=ca, chain=chain,
                    token_amount=actual_balance,
                    wallet_address=wallet_address,
                )
                if result_sell.get("success"):
                    usdt_received = float(result_sell.get("usdt_received", 0))
                    tx = result_sell.get("tx", "")
                    # 补记一条 trade
                    async with AsyncSessionLocal() as session:
                        trade = Trade(
                            position_id=pos.id,
                            ca=ca, chain=chain,
                            entry_price=pos.entry_price,
                            exit_price=float(result_sell.get("price", 0)),
                            amount_usdt=pos.amount_usdt,
                            pnl_usdt=usdt_received - pos.amount_usdt,
                            pnl_pct=(usdt_received - pos.amount_usdt) / pos.amount_usdt * 100 if pos.amount_usdt > 0 else 0,
                            reason="sweep",
                            open_time=pos.open_time,
                            close_time=datetime.utcnow(),
                            buy_tx=pos.buy_tx or "",
                            sell_tx=tx,
                            gas_fee_usd=0.0,
                        )
                        session.add(trade)
                        await session.commit()
                    broadcaster.log(f"✅ 卖出成功: {ca[:12]}... 回收 {usdt_received:.4f}U  tx={tx[:16]}", level="info")
                    sold_count += 1
                else:
                    err = str(result_sell.get("msg") or "")[:80]
                    broadcaster.log(f"❌ 卖出失败: {ca[:12]}... {err}", level="error")
                    fail_count += 1
            except Exception as e:
                err = str(e)[:80]
                broadcaster.log(f"❌ 卖出异常: {ca[:12]}... {err}", level="error")
                fail_count += 1

            await asyncio.sleep(0.3)  # 控制频率

        broadcaster.log(
            f"🔍 扫描完成！发现余额: {found_count}个  卖出成功: {sold_count}个  失败: {fail_count}个",
            level="info" if fail_count == 0 else "warn",
        )

    except Exception as e:
        broadcaster.log(f"🔍 扫描异常: {e}", level="error")
        logger.exception("sweep background error")
    finally:
        _sweep_running = False


class SellBatchItem(BaseModel):
    ca: str
    chain: str
    token_amount: float


class SellBatchRequest(BaseModel):
    items: list[SellBatchItem]


@router.get("/balances")
async def get_wallet_balances(db: AsyncSession = Depends(get_db)):
    """扫描钱包中仍有余额的历史代币（并发查链，BSC/ETH）"""
    import asyncio
    from services.ave_client import ave_client
    from services.wallet_manager import wallet_manager

    # 查所有历史持仓，按(ca, chain)去重，只看BSC/ETH
    result = await db.execute(
        select(Position).where(
            Position.status.in_(["closed", "sell_failed"])
        ).order_by(Position.open_time.desc())
    )
    all_positions = result.scalars().all()

    seen: set = set()
    candidates = []
    for p in all_positions:
        key = (p.ca.lower(), p.chain.upper())
        if key not in seen and p.chain.upper() in ("BSC", "ETH"):
            seen.add(key)
            candidates.append(p)

    # 预先获取钱包地址（按链缓存）
    addr_cache: dict = {}
    for chain in ("BSC", "ETH"):
        try:
            addr_cache[chain] = await wallet_manager.get_address_async(chain)
        except Exception:
            addr_cache[chain] = None

    async def _check_one(p: Position):
        wallet_address = addr_cache.get(p.chain.upper())
        if not wallet_address:
            return None
        raw_balance, decimals = await _get_chain_balance(p.ca, wallet_address, p.chain)
        if raw_balance <= 0:
            return None
        actual_balance = raw_balance / (10 ** decimals)
        return (p, actual_balance)

    # 并发查所有CA余额（20路并发）
    semaphore = asyncio.Semaphore(20)
    async def _check_limited(p):
        async with semaphore:
            return await _check_one(p)

    balance_results = await asyncio.gather(*[_check_limited(p) for p in candidates])
    found = [(p, bal) for r in balance_results if r for p, bal in [r]]

    # 并发查价格（对有余额的代币）
    async def _get_price_safe(ca, chain):
        try:
            return await ave_client.get_price(ca, chain) or 0.0
        except Exception:
            return 0.0

    price_results = await asyncio.gather(*[_get_price_safe(p.ca, p.chain) for p, _ in found])

    out = []
    for (p, actual_balance), current_price in zip(found, price_results):
        entry_price = p.entry_price or 0.0
        amount_usdt = p.amount_usdt or 0.0
        pnl_pct = 0.0
        pnl_usdt = 0.0
        if entry_price > 0 and current_price > 0:
            pnl_pct = (current_price - entry_price) / entry_price * 100
            pnl_usdt = (current_price - entry_price) * actual_balance

        meta = await _get_token_meta(db, p.ca)
        out.append({
            "ca": p.ca,
            "chain": p.chain,
            "token_amount": actual_balance,
            "entry_price": entry_price,
            "current_price": current_price,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usdt": round(pnl_usdt, 4),
            "amount_usdt_at_buy": amount_usdt,
            "token_name": meta.get("token_name", ""),
            "symbol": meta.get("symbol", ""),
            "logo_url": meta.get("logo_url", ""),
        })
        await asyncio.sleep(0.05)

    return out


@router.post("/sell_batch")
async def sell_batch(body: SellBatchRequest):
    """批量卖出指定代币"""
    from services.ave_client import ave_client
    from services.wallet_manager import wallet_manager
    import asyncio

    results = []
    for item in body.items:
        try:
            wallet_address = await wallet_manager.get_address_async(item.chain)
            r = await ave_client.sell(
                ca=item.ca,
                chain=item.chain,
                token_amount=item.token_amount,
                wallet_address=wallet_address,
            )
            results.append({
                "ca": item.ca,
                "chain": item.chain,
                "success": r.get("success", False),
                "usdt_received": float(r.get("usdt_received", 0)),
                "tx": r.get("tx", ""),
                "error": "" if r.get("success") else str(r.get("msg", "")),
            })
        except Exception as e:
            results.append({
                "ca": item.ca,
                "chain": item.chain,
                "success": False,
                "usdt_received": 0.0,
                "tx": "",
                "error": str(e)[:200],
            })
        await asyncio.sleep(0.2)

    return {"results": results}


def _serialize(p: Position, meta: dict = None) -> dict:
    now = datetime.utcnow()
    hold_minutes = (now - p.open_time).total_seconds() / 60
    pnl_pct = 0.0
    if p.entry_price > 0 and p.current_price > 0:
        pnl_pct = (p.current_price - p.entry_price) / p.entry_price * 100
    pnl_usdt = (p.current_price - p.entry_price) * p.token_amount if p.token_amount > 0 else 0

    return {
        "id": p.id,
        "ca": p.ca,
        "chain": p.chain,
        "entry_price": p.entry_price,
        "current_price": p.current_price,
        "peak_price": p.peak_price,
        "amount_usdt": p.amount_usdt,
        "token_amount": p.token_amount,
        "buy_tx": p.buy_tx,
        "open_time": p.open_time.isoformat() + "Z",
        "hold_minutes": round(hold_minutes, 1),
        "pnl_usdt": round(pnl_usdt, 4),
        "pnl_pct": round(pnl_pct, 2),
        "status": p.status,
        "gas_fee_usd": round(getattr(p, 'gas_fee_usd', None) or 0, 4),
        "token_name": (meta or {}).get("token_name", ""),
        "symbol": (meta or {}).get("symbol", ""),
        "logo_url": (meta or {}).get("logo_url", ""),
    }
