"""
数据沉淀服务 —— 统一处理写入 ca_feed / sender_stats / price_snapshots
与交易逻辑解耦，异常不影响主流程
"""
import json
import logging
from datetime import datetime
from database import AsyncSessionLocal, CaFeed, SenderStats, PriceSnapshot
from sqlalchemy import select

logger = logging.getLogger(__name__)


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safe_int(v, default=0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


async def record_ca_feed(
    msg: dict,
    filter_passed: bool,
    filter_reason: str,
    bought: bool = False,
    position_id: int = 0,
):
    """把 WS 推送消息写入 ca_feed 表。

    如果 msg 中带有 _feed_id（由 ca_listener 提前写入），则更新已有记录（补充完整字段）；
    否则新建记录（兼容未经 ca_listener 的直接调用）。
    """
    from services.trade_engine import _parse_current_multiple

    ca = (msg.get("ca") or msg.get("token") or msg.get("address") or "").strip()
    chain = (msg.get("chain") or "").upper()
    if not ca:
        return

    feed_id = msg.get("_feed_id")

    try:
        async with AsyncSessionLocal() as session:
            if feed_id:
                # 更新已有记录（ca_listener 已写入轻量字段，这里补完整字段）
                existing = await session.get(CaFeed, feed_id)
                if existing:
                    existing.token_name = str(msg.get("name") or "")
                    existing.symbol = str(msg.get("symbol") or "")
                    existing.sender = str(msg.get("cxr") or "")
                    existing.sender_win_rate = _safe_float(msg.get("sender_win_rate"))
                    existing.sender_group_win_rate = _safe_float(msg.get("sender_group_win_rate"))
                    existing.sender_total_tokens = _safe_int(msg.get("sender_total_tokens"))
                    existing.sender_win_tokens = _safe_int(msg.get("sender_win_tokens"))
                    existing.sender_best_multiple = _safe_float(msg.get("sender_best_multiple"))
                    existing.current_multiple = _parse_current_multiple(str(msg.get("cxrzf", "0x")))
                    existing.bqfc = _safe_int(msg.get("bqfc"))
                    existing.qwfc = _safe_int(msg.get("qwfc"))
                    existing.fgq = _safe_int(msg.get("fgq"))
                    existing.grcxcs = _safe_int(msg.get("grcxcs"))
                    existing.price_usd = _safe_float(msg.get("current_price_usd"))
                    existing.market_cap = _safe_float(msg.get("market_cap"))
                    existing.tvl = _safe_float(msg.get("main_pair_tvl"))
                    existing.holders = _safe_int(msg.get("holders"))
                    existing.price_change_5m = _safe_float(msg.get("price_change_5m"))
                    existing.price_change_1h = _safe_float(msg.get("price_change_1h"))
                    existing.price_change_4h = _safe_float(msg.get("price_change_4h"))
                    existing.price_change_24h = _safe_float(msg.get("price_change_24h"))
                    existing.buy_volume_1h = _safe_float(msg.get("buy_volume_u_1h"))
                    existing.sell_volume_1h = _safe_float(msg.get("sell_volume_u_1h"))
                    existing.buy_volume_24h = _safe_float(msg.get("buy_volume_u_24h"))
                    existing.sell_volume_24h = _safe_float(msg.get("sell_volume_u_24h"))
                    existing.buys_tx_1h = _safe_int(msg.get("buys_tx_1h_count"))
                    existing.sells_tx_1h = _safe_int(msg.get("sells_tx_1h_count"))
                    existing.buys_tx_24h = _safe_int(msg.get("buys_tx_24h_count"))
                    existing.sells_tx_24h = _safe_int(msg.get("sells_tx_24h_count"))
                    existing.risk_score = _safe_float(msg.get("risk_score"))
                    existing.risk_level = _safe_int(msg.get("risk_level"))
                    existing.is_honeypot = str(msg.get("is_honeypot") or "-1")
                    existing.is_mintable = str(msg.get("is_mintable") or "0")
                    existing.max_holder_pct = _safe_float(msg.get("zzb"))
                    existing.filter_passed = filter_passed
                    existing.filter_reason = filter_reason
                    if bought:
                        existing.bought = True
                    if position_id:
                        existing.position_id = position_id
                    existing.raw_json = json.dumps(msg, ensure_ascii=False, default=str)
                    await session.commit()
                    return feed_id
            # 没有 feed_id，新建（兼容）
            feed = CaFeed(
                received_at=datetime.utcnow(),
                ca=ca,
                chain=chain,
                token_name=str(msg.get("name") or ""),
                symbol=str(msg.get("symbol") or ""),
                sender=str(msg.get("cxr") or ""),
                sender_win_rate=_safe_float(msg.get("sender_win_rate")),
                sender_group_win_rate=_safe_float(msg.get("sender_group_win_rate")),
                sender_total_tokens=_safe_int(msg.get("sender_total_tokens")),
                sender_win_tokens=_safe_int(msg.get("sender_win_tokens")),
                sender_best_multiple=_safe_float(msg.get("sender_best_multiple")),
                current_multiple=_parse_current_multiple(str(msg.get("cxrzf", "0x"))),
                bqfc=_safe_int(msg.get("bqfc")),
                qwfc=_safe_int(msg.get("qwfc")),
                fgq=_safe_int(msg.get("fgq")),
                grcxcs=_safe_int(msg.get("grcxcs")),
                price_usd=_safe_float(msg.get("current_price_usd")),
                market_cap=_safe_float(msg.get("market_cap")),
                tvl=_safe_float(msg.get("main_pair_tvl")),
                holders=_safe_int(msg.get("holders")),
                price_change_5m=_safe_float(msg.get("price_change_5m")),
                price_change_1h=_safe_float(msg.get("price_change_1h")),
                price_change_4h=_safe_float(msg.get("price_change_4h")),
                price_change_24h=_safe_float(msg.get("price_change_24h")),
                buy_volume_1h=_safe_float(msg.get("buy_volume_u_1h")),
                sell_volume_1h=_safe_float(msg.get("sell_volume_u_1h")),
                buy_volume_24h=_safe_float(msg.get("buy_volume_u_24h")),
                sell_volume_24h=_safe_float(msg.get("sell_volume_u_24h")),
                buys_tx_1h=_safe_int(msg.get("buys_tx_1h_count")),
                sells_tx_1h=_safe_int(msg.get("sells_tx_1h_count")),
                buys_tx_24h=_safe_int(msg.get("buys_tx_24h_count")),
                sells_tx_24h=_safe_int(msg.get("sells_tx_24h_count")),
                risk_score=_safe_float(msg.get("risk_score")),
                risk_level=_safe_int(msg.get("risk_level")),
                is_honeypot=str(msg.get("is_honeypot") or "-1"),
                is_mintable=str(msg.get("is_mintable") or "0"),
                max_holder_pct=_safe_float(msg.get("zzb")),
                filter_passed=filter_passed,
                filter_reason=filter_reason,
                bought=bought,
                position_id=position_id,
                raw_json=json.dumps(msg, ensure_ascii=False, default=str),
            )
            session.add(feed)
            await session.commit()
            return feed.id
    except Exception as e:
        logger.error(f"record_ca_feed error: {e}")
        return None


async def update_sender_stats(sender: str, msg: dict, bought: bool = False):
    """更新发币人统计"""
    if not sender:
        return
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SenderStats).where(SenderStats.sender == sender)
            )
            stats = result.scalar_one_or_none()
            if not stats:
                stats = SenderStats(sender=sender, first_seen=datetime.utcnow())
                session.add(stats)

            stats.total_pushed = (stats.total_pushed or 0) + 1
            stats.last_seen = datetime.utcnow()
            stats.ws_win_rate = _safe_float(msg.get("sender_win_rate"))
            stats.ws_total_tokens = _safe_int(msg.get("sender_total_tokens"))
            stats.ws_best_multiple = _safe_float(msg.get("sender_best_multiple"))
            if bought:
                stats.total_bought = (stats.total_bought or 0) + 1

            await session.commit()
    except Exception as e:
        logger.error(f"update_sender_stats error: {e}")


