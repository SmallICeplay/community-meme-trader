"""
交易引擎：核心交易逻辑，处理 CA → 买入 → 记录持仓
"""
import asyncio
import logging
import re
from datetime import datetime
from sqlalchemy import select
from database import AsyncSessionLocal, Position, ConfigModel
from services.chain_detector import detect_chain, is_enabled_chain
from services.ave_client import ave_client
from services.wallet_manager import wallet_manager
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)

# 防止重复购买同一CA（内存级别的去重）
_processing_cas: set[str] = set()
_processing_cas_lock: asyncio.Lock = asyncio.Lock()

# 短期失败黑名单：买入失败的 CA 在一段时间内不再重试
# ca -> 解禁时间戳（unix）
import time as _time
_buy_failed_cas: dict[str, float] = {}
BUY_FAIL_COOLDOWN = 300  # 失败后 5 分钟内不再重试同一 CA


def fmtprice(p: float) -> str:
    if not p or p == 0:
        return "—"
    if p < 0.000001:
        return f"{p:.2e}"
    if p < 0.01:
        return f"{p:.6f}"
    return f"{p:.4f}"


async def _get_config(session) -> dict:
    result = await session.execute(select(ConfigModel))
    rows = result.scalars().all()
    return {r.key: r.value for r in rows}


def _enabled(cfg: dict, key: str) -> bool:
    return cfg.get(key, "false").lower() == "true"


def _parse_current_multiple(cxrzf: str) -> float:
    """
    从 cxrzf 字段解析当前涨幅倍数。
    格式示例: "2.64x" / "0x" / "2.39x → 2.64x"（取最新值即最后一个数字）
    """
    if not cxrzf or cxrzf.strip() == "0x":
        return 0.0
    nums = re.findall(r"([\d.]+)x", cxrzf)
    if not nums:
        return 0.0
    return float(nums[-1])


