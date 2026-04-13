"""
分析数据 API
提供漏斗统计、链分布、发币人排行、P&L 曲线、CA 流水列表、持仓价格曲线
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, Integer
from datetime import datetime, timedelta
import hashlib
from database import get_db, CaFeed, SenderStats, Trade, Position, PriceSnapshot


def _short_hash(s: str, length: int = 4) -> str:
    """把字符串哈希成固定长度的大写十六进制编号，用于隐藏真实名称"""
    if not s:
        return "????"
    return hashlib.md5(s.encode('utf-8', errors='replace')).hexdigest()[:length].upper()

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


# ── 漏斗统计 ──────────────────────────────────────────────────────────────────
@router.get("/funnel")
async def get_funnel(days: int = 7, db: AsyncSession = Depends(get_db)):
    """
    返回过去 N 天的漏斗数据:
    total_received → filter_passed → bought → profitable
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    total_result = await db.execute(
        select(func.count()).select_from(CaFeed).where(CaFeed.received_at >= cutoff)
    )
    total = total_result.scalar() or 0

    passed_result = await db.execute(
        select(func.count()).select_from(CaFeed).where(
            CaFeed.received_at >= cutoff, CaFeed.filter_passed == True
        )
    )
    passed = passed_result.scalar() or 0

    bought_result = await db.execute(
        select(func.count()).select_from(CaFeed).where(
            CaFeed.received_at >= cutoff, CaFeed.bought == True
        )
    )
    bought = bought_result.scalar() or 0

    # 盈利笔数（只统计有真实 tx 的交易）
    profit_result = await db.execute(
        select(func.count()).select_from(Trade).where(
            Trade.close_time >= cutoff, Trade.pnl_usdt > 0,
            (Trade.buy_tx != '') | (Trade.sell_tx != '')
        )
    )
    profitable = profit_result.scalar() or 0

    # 亏损笔数（只统计有真实 tx 的交易）
    loss_result = await db.execute(
        select(func.count()).select_from(Trade).where(
            Trade.close_time >= cutoff, Trade.pnl_usdt <= 0,
            (Trade.buy_tx != '') | (Trade.sell_tx != '')
        )
    )
    loss = loss_result.scalar() or 0

    # 过滤通过但未买入（passed 但 bought=False）
    not_bought_result = await db.execute(
        select(func.count()).select_from(CaFeed).where(
            CaFeed.received_at >= cutoff,
            CaFeed.filter_passed == True,
            CaFeed.bought == False,
        )
    )
    not_bought = not_bought_result.scalar() or 0

    # 按拦截原因分组统计
    reason_result = await db.execute(
        select(CaFeed.filter_reason, func.count().label("cnt"))
        .where(CaFeed.received_at >= cutoff, CaFeed.filter_passed == False)
        .group_by(CaFeed.filter_reason)
        .order_by(desc("cnt"))
        .limit(10)
    )
    filter_breakdown = [{"reason": r, "count": c} for r, c in reason_result.all()]

    return {
        "days": days,
        "total_received": total,
        "filter_passed": passed,
        "not_bought": not_bought,
        "bought": bought,
        "profitable": profitable,
        "loss": loss,
        "filter_breakdown": filter_breakdown,
    }


# ── 链分布 ────────────────────────────────────────────────────────────────────
@router.get("/chain_distribution")
async def get_chain_distribution(days: int = 7, db: AsyncSession = Depends(get_db)):
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(CaFeed.chain, func.count().label("cnt"))
        .where(CaFeed.received_at >= cutoff)
        .group_by(CaFeed.chain)
        .order_by(desc("cnt"))
    )
    rows = result.all()

    bought_result = await db.execute(
        select(CaFeed.chain, func.count().label("cnt"))
        .where(CaFeed.received_at >= cutoff, CaFeed.bought == True)
        .group_by(CaFeed.chain)
    )
    bought_map = {r.chain: r.cnt for r in bought_result.all()}

    return [
        {
            "chain": r.chain or "UNKNOWN",
            "total": r.cnt,
            "bought": bought_map.get(r.chain, 0),
        }
        for r in rows
    ]