async def update_sender_trade_result(sender: str, pnl_pct: float, pnl_usdt: float):
    """交易结束后更新发币人盈亏统计"""
    if not sender:
        return
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SenderStats).where(SenderStats.sender == sender)
            )
            stats = result.scalar_one_or_none()
            if not stats:
                return

            if pnl_usdt >= 0:
                stats.win_count = (stats.win_count or 0) + 1
            else:
                stats.loss_count = (stats.loss_count or 0) + 1

            stats.total_pnl_usdt = (stats.total_pnl_usdt or 0.0) + pnl_usdt

            if pnl_pct > (stats.best_pnl_pct or 0.0):
                stats.best_pnl_pct = pnl_pct
            if pnl_pct < (stats.worst_pnl_pct or 0.0):
                stats.worst_pnl_pct = pnl_pct

            total = (stats.win_count or 0) + (stats.loss_count or 0)
            if total > 0:
                stats.avg_pnl_pct = (stats.total_pnl_usdt or 0.0) / total

            await session.commit()
    except Exception as e:
        logger.error(f"update_sender_trade_result error: {e}")


async def record_price_snapshot(
    position_id: int,
    ca: str,
    chain: str,
    price: float,
    pnl_pct: float = 0.0,
    event_type: str = "",
):
    """写入价格快照"""
    try:
        async with AsyncSessionLocal() as session:
            snap = PriceSnapshot(
                position_id=position_id,
                ca=ca,
                chain=chain,
                price=price,
                timestamp=datetime.utcnow(),
                event_type=event_type,
                pnl_pct=pnl_pct,
            )
            session.add(snap)
            await session.commit()
    except Exception as e:
        logger.error(f"record_price_snapshot error: {e}")
