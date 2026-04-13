"""
FastAPI 主入口
"""
import logging
import sys
import os

# 确保 backend/ 目录在 Python 路径中
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from database import init_db
from routers import config, positions, trades, ws, wallet, analytics
from services.broadcaster import broadcaster

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库
    await init_db()
    broadcaster.log("系统已启动，等待配置...")
    logger.info("Database initialized")

    # 如果配置了 bot_enabled=true，自动启动
    from database import AsyncSessionLocal, ConfigModel
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConfigModel).where(ConfigModel.key == "bot_enabled")
        )
        row = result.scalar_one_or_none()
        if row and row.value.lower() == "true":
            from services.ca_listener import start_listener
            from services.position_monitor import start_monitor
            start_listener()
            start_monitor()
            broadcaster.log("Bot 自动启动（上次配置为已启用）")

    # 补拉历史持仓/交易的 AVE 代币数据（异步后台，不阻塞启动）
    import asyncio
    asyncio.create_task(_backfill_token_meta())

    yield

    # 关闭时停止服务
    from services.ca_listener import stop_listener
    from services.position_monitor import stop_monitor
    from services.ave_client import ave_client
    stop_listener()
    stop_monitor()
    await ave_client.close()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Meme Trader API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config.router)
app.include_router(positions.router)
app.include_router(trades.router)
app.include_router(ws.router)
app.include_router(wallet.router)
app.include_router(analytics.router)


@app.post("/api/sweep")
async def sweep_residual():
    """残留代币扫描卖出——后台异步执行，进度通过 WS 日志流推送"""
    import asyncio as _aio
    from routers.positions import _run_sweep_background
    _aio.create_task(_run_sweep_background())
    return {"started": True, "message": "扫描已在后台启动，请查看实时日志"}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ── 前端静态文件托管 ──────────────────────────────────────────
_FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_FRONTEND_DIST):
    # 修正 Starlette 内置 MIME 类型（.js 默认 application/x-js 会被浏览器拒绝执行 ES module）
    import mimetypes
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")

    from starlette.staticfiles import StaticFiles as _SF

    class _SPAStaticFiles(_SF):
        async def get_response(self, path, scope):
            try:
                return await super().get_response(path, scope)
            except Exception:
                return await super().get_response("index.html", scope)

    app.mount("/", _SPAStaticFiles(directory=_FRONTEND_DIST, html=True), name="spa")


@app.post("/api/token/refresh_meta")
async def refresh_token_meta(ca: str, chain: str = "BSC"):
    """手动触发指定 CA 的 AVE 数据拉取（force=True 强制刷新）"""
    from services.ave_data_client import fetch_and_cache_token
    detail = await fetch_and_cache_token(ca, chain, force=True)
    if detail:
        return {"success": True, "token_name": detail.token_name, "symbol": detail.symbol}
    return {"success": False}


async def _backfill_token_meta():
    """启动时补拉所有无 AVE 数据的历史 CA"""
    import asyncio
    from database import AsyncSessionLocal, Position, Trade, TokenDetail
    from sqlalchemy import select
    from services.ave_data_client import fetch_and_cache_token

    try:
        # 收集所有需要补拉的 (ca, chain) 对
        pairs: set[tuple[str, str]] = set()
        async with AsyncSessionLocal() as session:
            # 已有 token_detail 的 CA 集合
            r = await session.execute(select(TokenDetail.ca))
            cached_cas = {row[0] for row in r.all()}

            # 持仓 + 历史交易中未缓存的
            for model in (Position, Trade):
                r2 = await session.execute(select(model.ca, model.chain))
                for ca, chain in r2.all():
                    if ca and ca not in cached_cas:
                        pairs.add((ca, chain))

        if not pairs:
            return

        logger.info(f"补拉 AVE 代币数据: {len(pairs)} 个")
        for ca, chain in pairs:
            try:
                await fetch_and_cache_token(ca, chain)
                await asyncio.sleep(0.3)  # 避免触发频率限制
            except Exception as e:
                logger.debug(f"backfill {ca}: {e}")
        logger.info("AVE 代币数据补拉完成")
    except Exception as e:
        logger.error(f"_backfill_token_meta error: {e}")


if __name__ == "__main__":
    import uvicorn
    from config import get_settings
    s = get_settings()
    uvicorn.run("main:app", host="0.0.0.0", port=s.backend_port, reload=False)