# ── 发币人排行 ─────────────────────────────────────────────────────────────────
@router.get("/sender_leaderboard")
async def get_sender_leaderboard(
    limit: int = 50,
    sort_by: str = Query("total_pushed", enum=["total_pnl_usdt", "win_count", "total_pushed", "ws_win_rate", "ws_total_tokens"]),
    days: int = 30,
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(days=days)

    # 从 CaFeed 聚合每个发币人的推送统计（包含没有买入记录的）
    feed_result = await db.execute(
        select(
            CaFeed.sender,
            func.count().label("feed_total"),
            func.sum(func.cast(CaFeed.bought, Integer)).label("feed_bought"),
            func.max(CaFeed.sender_win_rate).label("ws_win_rate"),
            func.max(CaFeed.sender_total_tokens).label("ws_total_tokens"),
            func.max(CaFeed.sender_best_multiple).label("ws_best_multiple"),
            func.max(CaFeed.received_at).label("last_seen"),
        )
        .where(CaFeed.received_at >= cutoff, CaFeed.sender != "")
        .group_by(CaFeed.sender)
    )
    feed_rows = {r.sender: r for r in feed_result.all()}

    # 从 SenderStats 取本地交易结果
    stats_result = await db.execute(select(SenderStats))
    stats_map = {r.sender: r for r in stats_result.scalars().all()}

    # 合并两个数据源
    senders = set(feed_rows.keys()) | set(stats_map.keys())
    merged = []
    for sender in senders:
        f = feed_rows.get(sender)
        s = stats_map.get(sender)
        if not f and not s:
            continue
        feed_total = int(f.feed_total) if f else (s.total_pushed or 0)
        feed_bought = int(f.feed_bought or 0) if f else (s.total_bought or 0)
        win_count = s.win_count or 0 if s else 0
        loss_count = s.loss_count or 0 if s else 0
        total_closed = win_count + loss_count
        merged.append({
            "sender": sender,
            "total_pushed": feed_total,
            "total_bought": feed_bought,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_count / total_closed * 100, 1) if total_closed > 0 else 0,
            "total_pnl_usdt": round(s.total_pnl_usdt or 0, 2) if s else 0,
            "best_pnl_pct": round(s.best_pnl_pct or 0, 1) if s else 0,
            "worst_pnl_pct": round(s.worst_pnl_pct or 0, 1) if s else 0,
            "ws_win_rate": float(f.ws_win_rate or 0) if f else float(s.ws_win_rate or 0 if s else 0),
            "ws_total_tokens": int(f.ws_total_tokens or 0) if f else int(s.ws_total_tokens or 0 if s else 0),
            "ws_best_multiple": float(f.ws_best_multiple or 0) if f else float(s.ws_best_multiple or 0 if s else 0),
            "last_seen": f.last_seen.isoformat() if f and f.last_seen else (s.last_seen.isoformat() if s and s.last_seen else None),
            "has_trade": feed_bought > 0,
        })

    # 排序
    sort_key_map = {
        "total_pushed": lambda x: x["total_pushed"],
        "total_pnl_usdt": lambda x: x["total_pnl_usdt"],
        "win_count": lambda x: x["win_count"],
        "ws_win_rate": lambda x: x["ws_win_rate"],
        "ws_total_tokens": lambda x: x["ws_total_tokens"],
    }
    merged.sort(key=sort_key_map.get(sort_by, lambda x: x["total_pushed"]), reverse=True)
    return merged[:limit]


