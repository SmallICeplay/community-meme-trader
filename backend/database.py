from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, Integer, DateTime, Text, Boolean
from datetime import datetime
from config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class ConfigModel(Base):
    __tablename__ = "config"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ca: Mapped[str] = mapped_column(String(128), index=True)
    chain: Mapped[str] = mapped_column(String(16))
    entry_price: Mapped[float] = mapped_column(Float)
    amount_usdt: Mapped[float] = mapped_column(Float)
    token_amount: Mapped[float] = mapped_column(Float, default=0.0)
    buy_tx: Mapped[str] = mapped_column(String(256), default="")
    open_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    peak_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open")
    gas_fee_usd: Mapped[float] = mapped_column(Float, default=0.0)  # 买入 gas 成本（USD）


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(Integer)
    ca: Mapped[str] = mapped_column(String(128))
    chain: Mapped[str] = mapped_column(String(16))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    amount_usdt: Mapped[float] = mapped_column(Float)
    pnl_usdt: Mapped[float] = mapped_column(Float)
    pnl_pct: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(32))
    open_time: Mapped[datetime] = mapped_column(DateTime)
    close_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    buy_tx: Mapped[str] = mapped_column(String(256), default="")
    sell_tx: Mapped[str] = mapped_column(String(256), default="")
    gas_fee_usd: Mapped[float] = mapped_column(Float, default=0.0)  # 估算 gas 成本（USD）


# ── 数据沉淀表（长期保留，构建智能体数据集） ─────────────────

class CaFeed(Base):
    """
    WS 推送流水 —— 每条 CA 推送完整保存，永久沉淀
    核心原始数据集：用于分析发币人规律、市场热度模式、过滤参数优化
    未来可作为机器学习训练数据（特征: WS字段 → 标签: 是否盈利）
    """
    __tablename__ = "ca_feed"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    ca: Mapped[str] = mapped_column(String(128), index=True)
    chain: Mapped[str] = mapped_column(String(16), index=True)
    token_name: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(32), default="")

    # 发币人（WS 数据）
    sender: Mapped[str] = mapped_column(String(128), default="", index=True)
    sender_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    sender_group_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    sender_total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    sender_win_tokens: Mapped[int] = mapped_column(Integer, default=0)
    sender_best_multiple: Mapped[float] = mapped_column(Float, default=0.0)
    current_multiple: Mapped[float] = mapped_column(Float, default=0.0)  # cxrzf 解析

    # 传播热度
    bqfc: Mapped[int] = mapped_column(Integer, default=0)
    qwfc: Mapped[int] = mapped_column(Integer, default=0)
    fgq: Mapped[int] = mapped_column(Integer, default=0)
    grcxcs: Mapped[int] = mapped_column(Integer, default=0)

    # 市场快照
    price_usd: Mapped[float] = mapped_column(Float, default=0.0)
    market_cap: Mapped[float] = mapped_column(Float, default=0.0)
    tvl: Mapped[float] = mapped_column(Float, default=0.0)
    holders: Mapped[int] = mapped_column(Integer, default=0)
    price_change_5m: Mapped[float] = mapped_column(Float, default=0.0)
    price_change_1h: Mapped[float] = mapped_column(Float, default=0.0)
    price_change_4h: Mapped[float] = mapped_column(Float, default=0.0)
    price_change_24h: Mapped[float] = mapped_column(Float, default=0.0)
    buy_volume_1h: Mapped[float] = mapped_column(Float, default=0.0)
    sell_volume_1h: Mapped[float] = mapped_column(Float, default=0.0)
    buy_volume_24h: Mapped[float] = mapped_column(Float, default=0.0)
    sell_volume_24h: Mapped[float] = mapped_column(Float, default=0.0)
    buys_tx_1h: Mapped[int] = mapped_column(Integer, default=0)
    sells_tx_1h: Mapped[int] = mapped_column(Integer, default=0)
    buys_tx_24h: Mapped[int] = mapped_column(Integer, default=0)
    sells_tx_24h: Mapped[int] = mapped_column(Integer, default=0)

    # 安全指标
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    is_honeypot: Mapped[str] = mapped_column(String(8), default="-1")
    is_mintable: Mapped[str] = mapped_column(String(4), default="0")
    max_holder_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # 过滤结果（用于分析哪个条件拦截最多）
    filter_passed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    filter_reason: Mapped[str] = mapped_column(String(256), default="")
    bought: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    position_id: Mapped[int] = mapped_column(Integer, default=0)

    # 完整原始 JSON（用于未来数据挖掘，字段可能随WS更新增加）
    raw_json: Mapped[str] = mapped_column(Text, default="")