def _check_filters(msg: dict, cfg: dict) -> tuple[bool, str, float]:
    """
    检查所有过滤条件。
    返回: (passed: bool, reason: str, buy_amount_multiplier: float)
    buy_amount_multiplier: 1.0=正常买, 0.5=半仓, 0.0=不买
    """
    sender_total = int(msg.get("sender_total_tokens") or 0)
    sender_win_rate = float(msg.get("sender_win_rate") or 0)
    sender_group_win_rate = float(msg.get("sender_group_win_rate") or 0)
    sender_best_multiple = float(msg.get("sender_best_multiple") or 0)
    is_new_sender = sender_total == 0

    # ── 新人处理 ─────────────────────────────────────────────
    if is_new_sender:
        action = cfg.get("filter_new_sender_action", "skip")
        if action == "skip":
            return False, "新发币人（无历史记录），配置为跳过", 0.0
        elif action == "half":
            # 后续条件中跳过所有发币人相关条件，直接用半仓
            return True, "新发币人，半仓买入", 0.5
        # action == "allow": 正常走后续判断（但发币人条件无样本，跳过）

    # ── 发币人质量 ───────────────────────────────────────────
    if not is_new_sender:
        if _enabled(cfg, "filter_sender_win_rate_enabled"):
            min_rate = float(cfg.get("filter_sender_win_rate_min", "60"))
            if sender_win_rate < min_rate:
                return False, f"全局胜率 {sender_win_rate}% < {min_rate}%", 0.0

        if _enabled(cfg, "filter_sender_group_win_rate_enabled"):
            min_rate = float(cfg.get("filter_sender_group_win_rate_min", "60"))
            if sender_group_win_rate < min_rate:
                return False, f"群胜率 {sender_group_win_rate}% < {min_rate}%", 0.0

        if _enabled(cfg, "filter_sender_total_tokens_enabled"):
            min_total = int(cfg.get("filter_sender_total_tokens_min", "5"))
            if sender_total < min_total:
                return False, f"历史发币数 {sender_total} < {min_total}", 0.0

        if _enabled(cfg, "filter_sender_best_multiple_enabled"):
            min_mult = float(cfg.get("filter_sender_best_multiple_min", "10"))
            if sender_best_multiple < min_mult:
                return False, f"历史最高倍数 {sender_best_multiple}x < {min_mult}x", 0.0

    # ── 防追高：当前已涨倍数 ─────────────────────────────────
    if _enabled(cfg, "filter_current_multiple_enabled"):
        max_mult = float(cfg.get("filter_current_multiple_max", "3"))
        current_mult = _parse_current_multiple(str(msg.get("cxrzf", "0x")))
        if current_mult > max_mult:
            return False, f"已涨 {current_mult}x 超过上限 {max_mult}x，防追高跳过", 0.0

    # ── 传播热度 ─────────────────────────────────────────────
    if _enabled(cfg, "filter_qwfc_enabled"):
        min_val = int(cfg.get("filter_qwfc_min", "3"))
        val = int(msg.get("qwfc") or 0)
        if val < min_val:
            return False, f"全网发送次数 {val} < {min_val}", 0.0

    if _enabled(cfg, "filter_bqfc_enabled"):
        min_val = int(cfg.get("filter_bqfc_min", "2"))
        val = int(msg.get("bqfc") or 0)
        if val < min_val:
            return False, f"本群发送次数 {val} < {min_val}", 0.0

    if _enabled(cfg, "filter_fgq_enabled"):
        min_val = int(cfg.get("filter_fgq_min", "2"))
        val = int(msg.get("fgq") or 0)
        if val < min_val:
            return False, f"覆盖群数量 {val} < {min_val}", 0.0

    if _enabled(cfg, "filter_grcxcs_enabled"):
        min_val = int(cfg.get("filter_grcxcs_min", "1"))
        val = int(msg.get("grcxcs") or 0)
        if val < min_val:
            return False, f"个人查询次数 {val} < {min_val}", 0.0

    # ── 市场数据 ─────────────────────────────────────────────
    if _enabled(cfg, "filter_market_cap_enabled"):
        mc = float(msg.get("market_cap") or 0)
        mc_min = float(cfg.get("filter_market_cap_min", "10000"))
        mc_max = float(cfg.get("filter_market_cap_max", "5000000"))
        if mc < mc_min:
            return False, f"市值 ${mc:.0f} < 下限 ${mc_min:.0f}", 0.0
        if mc_max > 0 and mc > mc_max:
            return False, f"市值 ${mc:.0f} > 上限 ${mc_max:.0f}", 0.0

    if _enabled(cfg, "filter_price_change_5m_enabled"):
        min_pct = float(cfg.get("filter_price_change_5m_min", "0"))
        val = float(msg.get("price_change_5m") or 0)
        if val < min_pct:
            return False, f"5分钟涨幅 {val}% < {min_pct}%", 0.0

    if _enabled(cfg, "filter_buy_volume_1h_enabled"):
        min_vol = float(cfg.get("filter_buy_volume_1h_min", "1000"))
        val = float(msg.get("buy_volume_u_1h") or 0)
        if val < min_vol:
            return False, f"1小时买入量 ${val:.0f} < ${min_vol:.0f}", 0.0

    if _enabled(cfg, "filter_holders_enabled"):
        min_holders = int(cfg.get("filter_holders_min", "50"))
        val = int(msg.get("holders") or 0)
        if val < min_holders:
            return False, f"持有人数 {val} < {min_holders}", 0.0

    # ── 安全过滤 ─────────────────────────────────────────────
    honeypot_val = str(msg.get("is_honeypot", "-1"))
    if _enabled(cfg, "filter_honeypot_enabled"):
        # 排除确认蜜罐
        if honeypot_val == "1":
            return False, "蜜罐检测: is_honeypot=1（确认为蜜罐）", 0.0
        # 排除未检测代币（独立子开关，仅在主开关开启时生效）
        if honeypot_val in ("-1", ""):
            action = cfg.get("filter_honeypot_unknown_action", "skip")
            if action == "skip":
                return False, "蜜罐检测未知（is_honeypot=-1），配置为跳过未检测代币", 0.0

    if _enabled(cfg, "filter_mintable_enabled"):
        val = str(msg.get("is_mintable", "0"))
        if val == "1":
            return False, "代币可增发，跳过", 0.0

    if _enabled(cfg, "filter_risk_score_enabled"):
        max_score = float(cfg.get("filter_risk_score_max", "70"))
        val = float(msg.get("risk_score") or 0)
        if val > max_score:
            return False, f"风险评分 {val} > {max_score}", 0.0

    if _enabled(cfg, "filter_max_holder_pct_enabled"):
        max_pct = float(cfg.get("filter_max_holder_pct_max", "90"))
        val = float(msg.get("zzb") or 0)
        if val > max_pct:
            return False, f"最大持仓比 {val}% > {max_pct}%（集中度过高）", 0.0

    return True, "通过所有过滤条件", 1.0


