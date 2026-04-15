"""
AI 对话接口
支持多种 AI provider（OpenAI / Claude / DeepSeek / Gemini / 自定义兼容 OpenAI 的端点）
配置存储在 config 表，对话时自动注入系统上下文（持仓、统计、最近信号）
内置共享 Key（CometAPI 转发），每日次数限额，超限自动降级到用户自己的 Key
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db, ConfigModel
import httpx
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])

# ── 内置共享 Key（CometAPI 中转，兼容 OpenAI 协议）─────────────────
# 从环境变量读取，生产部署时设置 AI_BUILTIN_KEY
BUILTIN_KEY     = os.environ.get("AI_BUILTIN_KEY", "")
BUILTIN_URL     = os.environ.get("AI_BUILTIN_URL", "https://api.cometapi.com/v1")
BUILTIN_MODEL   = os.environ.get("AI_BUILTIN_MODEL", "gpt-4o-mini")
BUILTIN_DEFAULT_LIMIT = 50   # 每日默认限额（可由 DB 配置覆盖）

# 内存计数器（重启清零，精确到天；生产可换 Redis）
_builtin_usage: dict[str, int] = {}   # {"2026-04-13": 12}

def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def _builtin_used_today() -> int:
    return _builtin_usage.get(_today(), 0)

def _builtin_increment():
    today = _today()
    _builtin_usage[today] = _builtin_usage.get(today, 0) + 1
    # 清理旧日期，防止内存泄漏
    for k in list(_builtin_usage.keys()):
        if k != today:
            del _builtin_usage[k]

# ── 支持的 Provider 配置 ──────────────────────────────────────────
PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        "default_model": "gpt-4o-mini",
        "auth_header": "Bearer",
    },
    "anthropic": {
        "name": "Claude (Anthropic)",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5-20251001"],
        "default_model": "claude-sonnet-4-5",
        "auth_header": "x-api-key",
        "api_version": "2023-06-01",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "auth_header": "Bearer",
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
        "default_model": "gemini-2.0-flash",
        "auth_header": "Bearer",
    },
    "cometapi": {
        "name": "CometAPI（推荐，兼容OpenAI）",
        "base_url": "https://api.cometapi.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "claude-sonnet-4-5", "deepseek-chat", "gemini-2.0-flash"],
        "default_model": "gpt-4o-mini",
        "auth_header": "Bearer",
    },
    "custom": {
        "name": "自定义（兼容 OpenAI）",
        "base_url": "",
        "models": [],
        "default_model": "",
        "auth_header": "Bearer",
    },
}

# 配置 key 集合
AI_CFG_KEYS = {
    "ai_provider", "ai_model", "ai_api_key", "ai_base_url",
    "ai_enabled", "ai_max_tokens", "ai_temperature",
    "ai_use_builtin", "ai_builtin_daily_limit",
}


async def _get_ai_cfg(db: AsyncSession) -> dict:
    result = await db.execute(select(ConfigModel).where(ConfigModel.key.in_(AI_CFG_KEYS)))
    return {r.key: r.value for r in result.scalars().all()}


async def _save_cfg(db: AsyncSession, key: str, value: str):
    result = await db.execute(select(ConfigModel).where(ConfigModel.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(ConfigModel(key=key, value=value))
    await db.commit()


# ── 读取 AI 配置 ──────────────────────────────────────────────────
@router.get("/config")
async def get_ai_config(db: AsyncSession = Depends(get_db)):
    cfg = await _get_ai_cfg(db)
    provider = cfg.get("ai_provider", "openai")
    api_key  = cfg.get("ai_api_key", "")
    use_builtin = cfg.get("ai_use_builtin", "true") == "true"
    daily_limit = int(cfg.get("ai_builtin_daily_limit", str(BUILTIN_DEFAULT_LIMIT)))
    used_today  = _builtin_used_today()
    return {
        "provider":    provider,
        "model":       cfg.get("ai_model", PROVIDERS.get(provider, {}).get("default_model", "")),
        "api_key_set": bool(api_key),
        "base_url":    cfg.get("ai_base_url", ""),
        "enabled":     cfg.get("ai_enabled", "false") == "true",
        "max_tokens":  int(cfg.get("ai_max_tokens", "1024")),
        "temperature": float(cfg.get("ai_temperature", "0.7")),
        # 内置 Key 信息
        "use_builtin":        use_builtin,
        "builtin_available":  bool(BUILTIN_KEY),
        "builtin_daily_limit": daily_limit,
        "builtin_used_today": used_today,
        "builtin_remaining":  max(0, daily_limit - used_today),
        "providers":   {k: {"name": v["name"], "models": v["models"], "default_model": v["default_model"]}
                        for k, v in PROVIDERS.items()},
    }


# ── 保存 AI 配置 ──────────────────────────────────────────────────
class AiConfigBody(BaseModel):
    provider:    str | None = None
    model:       str | None = None
    api_key:     str | None = None   # 传空字符串 = 不更新
    base_url:    str | None = None
    enabled:     bool | None = None
    max_tokens:  int | None = None
    temperature: float | None = None
    use_builtin: bool | None = None
    builtin_daily_limit: int | None = None

@router.put("/config")
async def save_ai_config(body: AiConfigBody, db: AsyncSession = Depends(get_db)):
    updates = {}
    if body.provider    is not None: updates["ai_provider"]    = body.provider
    if body.model       is not None: updates["ai_model"]       = body.model
    if body.api_key     is not None and body.api_key != "": updates["ai_api_key"] = body.api_key
    if body.base_url    is not None: updates["ai_base_url"]    = body.base_url
    if body.enabled     is not None: updates["ai_enabled"]     = "true" if body.enabled else "false"
    if body.max_tokens  is not None: updates["ai_max_tokens"]  = str(body.max_tokens)
    if body.temperature is not None: updates["ai_temperature"] = str(body.temperature)
    if body.use_builtin is not None: updates["ai_use_builtin"] = "true" if body.use_builtin else "false"
    if body.builtin_daily_limit is not None: updates["ai_builtin_daily_limit"] = str(body.builtin_daily_limit)
    for k, v in updates.items():
        await _save_cfg(db, k, v)
    return {"ok": True, "updated": list(updates.keys())}


# ── 新增：仅查询内置 Key 用量（轻量接口，无需完整配置）──────────────
@router.get("/builtin_usage")
async def get_builtin_usage(db: AsyncSession = Depends(get_db)):
    cfg = await _get_ai_cfg(db)
    daily_limit = int(cfg.get("ai_builtin_daily_limit", str(BUILTIN_DEFAULT_LIMIT)))
    used = _builtin_used_today()
    return {
        "used_today": used,
        "daily_limit": daily_limit,
        "remaining": max(0, daily_limit - used),
        "available": bool(BUILTIN_KEY),
    }


# ── 构建系统提示（注入当前交易上下文 + 社区信号摘要） ─────────────
async def _build_system_prompt(db: AsyncSession) -> str:
    from database import Position, Trade, CaFeed
    from sqlalchemy import select, desc, func
    import json as _json
    import hashlib

    def _short_hash(s: str) -> str:
        if not s:
            return "????"
        return hashlib.md5(s.encode('utf-8', errors='replace')).hexdigest()[:4].upper()

    lines = [
        "你是 Hodlo.AI × AVE Trader 的智能分析助手，专注于 Meme Coin 自动交易。",
        "你能做的事：分析当前持仓盈亏、解读社区信号热度、评估发币人历史战绩、分析交易策略胜率、识别高风险代币。",
        "以下是系统实时快照，请直接引用这些数据回答问题：\n",
    ]

    try:
        # ── 1. 当前持仓 ──────────────────────────────────────────────
        pos_result = await db.execute(
            select(Position).where(Position.status == "open").order_by(Position.open_time.desc())
        )
        positions = pos_result.scalars().all()
        lines.append(f"【当前持仓】共 {len(positions)} 笔")
        for p in positions[:8]:
            pnl = ((p.current_price - p.entry_price) / p.entry_price * 100) if p.entry_price > 0 and p.current_price > 0 else 0
            lines.append(f"  - {p.ca[:12]}… [{p.chain}] 入场${p.entry_price:.6f} 当前${p.current_price:.6f} 盈亏{pnl:+.1f}%")
        if len(positions) > 8:
            lines.append(f"  … 还有 {len(positions)-8} 笔")

        # ── 2. 近期交易统计 ──────────────────────────────────────────
        trade_result = await db.execute(
            select(Trade).where(Trade.buy_tx != "").order_by(Trade.close_time.desc()).limit(100)
        )
        trades = trade_result.scalars().all()
        if trades:
            total = len(trades)
            wins  = sum(1 for t in trades if t.pnl_usdt > 0)
            total_pnl = sum(t.pnl_usdt for t in trades)
            avg_pnl   = total_pnl / total
            best  = max(t.pnl_pct for t in trades)
            worst = min(t.pnl_pct for t in trades)
            lines.append(f"\n【近{total}笔统计】胜{wins}负{total-wins} 胜率{wins/total*100:.1f}% 净盈亏{total_pnl:+.3f}U 均值{avg_pnl:+.3f}U 最佳{best:+.1f}% 最差{worst:+.1f}%")

        # ── 3. 系统配置 ──────────────────────────────────────────────
        cfg_result = await db.execute(select(ConfigModel))
        cfg = {r.key: r.value for r in cfg_result.scalars().all()}
        bot_status = '运行中 🟢' if cfg.get('bot_enabled') == 'true' else '已停止 🔴'
        lines.append(f"\n【当前配置】Bot:{bot_status} 买入:{cfg.get('buy_amount_usdt','?')}U 止盈:{cfg.get('take_profit_pct','?')}% 止损:{cfg.get('stop_loss_pct','?')}% 最长持仓:{cfg.get('max_hold_minutes','?')}分钟")

        # ── 4. 近期社区信号摘要（最新2小时） ───────────────────────
        from datetime import timedelta
        cutoff_2h = __import__('datetime').datetime.utcnow() - timedelta(hours=2)
        feed_result = await db.execute(
            select(CaFeed)
            .where(CaFeed.received_at >= cutoff_2h)
            .order_by(desc(CaFeed.received_at))
            .limit(200)
        )
        feeds = feed_result.scalars().all()

        if feeds:
            # 按社区分组统计
            group_stats: dict[str, dict] = {}
            total_signals = len(feeds)
            for f in feeds:
                group_raw = ""
                try:
                    raw = _json.loads(f.raw_json or "{}")
                    group_raw = raw.get("qun_name", "") or ""
                except Exception:
                    pass
                gid = _short_hash(group_raw) if group_raw else "未知社区"
                if gid not in group_stats:
                    group_stats[gid] = {"count": 0, "cas": [], "max_qwfc": 0, "bought": 0}
                g = group_stats[gid]
                g["count"] += 1
                g["max_qwfc"] = max(g["max_qwfc"], f.qwfc or 0)
                if f.bought:
                    g["bought"] += 1
                if f.ca and f.ca not in g["cas"]:
                    g["cas"].append(f.ca)

            lines.append(f"\n【近2小时社区信号】共{total_signals}条信号，涉及{len(group_stats)}个社区")
            # 按推送量排序取前5
            top_groups = sorted(group_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:5]
            for gid, stat in top_groups:
                bought_note = f" 已买{stat['bought']}笔" if stat['bought'] > 0 else ""
                lines.append(f"  - 社区#{gid}: {stat['count']}条信号 独立CA {len(stat['cas'])}个 最高全网热度{stat['max_qwfc']}{bought_note}")

            # 最热CA（按qwfc排序）
            hot_feeds = sorted([f for f in feeds if (f.qwfc or 0) > 0], key=lambda x: x.qwfc, reverse=True)[:5]
            if hot_feeds:
                lines.append(f"  热门CA（按全网热度）:")
                for f in hot_feeds:
                    symbol = f.symbol or f.token_name or f.ca[:8] + "…"
                    bought_tag = " ✅已买" if f.bought else ""
                    cap_str = f" 市值${f.market_cap/1000:.0f}K" if f.market_cap and f.market_cap >= 1000 else ""
                    lines.append(f"    {symbol}[{f.chain}] 全网热度{f.qwfc} WS胜率{f.sender_win_rate:.0f}%{cap_str}{bought_tag}")

            # 未买入但高热度的CA（潜在遗漏机会）
            missed = [f for f in feeds if not f.bought and (f.qwfc or 0) >= 10]
            if missed:
                lines.append(f"  高热度未买入CA（{len(missed)}个，可能被过滤器拦截）:")
                for f in missed[:3]:
                    symbol = f.symbol or f.ca[:8] + "…"
                    lines.append(f"    {symbol}[{f.chain}] 热度{f.qwfc} 原因:{f.filter_reason[:40] if f.filter_reason else '未知'}")

        else:
            lines.append("\n【近2小时社区信号】暂无新信号")

    except Exception as e:
        logger.warning(f"build system prompt error: {e}")

    lines.append("\n回答要求：简洁中文，数据直接引用快照，分析要具体，不要套话。")
    return "\n".join(lines)


# ── 对话请求 ──────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str    # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]   # 完整对话历史（前端维护）
    inject_context: bool = True   # 是否注入系统上下文

class ChatResponse(BaseModel):
    reply: str
    provider: str
    model: str
    error: str | None = None


@router.post("/chat", response_model=ChatResponse)
async def ai_chat(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    cfg = await _get_ai_cfg(db)

    if cfg.get("ai_enabled", "false") != "true":
        raise HTTPException(400, "AI 对话未启用，请先在配置页开启 AI 助手")

    max_tokens  = int(cfg.get("ai_max_tokens", "1024"))
    temperature = float(cfg.get("ai_temperature", "0.7"))

    # ── 判断走内置 Key 还是用户自己的 Key ─────────────────────────
    use_builtin  = cfg.get("ai_use_builtin", "true") == "true"
    daily_limit  = int(cfg.get("ai_builtin_daily_limit", str(BUILTIN_DEFAULT_LIMIT)))
    used_today   = _builtin_used_today()
    builtin_ok   = use_builtin and bool(BUILTIN_KEY) and used_today < daily_limit

    if builtin_ok:
        # 走内置共享 Key（CometAPI，兼容 OpenAI 协议）
        msgs = []
        if body.inject_context:
            system_prompt = await _build_system_prompt(db)
            msgs.append({"role": "system", "content": system_prompt})
        msgs += [{"role": m.role, "content": m.content} for m in body.messages]

        _builtin_increment()
        result = await _call_openai_compat(BUILTIN_URL, BUILTIN_KEY, BUILTIN_MODEL, msgs, max_tokens, temperature, "builtin")
        # 在 model 字段标注来源
        result.model = f"{BUILTIN_MODEL} (内置)"
        return result

    # ── 内置不可用 → 走用户自己配置的 Key ─────────────────────────
    provider    = cfg.get("ai_provider", "openai")
    api_key     = cfg.get("ai_api_key", "")
    model       = cfg.get("ai_model", "")

    if not api_key:
        if use_builtin and not BUILTIN_KEY:
            raise HTTPException(400, "内置 Key 未配置（请联系管理员设置 AI_BUILTIN_KEY 环境变量），且未配置自己的 API Key")
        if use_builtin and used_today >= daily_limit:
            raise HTTPException(400, f"内置 Key 今日已用完（{used_today}/{daily_limit} 次），请在配置页填写自己的 API Key 后继续使用")
        raise HTTPException(400, "未配置 API Key，请在配置页 → AI 接口 中填写")
    if not model:
        raise HTTPException(400, "未配置模型名称，请在配置页 → AI 接口 中选择")

    p_cfg    = PROVIDERS.get(provider, PROVIDERS["custom"])
    base_url = cfg.get("ai_base_url", "") or p_cfg.get("base_url", "")
    if not base_url:
        raise HTTPException(400, "未配置 Base URL")

    msgs = []
    if body.inject_context:
        system_prompt = await _build_system_prompt(db)
        msgs.append({"role": "system", "content": system_prompt})
    msgs += [{"role": m.role, "content": m.content} for m in body.messages]

    if provider == "anthropic":
        return await _call_anthropic(api_key, model, msgs, max_tokens, temperature, p_cfg)

    return await _call_openai_compat(base_url, api_key, model, msgs, max_tokens, temperature, provider)


async def _call_openai_compat(base_url, api_key, model, msgs, max_tokens, temperature, provider):
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, headers=headers, json=payload)
            if r.status_code != 200:
                return ChatResponse(reply="", provider=provider, model=model,
                                    error=f"API 错误 {r.status_code}: {r.text[:200]}")
            data = r.json()
            reply = data["choices"][0]["message"]["content"]
            return ChatResponse(reply=reply, provider=provider, model=model)
    except Exception as e:
        return ChatResponse(reply="", provider=provider, model=model, error=str(e))


async def _call_anthropic(api_key, model, msgs, max_tokens, temperature, p_cfg):
    # 把 system 消息单独提取（Anthropic API 不在 messages 里放 system）
    system = ""
    user_msgs = []
    for m in msgs:
        if m["role"] == "system":
            system = m["content"]
        else:
            user_msgs.append(m)

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": p_cfg.get("api_version", "2023-06-01"),
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": user_msgs,
    }
    if system:
        payload["system"] = system

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, headers=headers, json=payload)
            if r.status_code != 200:
                return ChatResponse(reply="", provider="anthropic", model=model,
                                    error=f"API 错误 {r.status_code}: {r.text[:200]}")
            data = r.json()
            reply = data["content"][0]["text"]
            return ChatResponse(reply=reply, provider="anthropic", model=model)
    except Exception as e:
        return ChatResponse(reply="", provider="anthropic", model=model, error=str(e))