class PriceSnapshot(Base):
    """
    持仓价格快照 —— 每 10 秒写入一次，画价格曲线 + 标记买卖点
    """
    __tablename__ = "price_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(Integer, index=True)
    ca: Mapped[str] = mapped_column(String(128))
    chain: Mapped[str] = mapped_column(String(16))
    price: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    event_type: Mapped[str] = mapped_column(String(8), default="")  # "" | "buy" | "sell"
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)      # 实时盈亏%


class SenderStats(Base):
    """
    发币人本地聚合统计 —— 基于本系统实际交易结果
    与 WS 提供的胜率独立，是系统自己积累的发币人信用评分
    """
    __tablename__ = "sender_stats"
    sender: Mapped[str] = mapped_column(String(128), primary_key=True)
    total_pushed: Mapped[int] = mapped_column(Integer, default=0)
    total_bought: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl_usdt: Mapped[float] = mapped_column(Float, default=0.0)
    best_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    worst_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    ws_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    ws_total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ws_best_multiple: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TokenDetail(Base):
    """
    AVE API 拉取的链上详情缓存 —— 24小时内不重复拉取
    同时作为代币链上数据的永久存档
    """
    __tablename__ = "token_detail"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ca: Mapped[str] = mapped_column(String(128), index=True)
    chain: Mapped[str] = mapped_column(String(16))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    base_info_json: Mapped[str] = mapped_column(Text, default="")
    kline_1h_json: Mapped[str] = mapped_column(Text, default="")
    kline_5m_json: Mapped[str] = mapped_column(Text, default="")
    holders_json: Mapped[str] = mapped_column(Text, default="")
    token_name: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(32), default="")
    total_supply: Mapped[str] = mapped_column(String(64), default="")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        defaults = {
            "bot_enabled": "false",
            "buy_amount_usdt": "2",
            "take_profit_pct": "50",
            "stop_loss_pct": "30",
            "max_hold_minutes": "60",
            "max_concurrent_positions": "5",
            "enabled_chains": "SOL,BSC,ETH,XLAYER",
            "price_poll_interval": "10",
            "spend_limit_enabled": "false",
            "spend_limit_usdt": "50",
            "spend_limit_hours": "24",
            "filter_sender_win_rate_enabled": "false",
            "filter_sender_win_rate_min": "60",
            "filter_sender_group_win_rate_enabled": "false",
            "filter_sender_group_win_rate_min": "60",
            "filter_sender_total_tokens_enabled": "false",
            "filter_sender_total_tokens_min": "5",
            "filter_sender_best_multiple_enabled": "false",
            "filter_sender_best_multiple_min": "10",
            "filter_new_sender_action": "skip",
            "filter_current_multiple_enabled": "false",
            "filter_current_multiple_max": "3",
            "filter_qwfc_enabled": "false",
            "filter_qwfc_min": "3",
            "filter_bqfc_enabled": "false",
            "filter_bqfc_min": "2",
            "filter_fgq_enabled": "false",
            "filter_fgq_min": "2",
            "filter_grcxcs_enabled": "false",
            "filter_grcxcs_min": "1",
            "filter_market_cap_enabled": "false",
            "filter_market_cap_min": "10000",
            "filter_market_cap_max": "5000000",
            "filter_price_change_5m_enabled": "false",
            "filter_price_change_5m_min": "0",
            "filter_buy_volume_1h_enabled": "false",
            "filter_buy_volume_1h_min": "1000",
            "filter_holders_enabled": "false",
            "filter_holders_min": "50",
            "filter_honeypot_enabled": "true",
            "filter_mintable_enabled": "true",
            "filter_risk_score_enabled": "false",
            "filter_risk_score_max": "70",
            "filter_max_holder_pct_enabled": "false",
            "filter_max_holder_pct_max": "90",
            "position_price_source": "cached",
            "ave_trade_api_key": "",
            "ave_trade_api_url": "https://bot-api.ave.ai",
            "ave_data_api_key": "SW59NmZFRG2yfRSSWvKlzTuAuZBFl5SUUCV2DUX5rg5eK8n6sipMlLkwXCX5qHGw",
            "ave_data_api_url": "https://ave-api.cloud",
            "gas_price_multiplier": "1.0",
            "approve_gas_price_gwei": "1.0",
            "buy_amount_fallback_enabled": "true",
            "buy_amount_fallback_usdt": "1",
            "filter_honeypot_unknown_action": "skip",
            "ca_repeat_buy_enabled": "false",
            "ca_repeat_qwfc_delta": "20",
            "buy_precheck_enabled": "true",
            "buy_fail_cooldown_seconds": "300",
        }
        for key, value in defaults.items():
            result = await session.execute(select(ConfigModel).where(ConfigModel.key == key))
            if not result.scalar_one_or_none():
                session.add(ConfigModel(key=key, value=value))
        await session.commit()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
