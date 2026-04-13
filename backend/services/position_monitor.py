"""
持仓监控：后台定时任务，轮询所有开仓，触发止盈/止损/时间限制卖出
"""
import asyncio
import logging
from datetime import datetime
from sqlalchemy import select
from database import AsyncSessionLocal, Position, Trade, ConfigModel, CaFeed
from services.ave_client import ave_client
from services.wallet_manager import wallet_manager
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)
_monitor_task: asyncio.Task | None = None

# 卖出失败计数器：pos_id -> 连续失败次数
_sell_fail_count: dict[int, int] = {}
# 暂时跳过的持仓（连续失败过多，等待下一个大周期再试）
_sell_skip_until: dict[int, float] = {}  # pos_id -> unix timestamp
# 最后一次卖出失败的原因（用于放弃关仓时显示）
_sell_last_reason: dict[int, str] = {}  # pos_id -> 中文原因

SELL_MAX_CONSECUTIVE_FAIL = 3    # 连续失败 3 次后暂时跳过
SELL_SKIP_SECONDS = 300          # 跳过 5 分钟后重试
SELL_ABANDON_FAIL = 20           # 累计失败 20 次（跨多个跳过周期）后放弃，直接关仓


async def _get_config(session) -> dict:
    result = await session.execute(select(ConfigModel))
    return {r.key: r.value for r in result.scalars().all()}


async def _abandon_position(position: Position, last_reason: str, fail_count: int):
    """累计卖出失败超限，放弃卖出并直接关仓，记录完整原因"""
    broadcaster.emit("sell_failed", {
        "ca": position.ca,
        "chain": position.chain,
        "token_name": "",
        "error": "",
        "reason": f"已放弃卖出并强制关仓（{last_reason}）",
        "sell_reason": "abandon",
        "fail_count": fail_count,
        "abandoned": True,
    }, level="error")
    broadcaster.log(
        f"🚨 放弃卖出 {position.ca[:12]}... [{position.chain}] "
        f"累计失败 {fail_count} 次 · 原因: {last_reason} · 直接关仓记损失",
        level="error",
    )
    async with AsyncSessionLocal() as session:
        pos = await session.get(Position, position.id)
        if pos and pos.status == "open":
            pos.status = "closed"
            trade = Trade(
                position_id=position.id, ca=position.ca, chain=position.chain,
                entry_price=position.entry_price, exit_price=0.0,
                amount_usdt=position.amount_usdt, pnl_usdt=-position.amount_usdt,
                pnl_pct=-100.0, reason="sell_failed",
                open_time=position.open_time, close_time=datetime.utcnow(),
                buy_tx=pos.buy_tx or "", sell_tx="", gas_fee_usd=0.0,
            )
            session.add(trade)
            await session.commit()
    _sell_fail_count.pop(position.id, None)
    _sell_skip_until.pop(position.id, None)
    _sell_last_reason.pop(position.id, None)