async def handle_new_ca(raw_message: dict):
    """
    接收到新 CA 推送后的处理入口
    raw_message: WS 消息原始 dict
    """
    # 提取 CA（统一小写，避免大小写不同导致冷却/去重失效）
    ca = (
        raw_message.get("ca")
        or raw_message.get("address")
        or raw_message.get("token")
        or raw_message.get("contract")
        or ""
    ).strip().lower()

    if not ca:
        broadcaster.log(f"收到无效消息（无CA字段）: {raw_message}", level="warn")
        return

    # 检测链
    chain = detect_chain(raw_message)
    if not chain:
        broadcaster.log(f"无法识别链，跳过: ca={ca}", level="warn")
        return

    # ── 最早拦截（同步，在任何 await 之前） ────────────────
    # 1. 正在处理中的 CA（并发去重，Lock 保证原子性）
    async with _processing_cas_lock:
        if ca in _processing_cas:
            return
        # 2. 买入失败冷却中
        cooldown_until = _buy_failed_cas.get(ca, 0)
        if cooldown_until > _time.time():
            remaining = int(cooldown_until - _time.time())
            broadcaster.log(f"⏳ {ca[:12]}... 冷却中 {remaining}s", level="warn")
            return
        # 通过后立即占位，防止后续并发协程绕过
        _processing_cas.add(ca)

    try:
        await _handle_ca_inner(ca, chain, raw_message)
    finally:
        _processing_cas.discard(ca)


async def _handle_ca_inner(ca: str, chain: str, raw_message: dict):
    """内部处理：已通过 _processing_cas 去重"""
    broadcaster.log(f"📥 收到 CA: {ca[:12]}... [{chain}]")

    async with AsyncSessionLocal() as session:
        cfg = await _get_config(session)

        # 检查 bot 是否启用
        if cfg.get("bot_enabled", "false").lower() != "true":
            broadcaster.log(f"⏸ Bot 已暂停，跳过", level="warn")
            return

        # 检查链是否启用
        enabled_chains = cfg.get("enabled_chains", "SOL,BSC,ETH,XLAYER").split(",")
        if not is_enabled_chain(chain, enabled_chains):
            broadcaster.log(f"链 {chain} 未启用，跳过", level="warn")
            return

        # 检查并发持仓数量
        max_positions = int(cfg.get("max_concurrent_positions", "5"))
        open_count_result = await session.execute(
            select(Position).where(Position.status == "open")
        )
        open_positions = open_count_result.scalars().all()
        if len(open_positions) >= max_positions:
            broadcaster.log(f"⚡ 持仓已满 {len(open_positions)}/{max_positions}，跳过", level="warn")
            return

        # 检查是否已持有或曾持有无法卖出的该CA
        existing = await session.execute(
            select(Position).where(
                Position.ca == ca,
                Position.status.in_(["open", "sell_failed"]),
            )
        )
        if existing.scalar_one_or_none():
            broadcaster.log(f"已持有或卖出失败 {ca}，跳过重复购买")
            return

        # 检查是否曾经买过（closed），若是则看热度是否暴涨到阈值才重买
        repeat_buy_enabled = cfg.get("ca_repeat_buy_enabled", "false").lower() == "true"
        if not repeat_buy_enabled:
            prev_closed = await session.execute(
                select(Position).where(Position.ca == ca, Position.status == "closed")
            )
            if prev_closed.scalar_one_or_none():
                broadcaster.log(f"⏭ {ca[:12]}... 曾经买过（已平仓），重复买入未启用，跳过")
                return
        else:
            # 重复买入开关打开：检查热度增量是否达到阈值
            prev_closed = await session.execute(
                select(Position).where(Position.ca == ca, Position.status == "closed")
            )
            if prev_closed.scalar_one_or_none():
                threshold = int(cfg.get("ca_repeat_qwfc_delta", "20"))
                qwfc_delta = int(raw_message.get("_qwfc_delta", 0))
                if qwfc_delta < threshold:
                    broadcaster.log(
                        f"⏭ {ca[:12]}... 曾买过，热度增量 +{qwfc_delta} < 阈值 {threshold}，跳过",
                    )
                    return
                broadcaster.log(
                    f"🔥 {ca[:12]}... 曾买过但热度暴涨 +{qwfc_delta}（阈值 {threshold}），重新买入",
                    level="warn",
                )

        # ── 过滤条件检查 ─────────────────────────────────────
        passed, reason, amount_multiplier = _check_filters(raw_message, cfg)

        # ── 写入 CA 流水（无论是否通过过滤，都记录） ─────────
        sender = str(raw_message.get("cxr") or "")
        asyncio.create_task(_record_feed_and_sender(
            raw_message, passed, reason, sender, bought=False
        ))

        if not passed:
            broadcaster.log(f"🚫 过滤拦截 [{chain}] {ca[:8]}…: {reason}", level="warn")
            return
        if amount_multiplier != 1.0:
            broadcaster.log(f"过滤通过（{reason}）[{chain}] {ca[:8]}…")

        # ── 支出限额检查 ──────────────────────────────────────
        if _enabled(cfg, "spend_limit_enabled"):
            limit_usdt = float(cfg.get("spend_limit_usdt", "50"))
            limit_hours = float(cfg.get("spend_limit_hours", "24"))
            spent = await _get_spent_in_window(session, limit_hours)
            base_amount = float(cfg.get("buy_amount_usdt", "2"))
            this_buy = round(base_amount * amount_multiplier, 4)
            if spent + this_buy > limit_usdt:
                broadcaster.log(
                    f"支出限额已达上限：{limit_hours}小时内已花 {spent:.2f}U / {limit_usdt}U，跳过",
                    level="warn",
                )
                return

    await _execute_buy(ca, chain, raw_message, amount_multiplier)


