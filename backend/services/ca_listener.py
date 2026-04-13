"""
CA 监听器：连接推送源 WS，接收新 CA 并传给交易引擎
"""
import asyncio
import json
import logging
import websockets
from config import get_settings
from services.trade_engine import handle_new_ca
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)
settings = get_settings()

_listener_task: asyncio.Task | None = None


async def listen_loop():
    url = settings.ca_ws_url
    broadcaster.log(f"正在连接 CA 推送源: {url}")
    reconnect_delay = 3

    while True:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                broadcaster.log(f"已连接到 CA 推送源: {url}")
                reconnect_delay = 3  # 重置重连延迟
                async for raw in ws:
                    await _process_message(raw)
        except asyncio.CancelledError:
            broadcaster.log("CA 监听器已停止")
            break
        except websockets.exceptions.ConnectionClosed as e:
            broadcaster.log(f"CA 推送源连接断开: {e}，{reconnect_delay}秒后重连...", level="warn")
        except Exception as e:
            broadcaster.log(f"CA 监听器异常: {e}，{reconnect_delay}秒后重连...", level="error")
            logger.exception("CA listener error")

        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, 60)  # 指数退避，最大60秒


async def _process_message(raw: str):
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        message = {"ca": raw.strip()}

    # 提取关键字段用于前端展示
    ca = (
        message.get("ca") or message.get("address") or
        message.get("token") or message.get("contract") or ""
    ).strip() if isinstance(message, dict) else ""
    chain = message.get("chain", "") if isinstance(message, dict) else ""
    raw_symbol = (message.get("symbol") or message.get("name") or "").replace("*", "").strip()
    raw_token_name = (message.get("token_name") or message.get("tokenName") or message.get("fullName") or "").replace("*", "").strip()
    display_name = raw_symbol or raw_token_name

    # 查询该 CA 历史推送次数，并立即写入本条流水（保证下次查时计数正确）
    push_count = 1
    prev_qwfc = 0
    cur_qwfc = int(message.get("qwfc") or 0) if isinstance(message, dict) else 0
    feed_id = None
    if ca:
        try:
            from database import AsyncSessionLocal, CaFeed
            from sqlalchemy import select, func
            async with AsyncSessionLocal() as session:
                cnt = await session.execute(
                    select(func.count()).select_from(CaFeed).where(CaFeed.ca == ca)
                )
                existing_count = cnt.scalar() or 0
                push_count = existing_count + 1
                # 取上一次的 qwfc
                last = await session.execute(
                    select(CaFeed.qwfc).where(CaFeed.ca == ca).order_by(CaFeed.received_at.desc()).limit(1)
                )
                prev_row = last.scalar_one_or_none()
                if prev_row is not None:
                    prev_qwfc = prev_row
                # 立即写入本条（轻量字段，trade_engine 后续会更新 filter_passed/bought）
                feed = CaFeed(
                    ca=ca,
                    chain=chain,
                    symbol=raw_symbol,
                    token_name=raw_token_name,
                    qwfc=cur_qwfc,
                    raw_json=raw[:65000] if isinstance(raw, str) else "",
                )
                session.add(feed)
                await session.commit()
                await session.refresh(feed)
                feed_id = feed.id
        except Exception:
            pass

    qwfc_delta = max(0, cur_qwfc - prev_qwfc)

    broadcaster.emit("ca_received", {
        "ca": ca,
        "chain": chain,
        "symbol": display_name,
        "push_count": push_count,
        "qwfc": cur_qwfc,
        "qwfc_delta": qwfc_delta,
    })

    # 把 qwfc_delta、push_count、feed_id 注入消息，供 trade_engine 使用
    if isinstance(message, dict):
        message["_qwfc_delta"] = qwfc_delta
        message["_push_count"] = push_count
        message["_feed_id"] = feed_id  # trade_engine 用来更新已有记录，避免重复写入

    # 如果消息是列表（批量推送），逐个处理
    if isinstance(message, list):
        for item in message:
            await handle_new_ca(item)
    else:
        await handle_new_ca(message)


def start_listener():
    global _listener_task
    if _listener_task is None or _listener_task.done():
        _listener_task = asyncio.create_task(listen_loop())


def stop_listener():
    global _listener_task
    if _listener_task and not _listener_task.done():
        _listener_task.cancel()
        _listener_task = None