async def _sell_position(position: Position, reason: str, current_price: float):
    """执行卖出并记录到 trades 表。

    支持 Four.meme 防倾销场景：单次只能卖出部分 token，
    此时更新持仓 token_amount，保持 open 状态，下一轮 monitor 继续卖出剩余。
    """
    REASON_ZH = {"take_profit": "止盈", "stop_loss": "止损", "time_limit": "超时", "manual": "手动", "zero_balance": "归零"}
    reason_zh = REASON_ZH.get(reason, reason)
    broadcaster.log(f"🔔 触发{reason_zh}: {position.ca[:12]}... [{position.chain}] 现价 {current_price:.6g}")

    try:
        wallet_address = await wallet_manager.get_address_async(position.chain)
        result = await ave_client.sell(
            ca=position.ca,
            chain=position.chain,
            token_amount=position.token_amount,
            wallet_address=wallet_address,
        )
    except Exception as e:
        err_str = str(e)
        from services.trade_engine import _classify_sell_error
        reason_zh = _classify_sell_error(err_str)
        _sell_fail_count[position.id] = _sell_fail_count.get(position.id, 0) + 1
        _sell_last_reason[position.id] = reason_zh
        fail_count = _sell_fail_count[position.id]
        broadcaster.emit("sell_failed", {
            "ca": position.ca,
            "chain": position.chain,
            "token_name": "",
            "error": err_str[:200],
            "reason": reason_zh,
            "sell_reason": reason,
            "fail_count": fail_count,
        }, level="error")
        broadcaster.log(f"❌ 卖出失败 {position.ca[:12]}... [{position.chain}]: {reason_zh} — {err_str[:100]}", level="error")
        if fail_count >= SELL_ABANDON_FAIL:
            await _abandon_position(position, reason_zh, fail_count)
            return False
        if fail_count >= SELL_MAX_CONSECUTIVE_FAIL:
            import time
            _sell_skip_until[position.id] = time.time() + SELL_SKIP_SECONDS
            broadcaster.log(f"⏳ 持仓 {position.ca[:12]}... 连续卖出失败 {fail_count} 次，暂停 {SELL_SKIP_SECONDS//60} 分钟后重试", level="warn")
        return False

    if not result.get("success"):
        err_msg = str(result.get("msg") or result.get("message") or result)
        from services.trade_engine import _classify_sell_error
        reason_zh = _classify_sell_error(err_msg)
        _sell_fail_count[position.id] = _sell_fail_count.get(position.id, 0) + 1
        _sell_last_reason[position.id] = reason_zh
        fail_count = _sell_fail_count[position.id]
        broadcaster.emit("sell_failed", {
            "ca": position.ca,
            "chain": position.chain,
            "token_name": "",
            "error": err_msg[:200],
            "reason": reason_zh,
            "sell_reason": reason,
            "fail_count": fail_count,
        }, level="error")
        broadcaster.log(f"❌ 卖出未成功 {position.ca[:12]}... [{position.chain}]: {reason_zh}", level="error")
        if fail_count >= SELL_ABANDON_FAIL:
            await _abandon_position(position, reason_zh, fail_count)
            return False
        if fail_count >= SELL_MAX_CONSECUTIVE_FAIL:
            import time
            _sell_skip_until[position.id] = time.time() + SELL_SKIP_SECONDS
            broadcaster.log(f"⏳ 持仓 {position.ca[:12]}... 连续卖出失败，暂停重试", level="warn")
        return False

    exit_price = float(result.get("price", current_price))
    sell_tx = result.get("tx", "")
    usdt_received = float(result.get("usdt_received", exit_price * position.token_amount))

    # ── 检查是否部分成交（Four.meme 防倾销限制） ─────────────────────────────
    # sold_token_amount 是 sold_ratio (0.0~1.0)
    sold_ratio = float(result.get("sold_token_amount", 1.0))
    sold_ratio = max(0.0, min(1.0, sold_ratio))  # 确保在合法范围内
    remaining_token = position.token_amount * (1.0 - sold_ratio)
    # 剩余不足 0.5% 视为清仓完成
    is_partial = sold_ratio < 0.995

    if is_partial:
        sold_token_amount = position.token_amount * sold_ratio
        broadcaster.log(
            f"部分卖出: {position.ca[:12]}... 本次卖出 {sold_token_amount:.4f}，"
            f"剩余 {remaining_token:.4f}，下一轮继续清仓",
            level="warn",
        )
        # 更新持仓的剩余数量，保持 open 状态，清零失败计数
        async with AsyncSessionLocal() as session:
            pos = await session.get(Position, position.id)
            if pos and pos.status == "open":
                pos.token_amount = remaining_token
                pos.current_price = exit_price
                await session.commit()
        _sell_fail_count.pop(position.id, None)
        _sell_skip_until.pop(position.id, None)
        return True  # 返回 True 表示本批成功，但持仓未关闭

    # ── 全部卖出完成，正常关仓 ───────────────────────────────────────────────
    pnl_usdt = usdt_received - position.amount_usdt
    pnl_pct = pnl_usdt / position.amount_usdt * 100 if position.amount_usdt > 0 else 0

    # 从链上真实 receipt 计算 gas 费（买入+卖出两笔）
    gas_fee_usd = 0.0
    try:
        import httpx as _httpx
        from services.ave_client import _get_native_price_usd_async
        RPC_MAP = {
            "BSC": "https://bsc-dataseed1.binance.org",
            "ETH": "https://ethereum-rpc.publicnode.com",
        }
        chain_name_map = {"BSC": "bsc", "ETH": "eth", "SOL": "solana"}
        rpc = RPC_MAP.get(position.chain.upper(), "https://bsc-dataseed1.binance.org")
        chain_name_key = chain_name_map.get(position.chain.upper(), "bsc")
        native_price = await _get_native_price_usd_async(chain_name_key)
        async with AsyncSessionLocal() as _s:
            pos_db = await _s.get(Position, position.id)
            buy_tx_hash = pos_db.buy_tx if pos_db else ""
        for tx_hash in [buy_tx_hash, sell_tx]:
            if not tx_hash:
                continue
            try:
                async with _httpx.AsyncClient(timeout=8.0) as c:
                    r = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[tx_hash],"id":1})
                    receipt = r.json().get("result")
                    if receipt:
                        gas_used = int(receipt.get("gasUsed","0x0"), 16)
                        gp = int(receipt.get("effectiveGasPrice","0x0") or "0x0", 16)
                        gas_fee_usd += gas_used * gp / 1e18 * native_price
            except Exception:
                pass
        # 若链上查不到（SOL/查询失败），用保守估算
        if gas_fee_usd == 0.0:
            GAS_ESTIMATE_USD = {"BSC": 0.5, "ETH": 4.0, "SOL": 0.01, "XLAYER": 0.05}
            gas_fee_usd = GAS_ESTIMATE_USD.get(position.chain.upper(), 0.5) * 2
    except Exception:
        gas_fee_usd = 1.0  # fallback

    async with AsyncSessionLocal() as session:
        # 更新持仓状态
        pos = await session.get(Position, position.id)
        buy_tx = pos.buy_tx if pos else ""
        if pos:
            pos.status = "closed"
            pos.current_price = exit_price

        # 记录交易历史
        trade = Trade(
            position_id=position.id,
            ca=position.ca,
            chain=position.chain,
            entry_price=position.entry_price,
            exit_price=exit_price,
            amount_usdt=position.amount_usdt,
            pnl_usdt=pnl_usdt,
            pnl_pct=pnl_pct,
            reason=reason,
            open_time=position.open_time,
            close_time=datetime.utcnow(),
            buy_tx=buy_tx,
            sell_tx=sell_tx,
            gas_fee_usd=gas_fee_usd,
        )
        session.add(trade)
        await session.commit()

    # ── 写卖出快照 + 更新发币人统计 ──────────────────────
    try:
        from services.data_recorder import record_price_snapshot, update_sender_trade_result
        asyncio.create_task(record_price_snapshot(
            position_id=position.id,
            ca=position.ca,
            chain=position.chain,
            price=exit_price,
            pnl_pct=pnl_pct,
            event_type="sell",
        ))
        # 从 ca_feed 找发币人
        async with AsyncSessionLocal() as s2:
            feed_result = await s2.execute(
                select(CaFeed).where(
                    CaFeed.ca == position.ca,
                    CaFeed.bought == True,
                ).order_by(CaFeed.received_at.desc())
            )
            feed = feed_result.scalar_one_or_none()
            if feed and feed.sender:
                asyncio.create_task(
                    update_sender_trade_result(feed.sender, pnl_pct, pnl_usdt)
                )
    except Exception as e:
        logger.error(f"记录卖出快照失败: {e}")

    pnl_sign = "+" if pnl_usdt >= 0 else ""
    # 卖出成功，清零失败计数
    _sell_fail_count.pop(position.id, None)
    _sell_skip_until.pop(position.id, None)

    # 查代币名称用于日志和广播（优先 AVE 真实数据）
    token_name = ""
    symbol = ""
    logo_url = ""
    try:
        from services.ave_data_client import get_token_meta
        meta = await get_token_meta(position.ca, position.chain)
        token_name = meta.get("token_name", "")
        symbol = meta.get("symbol", "")
        logo_url = meta.get("logo_url", "")
    except Exception:
        pass

    display_name = symbol or token_name or position.ca[:12] + "..."

    broadcaster.emit("sell", {
        "ca": position.ca,
        "chain": position.chain,
        "reason": reason,
        "entry_price": position.entry_price,
        "exit_price": exit_price,
        "amount_usdt": position.amount_usdt,
        "pnl_usdt": pnl_usdt,
        "pnl_pct": pnl_pct,
        "gas_fee_usd": round(gas_fee_usd, 4),
        "net_pnl_usdt": round(pnl_usdt - gas_fee_usd, 4),
        "token_name": token_name,
        "symbol": symbol,
        "logo_url": logo_url,
        "hold_minutes": round((datetime.utcnow() - position.open_time).total_seconds() / 60, 1),
    })
    REASON_ZH = {"take_profit": "止盈", "stop_loss": "止损", "time_limit": "超时", "manual": "手动", "zero_balance": "归零", "sell_failed": "放弃"}
    reason_zh = REASON_ZH.get(reason, reason)
    pnl_emoji = "🟢" if pnl_usdt >= 0 else "🔴"
    broadcaster.log(
        f"{pnl_emoji} 卖出{reason_zh} {display_name} [{position.chain}] "
        f"{pnl_sign}{pnl_pct:.1f}% / {pnl_sign}{pnl_usdt:.3f}U  "
        f"入{position.entry_price:.6g}→出{exit_price:.6g}",
        level="info" if pnl_usdt >= 0 else "warn",
    )
    return True