async def _record_feed_and_sender(
    msg: dict,
    filter_passed: bool,
    reason: str,
    sender: str,
    bought: bool = False,
    position_id: int = 0,
):
    """异步写入 ca_feed + 更新 sender_stats，异常不影响主流程"""
    try:
        from services.data_recorder import record_ca_feed, update_sender_stats
        await record_ca_feed(msg, filter_passed, reason, bought=bought, position_id=position_id)
        await update_sender_stats(sender, msg, bought=bought)
    except Exception as e:
        logger.error(f"_record_feed_and_sender error: {e}")


async def _get_spent_in_window(session, hours: float) -> float:
    """统计时间窗口内的实际买入支出（来自 positions 表）"""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    result = await session.execute(
        select(Position).where(Position.open_time >= cutoff)
    )
    positions = result.scalars().all()
    return sum(p.amount_usdt for p in positions)


def _classify_buy_error(err: str, amount_usdt: float = 0.0) -> str:
    """将买入异常消息映射为中文原因描述"""
    if "预检失败" in err or "无法询价" in err:
        return "代币无效或无流动性（预检失败，已跳过，不会扣 gas）"
    if "3025" in err:
        hint = f"（当前买入 {amount_usdt}U，若金额过小可尝试调大至 1U 以上）" if amount_usdt > 0 and amount_usdt < 1.0 else "（代币可能有交易限制或池子问题）"
        return f"合约模拟失败{hint}"
    if "3024" in err:
        return "无效代币或合约地址"
    if "3026" in err:
        return "余额不足"
    if "3027" in err or "slippage" in err.lower():
        return "滑点超限，价格波动过大"
    if "nonce" in err.lower():
        return "Nonce 错误（网络拥堵），稍后会自动重试"
    if "timeout" in err.lower() or "timed out" in err.lower():
        return "RPC 超时，网络问题"
    if "insufficient" in err.lower():
        return "余额不足"
    if "approve" in err.lower():
        return "授权失败"
    if "wallet" in err.lower() or "address" in err.lower():
        return "钱包地址错误"
    return "交易失败（未知原因）"