# ── P&L 时间序列 ──────────────────────────────────────────────────────────────
@router.get("/pnl_series")
async def get_pnl_series(days: int = 30, db: AsyncSession = Depends(get_db)):
    """返回每日累计 P&L 数据点，用于折线图"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(Trade)
        .where(
            Trade.close_time >= cutoff,
            (Trade.buy_tx != '') | (Trade.sell_tx != '')
        )
        .order_by(Trade.close_time)
    )
    trades = result.scalars().all()

    cumulative = 0.0
    series = []
    for t in trades:
        cumulative += t.pnl_usdt
        series.append({
            "time": t.close_time.isoformat(),
            "pnl_usdt": round(t.pnl_usdt, 4),
            "cumulative_pnl": round(cumulative, 4),
            "reason": t.reason,
            "ca": t.ca,
            "chain": t.chain,
        })

    # 统计汇总
    total_pnl = sum(t.pnl_usdt for t in trades)
    win_trades = [t for t in trades if t.pnl_usdt > 0]
    loss_trades = [t for t in trades if t.pnl_usdt <= 0]

    return {
        "series": series,
        "summary": {
            "total_trades": len(trades),
            "win_trades": len(win_trades),
            "loss_trades": len(loss_trades),
            "win_rate": round(len(win_trades) / len(trades) * 100, 1) if trades else 0,
            "total_pnl_usdt": round(total_pnl, 4),
            "avg_pnl_usdt": round(total_pnl / len(trades), 4) if trades else 0,
            "best_trade": round(max((t.pnl_usdt for t in trades), default=0), 4),
            "worst_trade": round(min((t.pnl_usdt for t in trades), default=0), 4),
        },
    }


# ── CA 流水列表 ───────────────────────────────────────────────────────────────
@router.get("/ca_feed")
async def get_ca_feed(
    page: int = 1,
    page_size: int = 50,
    chain: str = "",
    filter_passed: str = "",   # "true" | "false" | ""
    bought: str = "",          # "true" | "false" | ""
    days: int = 7,
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    q = select(CaFeed).where(CaFeed.received_at >= cutoff)

    if chain:
        q = q.where(CaFeed.chain == chain.upper())
    if filter_passed == "true":
        q = q.where(CaFeed.filter_passed == True)
    elif filter_passed == "false":
        q = q.where(CaFeed.filter_passed == False)
    if bought == "true":
        q = q.where(CaFeed.bought == True)
    elif bought == "false":
        q = q.where(CaFeed.bought == False)

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar() or 0

    q = q.order_by(desc(CaFeed.received_at)).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    rows = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "data": [
            {
                "id": r.id,
                "received_at": r.received_at.isoformat(),
                "ca": r.ca,
                "chain": r.chain,
                "token_name": r.token_name,
                "symbol": r.symbol,
                "sender": r.sender,
                "sender_win_rate": r.sender_win_rate,
                "current_multiple": r.current_multiple,
                "bqfc": r.bqfc,
                "qwfc": r.qwfc,
                "fgq": r.fgq,
                "market_cap": r.market_cap,
                "holders": r.holders,
                "risk_score": r.risk_score,
                "grcxcs": r.grcxcs,
                "filter_passed": r.filter_passed,
                "filter_reason": r.filter_reason,
                "bought": r.bought,
                "position_id": r.position_id,
            }
            for r in rows
        ],
    }


@router.get("/recent_signals")
async def get_recent_signals(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """最近信号流，用于首页展示。sender/group 脱敏为哈希编号。"""
    import json as _json
    q = select(CaFeed).order_by(desc(CaFeed.received_at)).limit(limit)
    result = await db.execute(q)
    rows = result.scalars().all()

    # 批量查每条记录的 CA 是第几次出现（在整个 ca_feed 表中的历史推送次数）
    ca_list = list({r.ca for r in rows})
    ca_counts: dict[str, int] = {}
    if ca_list:
        count_q = select(CaFeed.ca, func.count().label("cnt")).where(CaFeed.ca.in_(ca_list)).group_by(CaFeed.ca)
        count_result = await db.execute(count_q)
        ca_counts = {row.ca: row.cnt for row in count_result}

    out = []
    for r in rows:
        # 从 raw_json 补读 qun_name（社区）和 qy_name（喊单人）及胜率
        group_raw = ""
        sender_raw = r.sender or ""
        win_rate = r.sender_win_rate or 0.0
        try:
            raw = _json.loads(r.raw_json or "{}")
            group_raw = raw.get("qun_name", "") or ""
            if not sender_raw:
                sender_raw = raw.get("qy_name", "") or ""
            if not win_rate:
                win_rate = float(raw.get("sender_win_rate") or 0)
        except Exception:
            pass

        out.append({
            "id": r.id,
            "received_at": r.received_at.isoformat(),
            "ca": r.ca,
            "chain": r.chain,
            "symbol": r.symbol or r.token_name or "",
            "push_count": ca_counts.get(r.ca, 1),   # 我们自己统计的总推送次数
            "sender_id": _short_hash(sender_raw) if sender_raw else None,
            "sender_win_rate": round(win_rate, 1) if win_rate else None,
            "group_id": _short_hash(group_raw) if group_raw else None,
            "filter_passed": r.filter_passed,
            "bought": r.bought,
        })
    return out


# ── 持仓价格曲线 ───────────────────────────────────────────────────────────────
@router.get("/price_curve/{position_id}")
async def get_price_curve(position_id: int, db: AsyncSession = Depends(get_db)):
    """返回某持仓的完整价格快照数组，用于画 K 线/折线图"""
    result = await db.execute(
        select(PriceSnapshot)
        .where(PriceSnapshot.position_id == position_id)
        .order_by(PriceSnapshot.timestamp)
    )
    snaps = result.scalars().all()

    pos_result = await db.execute(select(Position).where(Position.id == position_id))
    pos = pos_result.scalar_one_or_none()

    return {
        "position": {
            "id": pos.id,
            "ca": pos.ca,
            "chain": pos.chain,
            "entry_price": pos.entry_price,
            "amount_usdt": pos.amount_usdt,
            "status": pos.status,
            "open_time": pos.open_time.isoformat() if pos.open_time else None,
        } if pos else None,
        "snapshots": [
            {
                "timestamp": s.timestamp.isoformat(),
                "price": s.price,
                "pnl_pct": s.pnl_pct,
                "event_type": s.event_type,
            }
            for s in snaps
        ],
    }


# ── 整体统计汇总（Dashboard 用） ──────────────────────────────────────────────
@router.get("/summary")
async def get_summary(db: AsyncSession = Depends(get_db)):
    """供 Dashboard 顶部卡片使用的汇总数据"""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # 今日 P&L（只统计有真实 tx 的交易）
    today_pnl_result = await db.execute(
        select(func.sum(Trade.pnl_usdt)).where(
            Trade.close_time >= today,
            (Trade.buy_tx != '') | (Trade.sell_tx != '')
        )
    )
    today_pnl = today_pnl_result.scalar() or 0.0

    # 总 P&L（只统计有真实 tx 的交易）
    total_pnl_result = await db.execute(
        select(func.sum(Trade.pnl_usdt)).where(
            (Trade.buy_tx != '') | (Trade.sell_tx != '')
        )
    )
    total_pnl = total_pnl_result.scalar() or 0.0

    # 总交易次数（只统计有真实 tx 的交易）
    total_trades_result = await db.execute(
        select(func.count()).select_from(Trade).where(
            (Trade.buy_tx != '') | (Trade.sell_tx != '')
        )
    )
    total_trades = total_trades_result.scalar() or 0

    # 胜率（只统计有真实 tx 的交易）
    win_result = await db.execute(
        select(func.count()).select_from(Trade).where(
            Trade.pnl_usdt > 0,
            (Trade.buy_tx != '') | (Trade.sell_tx != '')
        )
    )
    win_count = win_result.scalar() or 0
    win_rate = round(win_count / total_trades * 100, 1) if total_trades > 0 else 0

    # 今日收到 CA 数
    today_ca_result = await db.execute(
        select(func.count()).select_from(CaFeed).where(CaFeed.received_at >= today)
    )
    today_ca = today_ca_result.scalar() or 0

    # 开仓数
    open_pos_result = await db.execute(
        select(func.count()).select_from(Position).where(Position.status == "open")
    )
    open_positions = open_pos_result.scalar() or 0

    return {
        "today_pnl_usdt": round(today_pnl, 4),
        "total_pnl_usdt": round(total_pnl, 4),
        "total_trades": total_trades,
        "win_count": win_count,
        "win_rate": win_rate,
        "today_ca_received": today_ca,
        "open_positions": open_positions,
    }


# ── 钱包资产总览 ───────────────────────────────────────────────────────────────
CHAIN_NATIVE = {
    "SOL": "SOL",
    "BSC": "BNB",
    "ETH": "ETH",
    "XLAYER": "OKB",
}

@router.get("/portfolio")
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    """
    返回各链钱包地址 + 主链余额 + 持仓代币估值
    主链余额调用 AVE Bot API（点击刷新时触发）
    持仓估值根据 position_price_source 配置决定实时/缓存
    """
    import asyncio
    from services.wallet_manager import wallet_manager
    from services.ave_client import ave_client
    from database import ConfigModel

    # 读配置
    cfg_result = await db.execute(select(ConfigModel))
    cfg = {r.key: r.value for r in cfg_result.scalars().all()}
    price_source = cfg.get("position_price_source", "cached")

    # 获取各链地址
    try:
        addresses = await wallet_manager.get_all_addresses_async()
    except Exception:
        addresses = {}

    # 读取所有开仓持仓，按链分组
    pos_result = await db.execute(
        select(Position).where(Position.status == "open")
    )
    open_positions = pos_result.scalars().all()

    # 如果需要实时价格，并发拉取所有持仓的当前价格
    price_cache: dict[str, float] = {}
    if price_source == "realtime" and open_positions:
        async def fetch_price(pos: Position):
            try:
                p = await ave_client.get_price(pos.ca, pos.chain)
                return pos.ca, p
            except Exception:
                return pos.ca, pos.current_price
        results = await asyncio.gather(*[fetch_price(p) for p in open_positions])
        price_cache = dict(results)

    # 按链整理持仓数据
    chain_positions: dict[str, list] = {}
    total_position_value = 0.0
    for pos in open_positions:
        price = price_cache.get(pos.ca, pos.current_price) if price_source == "realtime" else pos.current_price
        price = price or pos.entry_price or 0.0
        value_usdt = price * pos.token_amount if price > 0 else pos.amount_usdt
        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100 if pos.entry_price > 0 and price > 0 else 0.0
        total_position_value += value_usdt

        chain = pos.chain
        if chain not in chain_positions:
            chain_positions[chain] = []
        chain_positions[chain].append({
            "id": pos.id,
            "ca": pos.ca,
            "entry_price": pos.entry_price,
            "current_price": price,
            "token_amount": pos.token_amount,
            "amount_usdt": pos.amount_usdt,
            "value_usdt": round(value_usdt, 4),
            "pnl_pct": round(pnl_pct, 2),
            "open_time": pos.open_time.isoformat() if pos.open_time else None,
        })

    # 并发查询各链主链余额
    CHAINS = ["SOL", "BSC", "ETH", "XLAYER"]

    # ── 公链 RPC 配置 ─────────────────────────────────────────────────────────
    # EVM 链：用 eth_getBalance (JSON-RPC) 查主链币，用 eth_call 查 USDT 余额
    EVM_RPC = {
        "BSC":    "https://bsc-dataseed1.binance.org",
        "ETH":    "https://ethereum-rpc.publicnode.com",
        "XLAYER": "https://rpc.xlayer.tech",
    }
    # 各链 USDT 合约地址 + decimals
    USDT_CONTRACT = {
        "BSC": ("0x55d398326f99059fF775485246999027B3197955", 18),   # BSC-USDT (BEP20, 18位)
        "ETH": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),    # ETH-USDT (ERC20, 6位)
    }
    SOL_RPC = "https://api.mainnet-beta.solana.com"

    async def _evm_rpc(rpc_url: str, payload: dict) -> dict:
        """通用 EVM JSON-RPC 调用"""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.post(rpc_url, json=payload)
                return r.json()
        except Exception:
            return {}

    async def fetch_evm_native(chain: str, addr: str):
        """查 EVM 主链原生币余额，返回 float (ether 单位)"""
        rpc = EVM_RPC.get(chain)
        if not rpc or not addr:
            return None
        try:
            data = await _evm_rpc(rpc, {
                "jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
                "params": [addr, "latest"]
            })
            hex_val = data.get("result", "0x0")
            return int(hex_val, 16) / 1e18
        except Exception:
            return None

    async def fetch_evm_token(chain: str, addr: str, token_contract: str, decimals: int = 6):
        """查 ERC20 token 余额（balanceOf），返回 float"""
        rpc = EVM_RPC.get(chain)
        if not rpc or not addr:
            return None
        # balanceOf(address) = keccak256 前4字节 + 32字节地址
        padded = addr.lower().replace("0x", "").zfill(64)
        data_hex = "0x70a08231" + padded
        try:
            resp = await _evm_rpc(rpc, {
                "jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": token_contract, "data": data_hex}, "latest"]
            })
            hex_val = resp.get("result", "0x0")
            return int(hex_val, 16) / (10 ** decimals)
        except Exception:
            return None

    async def fetch_sol_balance(addr: str):
        """查 SOL 主链余额（lamports → SOL）"""
        if not addr:
            return None
        import httpx
        try:
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.post(SOL_RPC, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getBalance",
                    "params": [addr]
                })
                val = r.json().get("result", {}).get("value", None)
                return val / 1e9 if val is not None else None
        except Exception:
            return None

    async def fetch_balance(chain: str):
        addr = addresses.get(chain, "")
        if not addr:
            return chain, {"native": None, "usdt": None}
        try:
            if chain == "SOL":
                native = await fetch_sol_balance(addr)
                return chain, {"native": native, "usdt": None}
            else:
                native, usdt = await asyncio.gather(
                    fetch_evm_native(chain, addr),
                    fetch_evm_token(chain, addr, USDT_CONTRACT[chain][0], USDT_CONTRACT[chain][1]) if chain in USDT_CONTRACT else asyncio.sleep(0),
                )
                return chain, {"native": native, "usdt": usdt if chain in USDT_CONTRACT else None}
        except Exception:
            return chain, {"native": None, "usdt": None}

    balance_results = await asyncio.gather(*[fetch_balance(c) for c in CHAINS])
    balance_map = dict(balance_results)

    # 组装结果
    chains_data = []
    for chain in CHAINS:
        addr = addresses.get(chain, "")
        bal_info = balance_map.get(chain, {})
        native_bal = bal_info.get("native") if isinstance(bal_info, dict) else None
        usdt_bal = bal_info.get("usdt") if isinstance(bal_info, dict) else None
        positions_in_chain = chain_positions.get(chain, [])
        chain_pos_value = sum(p["value_usdt"] for p in positions_in_chain)

        chains_data.append({
            "chain": chain,
            "native_symbol": CHAIN_NATIVE.get(chain, chain),
            "address": addr,
            "native_balance": round(native_bal, 8) if native_bal is not None else None,
            "usdt_balance": round(usdt_bal, 4) if usdt_bal is not None else None,
            "positions": positions_in_chain,
            "position_count": len(positions_in_chain),
            "position_value_usdt": round(chain_pos_value, 4),
        })

    return {
        "price_source": price_source,
        "total_position_value_usdt": round(total_position_value, 4),
        "chains": chains_data,
    }