async def monitor_loop():
    """主监控循环"""
    broadcaster.log("持仓监控已启动")
    while True:
        poll_interval = 10  # 默认值，异常时也能正常 sleep
        try:
            async with AsyncSessionLocal() as session:
                cfg = await _get_config(session)
                poll_interval = int(cfg.get("price_poll_interval", "10"))
                take_profit_pct = float(cfg.get("take_profit_pct", "50"))
                stop_loss_pct = float(cfg.get("stop_loss_pct", "30"))
                max_hold_minutes = float(cfg.get("max_hold_minutes", "60"))

                result = await session.execute(
                    select(Position).where(Position.status == "open")
                )
                positions = result.scalars().all()

            if positions:
                # 并发查所有持仓价格，大幅缩短轮询周期
                await asyncio.gather(
                    *[_check_position(pos, take_profit_pct, stop_loss_pct, max_hold_minutes)
                      for pos in positions],
                    return_exceptions=True,
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            broadcaster.log(f"监控循环异常: {e}", level="error")
            logger.exception("Monitor loop error")

        await asyncio.sleep(poll_interval)

    broadcaster.log("持仓监控已停止")


async def _get_chain_token_balance(ca: str, wallet_address: str, chain: str) -> float:
    """直接从链上 RPC 查询 EVM 代币余额（使用正确的 decimals）。
    返回实际代币数量；失败返回 -1。"""
    if chain.upper() not in ("BSC", "ETH", "XLAYER"):
        return -1  # 非 EVM 链跳过
    import httpx as _httpx
    RPC_MAP = {
        "BSC":    "https://bsc-dataseed1.binance.org",
        "ETH":    "https://ethereum-rpc.publicnode.com",
        "XLAYER": "https://rpc.xlayer.tech",
    }
    rpc = RPC_MAP.get(chain.upper(), "https://bsc-dataseed1.binance.org")
    try:
        addr_padded = wallet_address[2:].zfill(64) if wallet_address.startswith("0x") else wallet_address.zfill(64)
        balance_data = "0x70a08231" + addr_padded
        decimals_data = "0x313ce567"
        async with _httpx.AsyncClient(timeout=8.0) as c:
            r_bal = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":ca,"data":balance_data},"latest"],"id":1})
            r_dec = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":ca,"data":decimals_data},"latest"],"id":2})
        bal_hex = r_bal.json().get("result", "0x0") or "0x0"
        dec_hex = r_dec.json().get("result", "0x12") or "0x12"
        balance_raw = int(bal_hex, 16) if bal_hex and bal_hex != "0x" else 0
        decimals = int(dec_hex, 16) if dec_hex and dec_hex != "0x" else 18
        return balance_raw / (10 ** decimals)
    except Exception as e:
        logger.warning(f"链上余额查询失败 {ca[:12]}: {e}")
        return -1


async def _check_position(
    pos: Position,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_hold_minutes: float,
):
    # 卖出失败过多，暂时跳过
    import time
    skip_until = _sell_skip_until.get(pos.id, 0)
    if time.time() < skip_until:
        return

    # 检查时间限制
    hold_minutes = (datetime.utcnow() - pos.open_time).total_seconds() / 60

    # 获取当前价格
    current_price = await ave_client.get_price(pos.ca, pos.chain)

    if pos.entry_price > 0 and current_price > 0:
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
    else:
        pnl_pct = 0.0

    # 判断是否需要卖出
    should_sell = (
        hold_minutes >= max_hold_minutes
        or (current_price > 0 and pnl_pct >= take_profit_pct)
        or (current_price > 0 and pnl_pct <= -stop_loss_pct)
    )

    # ── 链上余额校验（EVM链）：每 30 秒 或 触发卖出时 检查一次 ──────────────
    # 使用内存缓存控制频率，避免对公共节点发送过多 RPC 请求
    _balance_check_last: dict = getattr(_check_position, "_balance_check_ts", {})
    _check_position._balance_check_ts = _balance_check_last
    now = time.time()
    last_check = _balance_check_last.get(pos.id, 0)
    # 新建持仓后至少等 60 秒再查余额，避免买入交易刚上链未确认就被误判归零
    hold_seconds = (datetime.utcnow() - pos.open_time).total_seconds() if pos.open_time else 999
    should_check_balance = should_sell or (now - last_check > 30 and hold_seconds > 60)

    if should_check_balance:
        _balance_check_last[pos.id] = now
        wallet_address = await wallet_manager.get_address_async(pos.chain)
        chain_balance = await _get_chain_token_balance(pos.ca, wallet_address, pos.chain)

        if chain_balance < 0:
            # RPC 查询失败（返回 -1），跳过本次余额校验，不做任何判断
            logger.warning(f"链上余额查询失败，跳过归零检测: pos={pos.id} ca={pos.ca[:12]}")
            chain_balance_valid = False
        else:
            chain_balance_valid = True

        # 归零判断：余额 < 1e-9，或者余额不足买入量的1%（rug pull / 蜜罐）
        # 仅当 RPC 查询成功时才判断
        original_amount = pos.token_amount  # 当前 DB 记录（可能已被更新过）
        is_rugged = chain_balance_valid and (
            chain_balance < 1e-9
            or (original_amount > 0 and chain_balance < original_amount * 0.01)
        )

        if is_rugged:
            rug_reason = "余额归零" if chain_balance < 1e-9 else f"余额仅剩{chain_balance/original_amount*100:.1f}%"
            # 链上代币已归零或余额极小，直接关闭持仓
            broadcaster.log(f"持仓 {pos.ca[:12]}... 链上{rug_reason}，直接关闭", level="warn")
            buy_tx = ""
            async with AsyncSessionLocal() as session:
                p = await session.get(Position, pos.id)
                if p and p.status == "open":
                    buy_tx = p.buy_tx or ""
                    p.status = "closed"
                    p.current_price = current_price or 0.0
                    p.token_amount = 0.0
                    await session.commit()
                else:
                    return
            pnl_usdt = -pos.amount_usdt
            async with AsyncSessionLocal() as session:
                trade = Trade(
                    position_id=pos.id,
                    ca=pos.ca,
                    chain=pos.chain,
                    entry_price=pos.entry_price,
                    exit_price=0.0,
                    amount_usdt=pos.amount_usdt,
                    pnl_usdt=pnl_usdt,
                    pnl_pct=-100.0,
                    reason="zero_balance",
                    open_time=pos.open_time,
                    close_time=datetime.utcnow(),
                    buy_tx=buy_tx,
                    sell_tx="",
                    gas_fee_usd=0.0,
                )
                session.add(trade)
                await session.commit()
            broadcaster.log(f"持仓关闭(链上归零): {pos.ca[:12]}... P&L=-100%", level="warn")
            _sell_fail_count.pop(pos.id, None)
            _sell_skip_until.pop(pos.id, None)
            _balance_check_last.pop(pos.id, None)
            return
        elif chain_balance_valid and chain_balance > 0 and pos.token_amount > 0 and abs(chain_balance - pos.token_amount) / pos.token_amount > 0.1:
            # DB 记录与链上余额差异超过10%，更新 DB
            logger.info(f"修正 token_amount: pos={pos.id} db={pos.token_amount:.6f} chain={chain_balance:.6f}")
            async with AsyncSessionLocal() as session:
                p = await session.get(Position, pos.id)
                if p and p.status == "open":
                    p.token_amount = chain_balance
                    await session.commit()
            pos.token_amount = chain_balance

    # 触发卖出
    if hold_minutes >= max_hold_minutes:
        await _sell_position(pos, "time_limit", current_price)
        return

    if current_price <= 0:
        return

    # 更新当前价格和峰值价格
    async with AsyncSessionLocal() as session:
        p = await session.get(Position, pos.id)
        if p and p.status == "open":
            p.current_price = current_price
            if current_price > p.peak_price:
                p.peak_price = current_price
            await session.commit()

    if pos.entry_price <= 0:
        return

    pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100

    # ── 写价格快照 ────────────────────────────────────────
    try:
        from services.data_recorder import record_price_snapshot
        asyncio.create_task(record_price_snapshot(
            position_id=pos.id,
            ca=pos.ca,
            chain=pos.chain,
            price=current_price,
            pnl_pct=pnl_pct,
            event_type="",
        ))
    except Exception:
        pass

    # 止盈
    if pnl_pct >= take_profit_pct:
        await _sell_position(pos, "take_profit", current_price)
        return

    # 止损
    if pnl_pct <= -stop_loss_pct:
        await _sell_position(pos, "stop_loss", current_price)
        return


def start_monitor():
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(monitor_loop())


def stop_monitor():
    global _monitor_task
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()
        _monitor_task = None