def _classify_sell_error(err: str) -> str:
    """将卖出异常消息映射为中文原因描述"""
    if "3025" in err:
        return "合约模拟失败（代币可能锁定/貔貅/无法卖出）"
    if "3024" in err:
        return "无效代币或合约地址"
    if "3026" in err:
        return "余额不足"
    if "3027" in err or "slippage" in err.lower():
        return "滑点超限，价格波动过大"
    if "nonce" in err.lower():
        return "Nonce 错误"
    if "timeout" in err.lower() or "timed out" in err.lower():
        return "RPC 超时，网络问题"
    if "insufficient" in err.lower():
        return "余额不足"
    if "zero" in err.lower() or "balance" in err.lower():
        return "代币余额为零，可能已归零"
    return "卖出失败（未知原因）"


async def _execute_buy(ca: str, chain: str, raw_message: dict, amount_multiplier: float = 1.0):
    async with AsyncSessionLocal() as session:
        cfg = await _get_config(session)
        base_amount = float(cfg.get("buy_amount_usdt", "2"))
        amount_usdt = round(base_amount * amount_multiplier, 4)
        fallback_enabled = cfg.get("buy_amount_fallback_enabled", "true").lower() == "true"
        fallback_usdt = float(cfg.get("buy_amount_fallback_usdt", "1"))

    try:
        wallet_address = await wallet_manager.get_address_async(chain)
    except Exception as e:
        broadcaster.log(f"获取钱包地址失败 [{chain}]: {e}", level="error")
        return

    token_name = (str(raw_message.get("symbol") or raw_message.get("name") or "")).replace("*", "").strip()

    # 构建尝试列表：原始金额，若启用 fallback 且原始金额小于 fallback 则追加 fallback
    attempt_amounts = [amount_usdt]
    if fallback_enabled and amount_usdt < fallback_usdt:
        attempt_amounts.append(fallback_usdt)

    last_err = ""
    last_reason = ""
    for i, try_amount in enumerate(attempt_amounts):
        is_fallback = i > 0
        label = f"{try_amount}U{' ↑升级' if is_fallback else ''}"
        broadcaster.log(f"💸 执行买入: {token_name or ca[:12]}... [{chain}] {label}")

        try:
            result = await ave_client.buy(
                ca=ca, chain=chain,
                amount_usdt=try_amount,
                wallet_address=wallet_address,
            )
        except Exception as e:
            last_err = str(e)
            last_reason = _classify_buy_error(last_err, try_amount)
            # 3025 = AVE 模拟失败（代币本身问题），直接冷却不再重试其他金额
            if "3025" in last_err:
                break
            continue

        if not result.get("success"):
            last_err = str(result.get("msg") or result.get("message") or result)
            last_reason = _classify_buy_error(last_err, try_amount)
            if "3025" in last_err:
                break
            continue

        # ── 买入成功，后续流程 ────────────────────────────────
        amount_usdt = try_amount  # 记录实际成交金额
        entry_price = float(result.get("price", 0))
        token_amount = float(result.get("token_amount", 0))
        buy_tx = result.get("tx", "")

        async with AsyncSessionLocal() as session:
            position = Position(
                ca=ca,
                chain=chain,
                entry_price=entry_price,
                amount_usdt=amount_usdt,
                token_amount=token_amount,
                buy_tx=buy_tx,
                open_time=datetime.utcnow(),
                peak_price=entry_price,
                current_price=entry_price,
                status="open",
            )
            session.add(position)
            await session.commit()
            await session.refresh(position)
            pos_id = position.id

        # ── 写买入快照 + 更新 ca_feed.bought ─────────────────
        sender = str(raw_message.get("cxr") or "")
        asyncio.create_task(_record_feed_and_sender(
            raw_message, True, "买入成功", sender, bought=True, position_id=pos_id
        ))
        try:
            from services.data_recorder import record_price_snapshot
            asyncio.create_task(record_price_snapshot(
                position_id=pos_id, ca=ca, chain=chain,
                price=entry_price, pnl_pct=0.0, event_type="buy",
            ))
        except Exception:
            pass

        # ── 触发 AVE 数据拉取 ─────────────────────────────────
        try:
            from services.ave_data_client import fetch_and_cache_token
            asyncio.create_task(fetch_and_cache_token(ca, chain))
        except Exception:
            pass

        # 优先用 AVE 缓存名称，否则从 WS 消息取
        try:
            from services.ave_data_client import get_token_meta
            meta = await get_token_meta(ca, chain)
        except Exception:
            meta = {"token_name": "", "symbol": "", "logo_url": ""}

        if not meta.get("token_name") and not meta.get("symbol"):
            meta = {
                "token_name": (str(raw_message.get("name") or raw_message.get("token_name") or "")).replace("*", "").strip(),
                "symbol": (str(raw_message.get("symbol") or "")).replace("*", "").strip(),
                "logo_url": str(raw_message.get("logo_url") or ""),
            }

        broadcaster.emit("buy", {
            "ca": ca, "chain": chain, "amount_usdt": amount_usdt,
            "entry_price": entry_price, "token_amount": token_amount,
            "tx": buy_tx, "position_id": pos_id,
            "token_name": meta.get("token_name", ""),
            "symbol": meta.get("symbol", ""),
            "logo_url": meta.get("logo_url", ""),
        })
        display = meta.get("symbol") or meta.get("token_name") or ca[:12] + "..."
        broadcaster.log(f"✅ 买入成功! {display} [{chain}] 入场 {fmtprice(entry_price)} · 数量 {token_amount:.2f}")

        if buy_tx:
            asyncio.create_task(_emit_buy_gas(buy_tx, chain, pos_id))
        return  # 成功，退出

    # ── 所有金额都失败 ────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        _cfg = await _get_config(session)
    try:
        cooldown_seconds = int(_cfg.get("buy_fail_cooldown_seconds", "300"))
    except Exception:
        cooldown_seconds = 300
    _buy_failed_cas[ca] = _time.time() + cooldown_seconds
    tried = " → ".join(f"{a}U" for a in attempt_amounts)
    broadcaster.emit("buy_failed", {
        "ca": ca, "chain": chain, "token_name": token_name,
        "error": last_err[:200],
        "reason": last_reason,
        "amount_usdt": attempt_amounts[-1],
        "tried_amounts": tried if len(attempt_amounts) > 1 else None,
    }, level="error")


