"""
AVE 数据 API 客户端
文档: https://ave-cloud.gitbook.io/data-api/rest/tokens
API Key 和 Base URL 从数据库 config 表读取，可在前端实时修改。
本地开发时网络可能超时，所有方法均优雅降级返回空数据
"""
import logging
import json
from datetime import datetime, timedelta
import httpx

logger = logging.getLogger(__name__)

# chain_id 映射
CHAIN_ID_MAP = {
    "BSC": 56,
    "ETH": 1,
    "SOL": "solana",
    "XLAYER": 196,
}


async def _get_ave_data_cfg() -> tuple[str, str]:
    """从 DB config 表读取 Data API key 和 base url"""
    try:
        from database import AsyncSessionLocal, ConfigModel
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ConfigModel).where(
                ConfigModel.key.in_(["ave_data_api_key", "ave_data_api_url"])
            ))
            rows = {r.key: r.value for r in result.scalars().all()}
            key = rows.get("ave_data_api_key", "").strip()
            url = rows.get("ave_data_api_url", "https://ave-api.cloud").rstrip("/")
            return key, url
    except Exception:
        return "SW59NmZFRG2yfRSSWvKlzTuAuZBFl5SUUCV2DUX5rg5eK8n6sipMlLkwXCX5qHGw", "https://ave-api.cloud"


