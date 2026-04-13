"""
交易历史接口
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database import get_db, Trade, CaFeed, TokenDetail
import json

router = APIRouter(prefix="/api/trades", tags=["trades"])


async def _get_token_meta_batch(db: AsyncSession, cas: list[str]) -> dict[str, dict]:
    """批量查代币元信息，优先 token_detail（AVE真实数据），fallback ca_feed"""
    if not cas:
        return {}

    meta: dict[str, dict] = {}

    # 第一步：从 token_detail 批量查（AVE API 数据，未打码）
    try:
        result = await db.execute(
            select(TokenDetail).where(TokenDetail.ca.in_(cas))
            .order_by(TokenDetail.fetched_at.desc())
        )
        for td in result.scalars().all():
            if td.ca not in meta and (td.token_name or td.symbol):
                meta[td.ca] = {"token_name": td.token_name or "", "symbol": td.symbol or "", "logo_url": ""}
    except Exception:
        pass

    # 第二步：未命中的从 ca_feed 补充
    missing = [ca for ca in cas if ca not in meta]
    if missing:
        try:
            result = await db.execute(
                select(CaFeed).where(CaFeed.ca.in_(missing)).order_by(CaFeed.received_at.desc())
            )
            for feed in result.scalars().all():
                if feed.ca in meta:
                    continue
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
                meta[feed.ca] = {"token_name": name, "symbol": symbol, "logo_url": logo_url}
        except Exception:
            pass

    return meta


@router.get("/history")
async def get_trade_history(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Trade).where(
            (Trade.buy_tx != '') | (Trade.sell_tx != '')
        ).order_by(Trade.close_time.desc()).limit(limit).offset(offset)
    )
    trades = result.scalars().all()
    cas = list({t.ca for t in trades})
    meta_map = await _get_token_meta_batch(db, cas)
    return [_serialize(t, meta_map.get(t.ca, {})) for t in trades]


@router.get("/stats")
async def get_trade_stats(db: AsyncSession = Depends(get_db)):
    """统计数据：总盈亏、胜率等（只统计有真实 tx 的交易）"""
    result = await db.execute(select(Trade))
    trades = result.scalars().all()

    # 只统计有真实链上 tx 的交易（buy_tx 不为空）
    trades = [t for t in trades if getattr(t, 'buy_tx', '') or getattr(t, 'sell_tx', '')]

    if not trades:
        return {
            "total_trades": 0,
            "win_trades": 0,
            "loss_trades": 0,
            "win_rate": 0,
            "total_pnl_usdt": 0,
            "total_invested": 0,
        }

    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_usdt > 0)
    total_pnl = sum(t.pnl_usdt for t in trades)
    total_invested = sum(t.amount_usdt for t in trades)
    total_gas = sum(getattr(t, 'gas_fee_usd', 0) or 0 for t in trades)

    return {
        "total_trades": total,
        "win_trades": wins,
        "loss_trades": total - wins,
        "win_rate": round(wins / total * 100, 1),
        "total_pnl_usdt": round(total_pnl, 4),
        "total_invested": round(total_invested, 4),
        "total_gas_usd": round(total_gas, 4),
    }


def _serialize(t: Trade, meta: dict = None) -> dict:
    return {
        "id": t.id,
        "position_id": t.position_id,
        "ca": t.ca,
        "chain": t.chain,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "amount_usdt": t.amount_usdt,
        "pnl_usdt": round(t.pnl_usdt, 4),
        "pnl_pct": round(t.pnl_pct, 2),
        "reason": t.reason,
        "open_time": t.open_time.isoformat() + "Z",
        "close_time": t.close_time.isoformat() + "Z",
        "buy_tx": getattr(t, 'buy_tx', '') or '',
        "sell_tx": getattr(t, 'sell_tx', '') or '',
        "gas_fee_usd": round(getattr(t, 'gas_fee_usd', 0) or 0, 4),
        "token_name": (meta or {}).get("token_name", ""),
        "symbol": (meta or {}).get("symbol", ""),
        "logo_url": (meta or {}).get("logo_url", ""),
    }