async def _emit_buy_gas(buy_tx: str, chain: str, position_id: int):
    """异步查买入交易的实际 gas 费，完成后广播 buy_gas 事件"""
    import httpx as _httpx
    RPC_MAP = {"BSC": "https://bsc-dataseed1.binance.org", "ETH": "https://ethereum-rpc.publicnode.com"}
    rpc = RPC_MAP.get(chain.upper(), "https://bsc-dataseed1.binance.org")
    try:
        from services.ave_client import _get_native_price_usd_async
        CHAIN_NAME = {"BSC": "bsc", "ETH": "eth", "SOL": "solana"}
        native_price = await _get_native_price_usd_async(CHAIN_NAME.get(chain.upper(), "bsc"))
        # 等待 receipt（最多重试 5 次，间隔 3s）
        gas_fee_usd = 0.0
        for _ in range(5):
            await asyncio.sleep(3)
            try:
                async with _httpx.AsyncClient(timeout=8.0) as c:
                    r = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[buy_tx],"id":1})
                    receipt = r.json().get("result")
                    if receipt:
                        gas_used = int(receipt.get("gasUsed","0x0"), 16)
                        gp = int(receipt.get("effectiveGasPrice","0x0") or "0x0", 16)
                        gas_fee_usd = gas_used * gp / 1e18 * native_price
                        break
            except Exception:
                pass
        if gas_fee_usd > 0:
            broadcaster.emit("buy_gas", {"position_id": position_id, "gas_fee_usd": round(gas_fee_usd, 4)})
            # 同时写回 Position 表，持仓页可直接显示
            try:
                async with AsyncSessionLocal() as session:
                    pos = await session.get(Position, position_id)
                    if pos:
                        pos.gas_fee_usd = round(gas_fee_usd, 4)
                        await session.commit()
            except Exception as e:
                logger.debug(f"_emit_buy_gas db write error: {e}")
    except Exception as e:
        logger.debug(f"_emit_buy_gas error: {e}")