def _get_system_proxy() -> str | None:
    """读取 Windows 注册表里的系统代理设置"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        )
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if enabled:
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if server:
                if not server.startswith("http"):
                    server = "http://" + server
                return server
    except Exception:
        pass
    return None


class AveDataClient:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._current_key: str = ""
        self._current_url: str = ""

    async def _get_client(self) -> httpx.AsyncClient:
        key, url = await _get_ave_data_cfg()
        if self._client is None or self._client.is_closed or key != self._current_key or url != self._current_url:
            if self._client and not self._client.is_closed:
                await self._client.aclose()
            self._current_key = key
            self._current_url = url
            proxy = _get_system_proxy()
            self._client = httpx.AsyncClient(
                base_url=url,
                headers={
                    "Ave-Auth": key,
                    "Content-Type": "application/json",
                },
                timeout=8.0,
                proxies=proxy,
            )
        return self._client

    async def _get(self, path: str, params: dict) -> dict | None:
        try:
            client = await self._get_client()
            r = await client.get(path, params=params)
            r.raise_for_status()
            data = r.json()
            logger.info(f"AVE data API {path} -> {str(data)[:300]}")
            return data
        except httpx.TimeoutException:
            logger.info(f"AVE data API timeout: {path}")
            return None
        except Exception as e:
            logger.info(f"AVE data API error {path}: {type(e).__name__}: {e}")
            return None

    async def get_base_info(self, ca: str, chain: str) -> dict:
        chain_id = CHAIN_ID_MAP.get(chain.upper(), 56)
        data = await self._get("/token/base_info", {"token": ca, "chain_id": chain_id})
        return data or {}

    async def get_kline(self, ca: str, chain: str, interval: str = "1h", limit: int = 100) -> list:
        chain_id = CHAIN_ID_MAP.get(chain.upper(), 56)
        data = await self._get("/token/kline", {
            "token": ca, "chain_id": chain_id,
            "interval": interval, "limit": limit,
        })
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data", data.get("klines", []))
        return []

    async def get_holders(self, ca: str, chain: str) -> list:
        chain_id = CHAIN_ID_MAP.get(chain.upper(), 56)
        data = await self._get("/token/holders", {"token": ca, "chain_id": chain_id})
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data", data.get("holders", []))
        return []

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


ave_data_client = AveDataClient()


async def get_token_meta(ca: str, chain: str) -> dict:
    """
    获取代币 name/symbol/logo_url，优先查 token_detail（AVE），fallback 到 ca_feed。
    此函数供所有需要显示代币名称的地方调用。
    """
    from database import AsyncSessionLocal, TokenDetail, CaFeed
    from sqlalchemy import select
    import json as _json

    # 优先从 token_detail（AVE 真实数据）
    try:
        async with AsyncSessionLocal() as session:
            r = await session.execute(
                select(TokenDetail)
                .where(TokenDetail.ca == ca)
                .order_by(TokenDetail.fetched_at.desc())
                .limit(1)
            )
            td = r.scalars().first()
            if td:
                name = td.token_name or ""
                symbol = td.symbol or ""
                # 若字段为空，尝试从 base_info_json 补充解析
                if (not name or not symbol) and td.base_info_json:
                    try:
                        raw = _json.loads(td.base_info_json)
                        info = raw.get("data", raw) if isinstance(raw, dict) else {}
                        if not name:
                            name = str(info.get("name", "") or info.get("token_name", ""))
                        if not symbol:
                            symbol = str(info.get("symbol", ""))
                    except Exception:
                        pass
                if name or symbol:
                    return {"token_name": name, "symbol": symbol, "logo_url": ""}
    except Exception:
        pass

    # Fallback 到 ca_feed（WS 推送数据，名称可能打码如 **xxx**，去掉 * 显示）
    try:
        async with AsyncSessionLocal() as session:
            r = await session.execute(
                select(CaFeed)
                .where(CaFeed.ca == ca)
                .order_by(CaFeed.received_at.desc())
                .limit(1)
            )
            feed = r.scalars().first()
            if feed:
                name = (feed.token_name or "").replace("*", "").strip()
                symbol = (feed.symbol or "").replace("*", "").strip()
                logo_url = ""
                if feed.raw_json:
                    raw = _json.loads(feed.raw_json)
                    logo_url = raw.get("logo_url", "") or ""
                    if not name:
                        name = (raw.get("name", "") or "").replace("*", "").strip()
                    if not symbol:
                        symbol = (raw.get("symbol", "") or "").replace("*", "").strip()
                return {"token_name": name, "symbol": symbol, "logo_url": logo_url}
    except Exception:
        pass

    return {"token_name": "", "symbol": "", "logo_url": ""}


async def fetch_and_cache_token(ca: str, chain: str, force: bool = False):
    """
    拉取 AVE 数据并缓存到 token_detail 表
    24小时内同一 CA 不重复拉取（除非 force=True）
    """
    from database import AsyncSessionLocal, TokenDetail
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TokenDetail)
            .where(TokenDetail.ca == ca, TokenDetail.chain == chain)
            .order_by(TokenDetail.fetched_at.desc())
            .limit(1)
        )
        existing = result.scalars().first()

        # 24小时内有缓存则跳过
        if existing and not force:
            age = datetime.utcnow() - existing.fetched_at
            if age < timedelta(hours=24):
                return existing

    # 并发拉取三种数据
    base_info, kline_1h, kline_5m, holders = {}, [], [], []
    try:
        import asyncio
        base_info, kline_1h, kline_5m, holders = await asyncio.gather(
            ave_data_client.get_base_info(ca, chain),
            ave_data_client.get_kline(ca, chain, "1h", 168),   # 7天
            ave_data_client.get_kline(ca, chain, "5m", 288),   # 24小时
            ave_data_client.get_holders(ca, chain),
        )
    except Exception as e:
        logger.debug(f"fetch_and_cache_token gather error: {e}")

    # AVE API 可能返回 {code: 0, data: {...}} 或直接 {...}
    if isinstance(base_info, dict) and "data" in base_info:
        info = base_info.get("data") or {}
    else:
        info = base_info or {}

    async with AsyncSessionLocal() as session:
        detail = TokenDetail(
            ca=ca,
            chain=chain,
            fetched_at=datetime.utcnow(),
            base_info_json=json.dumps(base_info, ensure_ascii=False) if base_info else "",
            kline_1h_json=json.dumps(kline_1h, ensure_ascii=False) if kline_1h else "",
            kline_5m_json=json.dumps(kline_5m, ensure_ascii=False) if kline_5m else "",
            holders_json=json.dumps(holders, ensure_ascii=False) if holders else "",
            token_name=str(info.get("name", "") or info.get("token_name", "")),
            symbol=str(info.get("symbol", "")),
            total_supply=str(info.get("total_supply", "")),
        )
        session.add(detail)
        await session.commit()
        logger.info(f"AVE token cached: {ca} name={detail.token_name} symbol={detail.symbol}")
        return detail
