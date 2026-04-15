"""
AVE 链钱包交易客户端
文档: https://docs-bot-api.ave.ai/lian-qian-bao-jiao-yi-rest-api

链钱包流程（用户自管私钥）：
  EVM:    createEvmTx → 本地签名 → sendSignedEvmTx
  Solana: createSolanaTx → 本地签名 → sendSignedSolanaTx

认证 Header: AVE-ACCESS-KEY
API Key 和 Base URL 从数据库 config 表读取，可在前端修改。

Solana 买入说明：
  - inTokenAddress 传 "sol"（用 SOL 买）或 USDC/USDT 地址
  - 本系统默认用 SOL 买入，amount_usdt 参数实际表示 SOL 数量
"""
import httpx
import logging
import base64

logger = logging.getLogger(__name__)

# 链名称映射（AVE 文档要求小写）
CHAIN_NAME_MAP = {
    "BSC": "bsc",
    "ETH": "eth",
    "SOL": "solana",
    "XLAYER": "base",   # XLAYER 暂时 fallback base，后续确认
}

# 各链买入时使用的 inToken
# Solana 用 Wrapped SOL mint 地址（AVE createSolanaTx 不接受 "sol" 字符串）
# EVM 用 USDT
BUY_IN_TOKEN = {
    "bsc":     "0x55d398326f99059fF775485246999027B3197955",  # USDT BEP20
    "eth":     "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT ERC20
    "solana":  "sol",   # AVE Solana 接口规定传 "sol"
    "base":    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC on Base
}

# 各链原生币（BNB/ETH）地址（用于 BNB 回退买入模式）
BUY_NATIVE_TOKEN = {
    "bsc": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",  # BNB
    "eth": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",  # ETH
}
# 原生币 decimals（BNB/ETH 均为 18）
BUY_NATIVE_DECIMALS = {"bsc": 18, "eth": 18}
SELL_OUT_TOKEN = {
    "bsc":     "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",  # BNB
    "eth":     "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",  # ETH
    "solana":  "sol",   # AVE Solana 接口规定传 "sol"
    "base":    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",  # ETH on Base
}

# 主链币价格缓存（秒级，避免每笔交易都查）
import time as _time
_native_price_cache: dict[str, tuple[float, float]] = {}  # chain -> (price, ts)
_NATIVE_PRICE_TTL = 60.0  # 60秒缓存

# 代币价格缓存（监控轮询用，避免频繁 getAmountOut 触发 429）
_token_price_cache: dict[str, tuple[float, float]] = {}  # "chain:ca" -> (price, ts)
_TOKEN_PRICE_TTL = 20.0  # 20秒缓存（监控间隔10s，缓存20s保证2轮一次真实查询）

# ── Nonce 并发互斥（防止多个协程同时签名时用同一 nonce）──
import asyncio as _asyncio_nonce
_nonce_locks: dict[int, "_asyncio_nonce.Lock"] = {}   # chain_id -> Lock
_nonce_local: dict[int, int] = {}                      # chain_id -> last allocated nonce

# 静态兜底价格（网络不通时使用）
_NATIVE_PRICE_FALLBACK = {"bsc": 600.0, "eth": 3000.0, "solana": 150.0, "base": 3000.0}


async def _get_native_price_usd_async(chain_name: str) -> float:
    """异步查询主链币/USDT价格，通过 AVE getAmountOut 接口（已在 async 上下文中）。带60秒缓存。"""
    cached = _native_price_cache.get(chain_name)
    if cached and _time.time() - cached[1] < _NATIVE_PRICE_TTL:
        return cached[0]

    fallback = _NATIVE_PRICE_FALLBACK.get(chain_name, 600.0)

    # 用 AVE getAmountOut: 1个主链币 → USDT，反推主链币/USDT价格
    # SOL 链用 Solana getAmountOut: 1 SOL(1e9 lamports) → USDC(EPjFWdd5...)
    NATIVE_TO_USDT = {
        "bsc":    ("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                   "0x55d398326f99059fF775485246999027B3197955", 18, 18),  # BNB→USDT(18dec)
        "eth":    ("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                   "0xdAC17F958D2ee523a2206206994597C13D831ec7", 18, 6),   # ETH→USDT(6dec)
        "base":   ("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                   "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 18, 6),   # ETH→USDC(6dec)
        "solana": ("sol",
                   "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 9, 6),  # SOL→USDC(6dec)
    }
    pair = NATIVE_TO_USDT.get(chain_name)
    if not pair:
        return fallback

    in_token, out_token, in_dec, out_dec = pair
    try:
        key, url = await _get_ave_trade_cfg()
        if not key:
            return fallback
        in_amount_raw = str(10 ** in_dec)  # 1个主链币
        async with httpx.AsyncClient(
            base_url=url,
            headers={"AVE-ACCESS-KEY": key, "Content-Type": "application/json"},
            timeout=5.0,
        ) as c:
            resp = await c.post("/v1/thirdParty/chainWallet/getAmountOut", json={
                "chain": chain_name,
                "inAmount": in_amount_raw,
                "inTokenAddress": in_token,
                "outTokenAddress": out_token,
                "swapType": "buy",
            })
            data = resp.json().get("data", {})
            estimate_out = data.get("estimateOut", "0")
            if estimate_out and estimate_out != "0":
                price = int(estimate_out) / (10 ** out_dec)
                _native_price_cache[chain_name] = (price, _time.time())
                logger.info(f"native price {chain_name}: {price:.2f} USD (via AVE)")
                return price
    except Exception as e:
        logger.warning(f"获取主链币价格失败({e})，使用静态估算: {fallback}")

    _native_price_cache[chain_name] = (fallback, _time.time())
    return fallback

# inToken decimals（用于计算 inAmount 最小精度）
BUY_IN_DECIMALS = {
    "bsc": 18,   # USDT BEP20 = 18 decimals
    "eth": 6,    # USDT ERC20 = 6 decimals
    "solana": 9, # SOL = 9 decimals (lamports)
    "base": 6,   # USDC = 6 decimals
}

# SOL decimals
SOL_DECIMALS = 9

# 兼容旧代码
USDT_ADDRESS = BUY_IN_TOKEN
USDT_DECIMALS = BUY_IN_DECIMALS


async def _get_ave_trade_cfg() -> tuple[str, str]:
    """从 DB config 表读取 Trade API key 和 base url"""
    try:
        from database import AsyncSessionLocal, ConfigModel
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ConfigModel).where(
                ConfigModel.key.in_(["ave_trade_api_key", "ave_trade_api_url"])
            ))
            rows = {r.key: r.value for r in result.scalars().all()}
            from config import get_settings
            s = get_settings()
            key = (rows.get("ave_trade_api_key") or s.ave_api_key or "").strip()
            url = (rows.get("ave_trade_api_url") or s.ave_base_url or "https://bot-api.ave.ai").rstrip("/")
            return key, url
    except Exception:
        from config import get_settings
        s = get_settings()
        return s.ave_api_key, s.ave_base_url.rstrip("/")


# ── Approve 内存缓存 ──────────────────────────────────────────────────────────
# 记录已成功 approve MAX_UINT256 的 (chain_id, token_addr, spender) 组合
# approve 一次后永久有效，不需要再查链上 allowance
_approved_cache: set[tuple[int, str, str]] = set()


async def _get_gas_cfg() -> dict:
    """从 DB 读取 GAS 配置，返回 {gas_price_multiplier, approve_gas_multiplier, broadcast_mode}"""
    try:
        from database import AsyncSessionLocal, ConfigModel
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ConfigModel).where(
                ConfigModel.key.in_([
                    "gas_price_multiplier",
                    "approve_gas_price_gwei",
                    "broadcast_mode",
                ])
            ))
            return {r.key: r.value for r in result.scalars().all()}
    except Exception:
        return {}


class AveClient:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._current_key: str = ""
        self._current_url: str = ""

    async def _get_client(self) -> httpx.AsyncClient:
        key, url = await _get_ave_trade_cfg()
        if self._client is None or self._client.is_closed or key != self._current_key or url != self._current_url:
            if self._client and not self._client.is_closed:
                await self._client.aclose()
            if not key:
                raise ValueError("AVE Trade API Key 未配置，请在配置页面填写")
            self._current_key = key
            self._current_url = url
            self._client = httpx.AsyncClient(
                base_url=url,
                headers={
                    "AVE-ACCESS-KEY": key,
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── 询价 ──────────────────────────────────────────────────────────────────
    async def get_amount_out(
        self,
        chain_name: str,
        in_amount_raw: str,
        in_token: str,
        out_token: str,
        swap_type: str = "buy",
    ) -> dict:
        client = await self._get_client()
        try:
            resp = await client.post("/v1/thirdParty/chainWallet/getAmountOut", json={
                "chain": chain_name,
                "inAmount": in_amount_raw,
                "inTokenAddress": in_token,
                "outTokenAddress": out_token,
                "swapType": swap_type,
            })
            resp.raise_for_status()
            return resp.json().get("data", {})
        except Exception as e:
            logger.error(f"getAmountOut error: {e}")
            return {}

    # ── 构造 EVM 交易 ──────────────────────────────────────────────────────────
    async def _create_evm_tx(
        self,
        chain_name: str,
        creator_address: str,
        in_amount_raw: str,
        in_token: str,
        out_token: str,
        swap_type: str,
        slippage_bps: int = 1000,
    ) -> dict:
        client = await self._get_client()
        resp = await client.post("/v1/thirdParty/chainWallet/createEvmTx", json={
            "chain": chain_name,
            "creatorAddress": creator_address,
            "inAmount": in_amount_raw,
            "inTokenAddress": in_token,
            "outTokenAddress": out_token,
            "swapType": swap_type,
            "slippage": str(slippage_bps),
            "autoSlippage": True,
        })
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", {})
        if not data:
            status = body.get("status", "")
            msg = body.get("msg", "")
            logger.error(f"createEvmTx empty data: {body}")
            # 把 AVE 返回的具体原因附到异常上，供上层分类
            raise ValueError(f"createEvmTx failed: status={status} msg={msg}")
        return data

    # ── 发送签名后的 EVM 交易 ──────────────────────────────────────────────────
    async def _send_signed_evm_tx(
        self,
        chain_name: str,
        request_tx_id: str,
        signed_tx_hex: str,
        use_mev: bool = True,
    ) -> dict:
        client = await self._get_client()
        # 确保带 0x 前缀的 hex 格式（AVE 文档示例值是 0x 开头的 hex）
        if not signed_tx_hex.startswith("0x"):
            signed_tx_hex = "0x" + signed_tx_hex
        resp = await client.post("/v1/thirdParty/chainWallet/sendSignedEvmTx", json={
            "chain": chain_name,
            "requestTxId": request_tx_id,
            "signedTx": signed_tx_hex,
            "useMev": use_mev,
        })
        resp.raise_for_status()
        body = resp.json()
        biz_status = body.get("status", 0)
        if biz_status not in (0, 200):
            raise ValueError(f"AVE sendSignedEvmTx 失败: status={biz_status} msg={body.get('msg')} | tx_prefix={signed_tx_hex[:20]}")
        data = body.get("data", {}) or {}
        return data

    async def _broadcast_evm_tx_direct(self, signed_hex: str, chain_id: int) -> dict:
        """直接广播模式：跳过 AVE sendSignedEvmTx，用 eth_sendRawTransaction 直接上链。

        优点：gasPrice 不受 AVE 模拟 ≥1 Gwei 限制，可跟随实际网络价格（BSC 约 0.07-1 Gwei）。
        缺点：无 AVE MEV 保护，无模拟预检（失败直接上链消耗 gas）。
        返回: {"txHash": str}
        """
        import httpx as _httpx
        import asyncio as _asyncio
        RPC_MAP = {
            56:   "https://bsc-dataseed1.binance.org",
            1:    "https://ethereum-rpc.publicnode.com",
            8453: "https://mainnet.base.org",
        }
        rpc = RPC_MAP.get(chain_id, "https://bsc-dataseed1.binance.org")
        raw = signed_hex if signed_hex.startswith("0x") else "0x" + signed_hex
        async with _httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_sendRawTransaction","params":[raw],"id":1})
            rj = r.json()
            if "error" in rj:
                err_obj = rj["error"]
                err_msg = err_obj.get("message", str(err_obj)) if isinstance(err_obj, dict) else str(err_obj)
                # already known: 同一笔 tx 已在 mempool，不是真正失败，等待其上链即可
                if "already known" in err_msg.lower():
                    from eth_utils import keccak
                    tx_hash = "0x" + keccak(hexstr=raw).hex()
                    logger.info(f"already known：tx 已在 mempool，等待确认: txHash={tx_hash}")
                else:
                    raise ValueError(f"eth_sendRawTransaction 失败: {err_obj}")
            else:
                tx_hash = rj.get("result", "")
                if not tx_hash:
                    raise ValueError("eth_sendRawTransaction 返回空 txHash")
                logger.info(f"直接广播已发送: txHash={tx_hash}，等待链上确认...")

        # 等待 receipt，最多 60 秒（BSC 出块 ~3s）
        async with _httpx.AsyncClient(timeout=10.0) as c:
            for _ in range(20):
                await _asyncio.sleep(3)
                r2 = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[tx_hash],"id":1})
                receipt = r2.json().get("result")
                if receipt:
                    status = receipt.get("status", "0x0")
                    if status == "0x1":
                        logger.info(f"直接广播确认成功: txHash={tx_hash}")
                        return {"txHash": tx_hash}
                    else:
                        raise ValueError(f"直接广播 TX revert: txHash={tx_hash} status={status}")
        logger.warning(f"直接广播 60s 未确认，继续处理: txHash={tx_hash}")
        return {"txHash": tx_hash}

    # ── 构造 Solana 交易 ───────────────────────────────────────────────────────
    async def _create_solana_tx(
        self,
        creator_address: str,
        in_amount_raw: str,
        in_token: str,
        out_token: str,
        swap_type: str,
        slippage_bps: int = 1000,
        fee_lamports: str = "100000",
    ) -> dict:
        client = await self._get_client()
        logger.info(f"createSolanaTx params: creator={creator_address} inToken={in_token} outToken={out_token} inAmount={in_amount_raw}")
        resp = await client.post("/v1/thirdParty/chainWallet/createSolanaTx", json={
            "creatorAddress": creator_address,
            "inAmount": in_amount_raw,
            "inTokenAddress": in_token,
            "outTokenAddress": out_token,
            "swapType": swap_type,
            "slippage": str(slippage_bps),
            "fee": fee_lamports,
            "useMev": False,
            "autoSlippage": True,
        })
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or {}
        if not data:
            biz_status = body.get("status", "")
            biz_msg = body.get("msg", "") or body.get("message", "")
            raise ValueError(f"createSolanaTx 失败: status={biz_status} msg={biz_msg} body={str(body)[:200]}")
        return data

    # ── 发送签名后的 Solana 交易 ───────────────────────────────────────────────
    async def _send_signed_solana_tx(
        self,
        request_tx_id: str,
        signed_tx_b64: str,
        use_mev: bool = False,
    ) -> dict:
        client = await self._get_client()
        resp = await client.post("/v1/thirdParty/chainWallet/sendSignedSolanaTx", json={
            "requestTxId": request_tx_id,
            "signedTx": signed_tx_b64,
            "useMev": use_mev,
        })
        resp.raise_for_status()
        body = resp.json()
        biz_status = body.get("status", 0)
        if biz_status not in (0, 200):
            raise ValueError(f"AVE sendSignedSolanaTx 失败: status={biz_status} msg={body.get('msg')}")
        return body.get("data", {}) or {}

    # ── 签名工具 ───────────────────────────────────────────────────────────────
    def _sign_evm_tx(
        self,
        tx_content: dict,
        private_key: str,
        chain_id: int,
        gas_multiplier: float = 1.2,
        nonce: int = -1,
        direct_broadcast: bool = False,
    ) -> str:
        """用 eth_account 签名 EVM 交易，返回签名后的 hex。

        AVE txContent 只包含 data/to/value，不含 gasLimit/gasPrice/nonce。
        - data: 不带 0x 前缀的 hex，需要加上
        - value: 十进制字符串 "0" 或 "1000000..."
        - nonce: 若 >= 0 则直接使用（由 _alloc_nonce 预先分配），否则临时查链上
        - gas_multiplier: gasPrice 倍数（可配置，影响打包速度）
        - direct_broadcast: True 时用真实网络 gasPrice，不受 AVE 模拟最低限制
        """
        from eth_account import Account
        from eth_utils import to_checksum_address

        raw_data = tx_content.get("data", "")
        # data 字段不带 0x 时补上
        if raw_data and not raw_data.startswith("0x"):
            raw_data = "0x" + raw_data

        raw_value = tx_content.get("value", "0")
        value = int(raw_value) if raw_value else 0

        # 确保 to 是 EIP-55 checksum 地址（eth_account 要求）
        to_addr = tx_content.get("to", "")
        try:
            to_addr = to_checksum_address(to_addr)
        except Exception:
            pass

        # 若调用方未预分配 nonce，则临时查链上（单笔场景下无并发问题）
        if nonce < 0:
            nonce, gas_price = self._fetch_nonce_and_gas(private_key, chain_id, multiplier=gas_multiplier, direct_broadcast=direct_broadcast)
        else:
            _, gas_price = self._fetch_nonce_and_gas(private_key, chain_id, multiplier=gas_multiplier, direct_broadcast=direct_broadcast)

        tx = {
            "to":       to_addr,
            "data":     raw_data or "0x",
            "value":    value,
            # 优先用 AVE 返回的 gasLimit（某些复杂合约需要更多 gas）；默认 500k 兜底
            "gas":      int(tx_content.get("gas") or tx_content.get("gasLimit") or 500_000),
            "gasPrice": gas_price,
            "chainId":  chain_id,
            "nonce":    nonce,
        }
        logger.info(f"signing EVM tx: to={tx['to']} value={tx['value']} gas={tx['gas']} gasPrice={tx['gasPrice']} nonce={tx['nonce']} chain={chain_id}")

        try:
            signed = Account.sign_transaction(tx, private_key)
        except Exception as sign_err:
            logger.error(f"sign_transaction failed: {sign_err} | tx={tx}")
            raise
        return signed.rawTransaction.hex()

    def _fetch_nonce_and_gas(
        self,
        private_key: str,
        chain_id: int,
        multiplier: float = 1.2,
        direct_broadcast: bool = False,
    ) -> tuple[int, int]:
        """同步查询链上 nonce 和 gasPrice（在签名前调用，无需 async）

        multiplier: gasPrice 倍数（1.0=不加速, 1.2=20%溢价, 2.0=加速）
        direct_broadcast: True=直接广播模式，使用实际网络 gasPrice（可低至 0.05 Gwei）
                          False=AVE 广播模式，强制 ≥1 Gwei（AVE 模拟要求）
        """
        import httpx as _httpx
        from eth_account import Account as _Account

        address = _Account.from_key(private_key).address

        RPC = {
            56:   "https://bsc-dataseed1.binance.org",
            1:    "https://ethereum-rpc.publicnode.com",
            8453: "https://mainnet.base.org",
        }
        # 各链最低可接受 gasPrice（wei）
        # AVE sendSignedEvmTx 模拟要求 BSC ≥ 1 Gwei；直接广播模式无此限制（可低至 0.05 Gwei）
        MIN_GAS_PRICE = {
            56:    1_000_000_000,   # BSC  最低 1 Gwei（AVE 模拟要求；直接广播模式可不受此限）
            1:     1_000_000_000,   # ETH  最低 1 Gwei
            8453:     50_000_000,   # Base 最低 0.05 Gwei
        }
        # 直接广播模式的最低 gasPrice（跟随实际网络，无 AVE 模拟限制）
        MIN_GAS_PRICE_DIRECT = {
            56:      50_000_000,    # BSC  最低 0.05 Gwei（实测网络最低）
            1:    1_000_000_000,    # ETH  最低 1 Gwei
            8453:    50_000_000,    # Base 最低 0.05 Gwei
        }
        # 默认 gasPrice（RPC 查询失败时使用；正常情况跟随 RPC 返回值）
        DEFAULT_GAS_PRICE = {
            56:    1_000_000_000,   # BSC  1 Gwei
            1:    10_000_000_000,   # ETH  10 Gwei
            8453:    100_000_000,   # Base 0.1 Gwei
        }

        rpc_url = RPC.get(chain_id, "https://bsc-dataseed1.binance.org")
        min_gp  = MIN_GAS_PRICE_DIRECT.get(chain_id, 50_000_000) if direct_broadcast else MIN_GAS_PRICE.get(chain_id, 1_000_000_000)
        def_gp  = DEFAULT_GAS_PRICE.get(chain_id, 3_000_000_000)

        try:
            with _httpx.Client(timeout=8.0) as c:
                r1 = c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_getTransactionCount","params":[address,"pending"],"id":1})
                nonce = int(r1.json()["result"], 16)
                r2 = c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_gasPrice","params":[],"id":2})
                gas_price = int(r2.json()["result"], 16)
                # 低于最低值时用默认值
                if gas_price < min_gp:
                    gas_price = def_gp if not direct_broadcast else min_gp
                # 乘以可配置倍数（跟随网络行情 + 溢价）
                gas_price = int(gas_price * multiplier)
                # 乘完后再次保证不低于链最低值
                if gas_price < min_gp:
                    gas_price = min_gp
                mode_tag = "直接广播" if direct_broadcast else "AVE广播"
                logger.info(f"RPC nonce={nonce} gasPrice={gas_price} ({gas_price/1e9:.2f}Gwei) chain={chain_id} addr={address[:10]}... [{mode_tag}]")
                return nonce, gas_price
        except Exception as e:
            logger.warning(f"RPC query failed ({e}), using defaults")
            return 0, int(def_gp * multiplier)

    async def _alloc_nonce(self, private_key: str, chain_id: int) -> int:
        """持链级 asyncio.Lock 分配下一个 nonce，防止并发买卖时 nonce 碰撞。

        关键：用 async httpx 查 nonce（不阻塞事件循环），Lock 才能真正序列化并发协程。
        """
        import asyncio as _aio
        import httpx as _httpx
        from eth_account import Account as _Account

        if chain_id not in _nonce_locks:
            _nonce_locks[chain_id] = _aio.Lock()

        address = _Account.from_key(private_key).address
        RPC = {56: "https://bsc-dataseed1.binance.org", 1: "https://ethereum-rpc.publicnode.com", 8453: "https://mainnet.base.org"}
        rpc_url = RPC.get(chain_id, "https://bsc-dataseed1.binance.org")

        async with _nonce_locks[chain_id]:
            # 同时查 latest 和 pending，取较大值作为链上 nonce
            # pending 在重启后可能已过时（上次 pending tx 早已上链），latest 更可靠
            try:
                async with _httpx.AsyncClient(timeout=8.0) as c:
                    r_latest = await c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_getTransactionCount","params":[address,"latest"],"id":1})
                    r_pending = await c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_getTransactionCount","params":[address,"pending"],"id":2})
                    nonce_latest  = int(r_latest.json()["result"], 16)
                    nonce_pending = int(r_pending.json()["result"], 16)
                    nonce_chain = max(nonce_latest, nonce_pending)
            except Exception as e:
                logger.warning(f"_alloc_nonce RPC failed: {e}, using local fallback")
                nonce_chain = _nonce_local.get(chain_id, 0)

            local = _nonce_local.get(chain_id, -1)
            # 只有当本地计数比链上大时才用本地（上一笔在 pending 中未确认）
            if local >= 0 and local + 1 > nonce_chain:
                nonce = local + 1
            else:
                nonce = nonce_chain
            _nonce_local[chain_id] = nonce
            logger.info(f"alloc nonce={nonce} chain={chain_id} (chain={nonce_chain} local_prev={local})")
            return nonce

    def _sign_solana_tx(self, tx_b64: str, private_key_bytes: bytes) -> str:
        """签名 AVE createSolanaTx 返回的 Solana 交易

        AVE txContent 格式：[0x80=version][MessageV0 bytes]（不含签名槽位）
        正确签名方式：sign(raw[0:])（对完整原始字节签名，包含 0x80 前缀）
        然后构建标准 VersionedTransaction: [compact-u16=0x01][sig64][0x80][message]
        """
        from solders.keypair import Keypair
        import base64 as b64

        if len(private_key_bytes) == 32:
            kp = Keypair.from_seed(private_key_bytes)
        elif len(private_key_bytes) == 64:
            kp = Keypair.from_bytes(private_key_bytes)
        else:
            raise ValueError(f"SOL 私钥长度异常: {len(private_key_bytes)} 字节（期望 32 或 64）")

        # 修复 base64 padding
        padded = tx_b64 + '=' * (4 - len(tx_b64) % 4) if len(tx_b64) % 4 else tx_b64
        raw = b64.b64decode(padded)
        if not raw:
            raise ValueError("SOL txContent 为空")

        logger.info(f"_sign_solana_tx: raw={len(raw)}B, first=0x{raw[0]:02x}")

        # 对完整 raw 字节签名（包含 0x80 version 前缀）
        sig = kp.sign_message(raw)
        sig_bytes = bytes(sig)

        # 构建标准 VersionedTransaction 序列化:
        # [compact-u16=0x01 表示1个签名][64字节签名][原始raw(含0x80+message)]
        signed_raw = bytes([0x01]) + sig_bytes + raw
        result = b64.b64encode(signed_raw).decode()
        logger.info(f"_sign_solana_tx: 签名成功, signed={len(result)}chars")
        return result

    # ── 公开接口：buy ──────────────────────────────────────────────────────────
    async def buy(
        self,
        ca: str,
        chain: str,
        amount_usdt: float,
        wallet_address: str,
    ) -> dict:
        """
        买入代币（链钱包，用户自签名）
        amount_usdt: 统一传 USDT 金额，内部自动按链换算：
          - BSC/ETH：直接用 USDT 买，amount_usdt 即 USDT 数量
          - BASE/XLAYER：用 USDC 买，USDC ≈ 1:1 USDT，amount_usdt 即 USDC 数量
          - SOL：用 SOL 买，amount_usdt / sol_price → SOL 数量（实时换算，60s缓存）
        返回: {"success": bool, "tx": str, "token_amount": float, "price": float}
        """
        from services.wallet_manager import wallet_manager

        chain_name = CHAIN_NAME_MAP.get(chain.upper(), chain.lower())
        in_token = BUY_IN_TOKEN.get(chain_name)
        if not in_token:
            raise ValueError(f"不支持的链: {chain}")

        # ── 各链换算买入数量 ───────────────────────────────────────
        decimals = BUY_IN_DECIMALS.get(chain_name, 18)

        if chain_name == "solana":
            # SOL 链：amount_usdt(U) / sol_price → SOL 数量
            sol_price = await _get_native_price_usd_async("solana")
            sol_amount = amount_usdt / sol_price
            logger.info(f"SOL buy: {amount_usdt}U / {sol_price:.2f}USD = {sol_amount:.6f} SOL")
            in_amount_raw = str(int(sol_amount * (10 ** decimals)))
        else:
            # BSC/ETH：直接用 USDT；BASE/XLAYER：直接用 USDC（≈1U）
            in_amount_raw = str(int(amount_usdt * (10 ** decimals)))

        # ── BNB/ETH 回退模式：USDT 不足时自动换成原生币买入（仅 BSC/ETH） ───────
        using_native_fallback = False
        if chain_name in BUY_NATIVE_TOKEN:
            try:
                bnb_fallback_enabled = False
                from database import AsyncSessionLocal as _ASL, ConfigModel as _CM
                from sqlalchemy import select as _sel
                async with _ASL() as _sess:
                    _row = (await _sess.execute(_sel(_CM).where(_CM.key == "buy_with_bnb_fallback_enabled"))).scalar_one_or_none()
                    if _row and _row.value.lower() == "true":
                        bnb_fallback_enabled = True
                if bnb_fallback_enabled:
                    # 查 USDT 余额
                    import httpx as _httpx_fb
                    RPC_FB = {"bsc": "https://bsc-dataseed1.binance.org", "eth": "https://ethereum-rpc.publicnode.com"}
                    rpc_fb = RPC_FB.get(chain_name, "https://bsc-dataseed1.binance.org")
                    addr_padded = wallet_address[2:].zfill(64) if wallet_address.startswith("0x") else wallet_address.zfill(64)
                    async with _httpx_fb.AsyncClient(timeout=6.0) as _hc:
                        _r = await _hc.post(rpc_fb, json={"jsonrpc":"2.0","method":"eth_call",
                            "params":[{"to": in_token, "data": "0x70a08231" + addr_padded}, "latest"],"id":1})
                        bal_hex = _r.json().get("result", "0x0") or "0x0"
                        usdt_bal_raw = int(bal_hex, 16) if bal_hex and bal_hex != "0x" else 0
                    need_raw = int(in_amount_raw) * 95 // 100  # 允许5%误差
                    if usdt_bal_raw < need_raw:
                        # USDT 不足 → 切换到原生币
                        native_price = await _get_native_price_usd_async(chain_name)
                        native_decimals = BUY_NATIVE_DECIMALS[chain_name]
                        native_amount = amount_usdt / native_price
                        native_amount_raw = str(int(native_amount * (10 ** native_decimals)))
                        usdt_bal_u = usdt_bal_raw / (10 ** decimals)
                        logger.info(
                            f"BNB fallback: USDT余额 {usdt_bal_u:.4f}U < 需要 {amount_usdt}U，"
                            f"切换BNB: {native_amount:.6f} BNB (≈{amount_usdt}U @ {native_price:.2f})"
                        )
                        in_token = BUY_NATIVE_TOKEN[chain_name]
                        in_amount_raw = native_amount_raw
                        using_native_fallback = True
            except Exception as _fb_e:
                logger.warning(f"BNB fallback 检查失败，继续用USDT: {_fb_e}")

        try:
            if chain_name == "solana":
                return await self._buy_solana(
                    ca, chain, chain_name, in_amount_raw, in_token,
                    wallet_address, wallet_manager
                )
            else:
                return await self._buy_evm(
                    ca, chain, chain_name, in_amount_raw, in_token,
                    wallet_address, wallet_manager,
                    skip_approve=using_native_fallback,
                )
        except Exception as e:
            logger.error(f"Buy error: {e}")
            raise

    async def _ensure_erc20_approved(
        self,
        token_addr: str,
        spender: str,
        private_key: str,
        chain_id: int,
        min_amount: int,
    ) -> None:
        """检查 ERC20 allowance，不足时发送 approve(max) 交易并等待确认

        优化：
        - approve MAX_UINT256 成功后写入内存缓存，同 session 不重查链上
        - approve 使用低 gasPrice（不需要抢速度），swap 用高 gasPrice
        """
        import asyncio as _asyncio
        import httpx as _httpx
        from eth_account import Account as _Account

        addr = _Account.from_key(private_key).address
        cache_key = (chain_id, token_addr.lower(), spender.lower())

        # ── 命中缓存：已知 MAX_UINT256 授权，直接跳过 ────────────────────────
        if cache_key in _approved_cache:
            logger.info(f"ERC20 approve cache hit: token={token_addr[:10]} spender={spender[:10]} chain={chain_id}")
            return False

        RPC = {56: "https://bsc-dataseed1.binance.org", 1: "https://ethereum-rpc.publicnode.com", 8453: "https://mainnet.base.org"}
        rpc_url = RPC.get(chain_id, "https://bsc-dataseed1.binance.org")

        # 查询 allowance(addr, spender)
        data_allowance = "0xdd62ed3e" + addr[2:].zfill(64) + spender[2:].zfill(64)
        with _httpx.Client(timeout=8.0) as c:
            r = c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":token_addr,"data":data_allowance},"latest"],"id":1})
            result_hex = r.json().get("result","0x0")
            current_allowance = int(result_hex, 16) if result_hex and result_hex != "0x" else 0

        logger.info(f"ERC20 allowance: {current_allowance} (need {min_amount}) token={token_addr[:10]} spender={spender[:10]}")
        if current_allowance >= min_amount:
            # 链上已有足够授权（可能是上次 MAX_UINT256），写入缓存
            _approved_cache.add(cache_key)
            return False  # 已授权，不需要 approve

        # ── 发送 approve(spender, 2^256-1) 交易 ─────────────────────────────
        MAX_UINT256 = (1 << 256) - 1
        approve_data = (
            "0x095ea7b3"
            + spender[2:].zfill(64)
            + hex(MAX_UINT256)[2:].zfill(64)
        )

        # 读取 approve gasPrice 配置（跟随网络，默认比 swap 稍低）
        gas_cfg = await _get_gas_cfg()
        try:
            approve_gwei = float(gas_cfg.get("approve_gas_price_gwei", "0.0"))
        except Exception:
            approve_gwei = 0.0
        # approve 不需要抢速度；若配置为0则跟随网络 gasPrice（RPC查询）
        # 如果配置了固定值则使用，但不低于链最低值
        MIN_GAS_PRICE = {56: 1_000_000_000, 1: 1_000_000_000, 8453: 50_000_000}
        # 先用 _alloc_nonce 分配 nonce（持锁，防并发碰撞），再查 gasPrice
        nonce = await self._alloc_nonce(private_key, chain_id)
        if approve_gwei > 0:
            approve_gas_price = max(
                int(approve_gwei * 1e9),
                MIN_GAS_PRICE.get(chain_id, 50_000_000),
            )
        else:
            # 跟随网络 gasPrice，乘以0.8（approve不需要抢）
            _, net_gas = self._fetch_nonce_and_gas(private_key, chain_id, multiplier=0.8)
            approve_gas_price = max(net_gas, MIN_GAS_PRICE.get(chain_id, 50_000_000))

        from eth_account import Account
        from eth_utils import to_checksum_address as _cs
        approve_tx = {
            "to": _cs(token_addr),
            "data": approve_data,
            "value": 0,
            "gas": 60_000,
            "gasPrice": approve_gas_price,
            "chainId": chain_id,
            "nonce": nonce,
        }
        logger.info(f"Sending approve tx: token={token_addr[:10]} spender={spender[:10]} chain={chain_id} gasPrice={approve_gas_price/1e9:.2f}Gwei")
        signed = Account.sign_transaction(approve_tx, private_key)
        raw_hex = signed.rawTransaction.hex()

        with _httpx.Client(timeout=30.0) as c:
            r2 = c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_sendRawTransaction","params":[raw_hex],"id":2})
            r2j = r2.json()
            if "error" in r2j:
                err_msg = str(r2j['error'])
                # nonce too low：链上 nonce 比本地分配的高（重启后 local 被清空导致）
                # → 从链上重查 latest nonce 修正本地状态，重新签名重试一次
                if "nonce too low" in err_msg.lower() or "nonce" in err_msg.lower():
                    logger.warning(f"approve nonce too low (nonce={nonce})，重查链上 nonce 并重试")
                    try:
                        with _httpx.Client(timeout=8.0) as cc:
                            r_nc = cc.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_getTransactionCount","params":[addr,"latest"],"id":10})
                            latest_nonce = int(r_nc.json()["result"], 16)
                        _nonce_local[chain_id] = latest_nonce
                        approve_tx["nonce"] = latest_nonce
                        signed2 = Account.sign_transaction(approve_tx, private_key)
                        raw_hex2 = signed2.rawTransaction.hex()
                        r2b = c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_sendRawTransaction","params":[raw_hex2],"id":2})
                        r2j = r2b.json()
                        if "error" in r2j:
                            raise ValueError(f"approve tx failed: {r2j['error']}")
                        approve_tx_hash = r2j.get("result","")
                        logger.info(f"approve retry success: {approve_tx_hash} nonce={latest_nonce}")
                    except ValueError:
                        raise
                    except Exception as retry_e:
                        raise ValueError(f"approve tx failed: {err_msg}") from retry_e
                else:
                    raise ValueError(f"approve tx failed: {r2j['error']}")
            else:
                approve_tx_hash = r2j.get("result","")
            logger.info(f"approve tx submitted: {approve_tx_hash}")

        # 等待 approve 确认（最多 30 秒）
        for _ in range(15):
            await _asyncio.sleep(2)
            with _httpx.Client(timeout=8.0) as c:
                r3 = c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[approve_tx_hash],"id":3})
                receipt = r3.json().get("result")
                if receipt and receipt.get("status") == "0x1":
                    logger.info(f"approve confirmed: {approve_tx_hash}")
                    # ── 确认后写缓存，下次直接跳过 ────────────────────────
                    _approved_cache.add(cache_key)
                    return True
        logger.warning(f"approve tx not confirmed after 30s, proceeding anyway: {approve_tx_hash}")
        # 即使未确认也写缓存（max approval 一般不会失败）
        _approved_cache.add(cache_key)
        return True  # 仍然返回 True，让调用方重新构造交易

    async def _buy_evm(self, ca, chain, chain_name, in_amount_raw, usdt_addr, wallet_address, wallet_manager, skip_approve=False):
        CHAIN_ID = {"bsc": 56, "eth": 1, "base": 8453}
        chain_id = CHAIN_ID.get(chain_name, 56)

        # 读取 swap gasPrice 倍数 + 广播模式（默认 AVE 广播）
        gas_cfg = await _get_gas_cfg()
        try:
            swap_gas_multiplier = float(gas_cfg.get("gas_price_multiplier", "1.2"))
        except Exception:
            swap_gas_multiplier = 1.2
        direct_broadcast = gas_cfg.get("broadcast_mode", "ave") == "direct"

        # Step 0: 检查钱包 inToken 余额是否足够
        # 原生币（BNB/ETH）= 0xeeee... 时跳过 ERC20 balanceOf 检查（原生币余额检查逻辑不同）
        is_native = usdt_addr.lower() == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        if not is_native and not skip_approve:
            try:
                import httpx as _httpx_bal
                RPC_BAL = {"bsc": "https://bsc-dataseed1.binance.org", "eth": "https://ethereum-rpc.publicnode.com", "base": "https://mainnet.base.org"}
                rpc_bal = RPC_BAL.get(chain_name, "https://bsc-dataseed1.binance.org")
                addr_padded = wallet_address[2:].zfill(64) if wallet_address.startswith("0x") else wallet_address.zfill(64)
                async with _httpx_bal.AsyncClient(timeout=6.0) as _bc:
                    _r = await _bc.post(rpc_bal, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to": usdt_addr, "data": "0x70a08231" + addr_padded}, "latest"],"id":1})
                    bal_hex = _r.json().get("result", "0x0") or "0x0"
                    bal_raw = int(bal_hex, 16) if bal_hex and bal_hex != "0x" else 0
                    if bal_raw < int(in_amount_raw) * 0.95:  # 允许5%误差（精度/手续费）
                        decimals = BUY_IN_DECIMALS.get(chain_name, 18)
                        bal_u = bal_raw / (10 ** decimals)
                        need_u = int(in_amount_raw) / (10 ** decimals)
                        raise ValueError(f"钱包余额不足: 需要 {need_u:.4f}U，当前仅 {bal_u:.4f}U（请充值）")
            except ValueError:
                raise
            except Exception as _be:
                logger.warning(f"买入前余额检查失败（跳过检查继续执行）: {_be}")

        # Step 0.5: getAmountOut 预检——验证代币地址有效且有流动性
        # 用固定 1U 询价（不受 in_amount_raw 大小影响），避免小额导致误判
        precheck_enabled = True
        try:
            from database import AsyncSessionLocal, ConfigModel
            from sqlalchemy import select as _select
            async with AsyncSessionLocal() as _sess:
                _r = await _sess.execute(_select(ConfigModel).where(ConfigModel.key == "buy_precheck_enabled"))
                _row = _r.scalar_one_or_none()
                if _row and _row.value.lower() == "false":
                    precheck_enabled = False
        except Exception:
            pass
        if precheck_enabled:
            probe_amount = str(10 ** BUY_IN_DECIMALS.get(chain_name, 18))  # 1 USDT
            probe = await self.get_amount_out(chain_name, probe_amount, usdt_addr, ca, "buy")
            if not probe or not probe.get("estimateOut") or probe.get("estimateOut") == "0":
                biz_status = probe.get("status") if probe else "N/A"
                raise ValueError(f"代币预检失败（无法询价）: status={biz_status}，合约地址可能无效或无流动性")

        # Step 1: 确保 USDT 已授权给 AVE router（原生币 BNB/ETH 不需要 approve）
        wallet = await wallet_manager.get_wallet_async(chain)
        private_key = wallet["private_key"]

        # 先做一次询价，获取正确的 spender 地址
        pre_tx_data = await self._create_evm_tx(
            chain_name, wallet_address, in_amount_raw,
            usdt_addr, ca, "buy"
        )
        if not pre_tx_data:
            raise ValueError("构造交易失败，返回为空")
        # getAmountOut 返回的 spender 才是正确的授权合约地址
        if not is_native:
            amount_out_data = await self.get_amount_out(chain_name, in_amount_raw, usdt_addr, ca, "buy")
            spender_addr = amount_out_data.get("spender", "") or (pre_tx_data.get("txContent") or {}).get("to", "")
            if spender_addr:
                await self._ensure_erc20_approved(
                    token_addr=usdt_addr,
                    spender=spender_addr,
                    private_key=private_key,
                    chain_id=chain_id,
                    min_amount=int(in_amount_raw),
                )
        # 无论 approve 是否发生，都重新构造交易（pre_tx 的 requestTxId 可能已过期）
        tx_data = await self._create_evm_tx(
            chain_name, wallet_address, in_amount_raw,
            usdt_addr, ca, "buy"
        )
        if not tx_data:
            raise ValueError("构造交易失败，返回为空")

        request_tx_id = tx_data.get("requestTxId", "")
        tx_content = tx_data.get("txContent", {})
        estimate_out = tx_data.get("estimateOut", "0")
        create_price = float(tx_data.get("createPrice", 0))
        # token 精度：优先用 createEvmTx 返回的 decimals，默认18，防止异常值
        _raw_dec = int(tx_data.get("decimals", 18) or 18)
        token_decimals = _raw_dec if 1 <= _raw_dec <= 36 else 18
        logger.info(f"createEvmTx txContent keys={list(tx_content.keys())} gas_in_content={tx_content.get('gas') or tx_content.get('gasLimit')}")

        # Step 2: 本地签名（swap 用高 gasPrice 倍数，加速打包）
        # approve 若命中缓存不更新 _nonce_local，强制重查 pending nonce 避免 nonce too low
        _nonce_local.pop(chain_id, None)
        alloc_nonce = await self._alloc_nonce(private_key, chain_id)
        signed_hex = self._sign_evm_tx(tx_content, private_key, chain_id, gas_multiplier=swap_gas_multiplier,
                                        nonce=alloc_nonce, direct_broadcast=direct_broadcast)

        # Step 3: 发送（AVE 广播 或 直接广播）
        try:
            if direct_broadcast:
                result = await self._broadcast_evm_tx_direct(signed_hex, chain_id)
            else:
                result = await self._send_signed_evm_tx(chain_name, request_tx_id, signed_hex)
        except Exception:
            # 发送/模拟失败：tx 未上链，nonce 未消耗，回退到 alloc_nonce-1
            _nonce_local[chain_id] = alloc_nonce - 1
            raise
        tx_hash = result.get("txHash") or result.get("hash", "")

        # 估算 token 数量（用 AVE 返回的实际 decimals 转换，不能写死 1e18）
        token_amount = int(estimate_out) / (10 ** token_decimals) if estimate_out and estimate_out != "0" else 0.0

        logger.info(f"EVM buy success: {ca} tx={tx_hash} token_amount={token_amount} decimals={token_decimals}")
        return {
            "success": True,
            "tx": tx_hash,
            "token_amount": token_amount,
            "price": create_price,
        }

    async def _buy_solana(self, ca, chain, chain_name, in_amount_raw, usdt_addr, wallet_address, wallet_manager):
        # Step 1: 构造（失败时 _create_solana_tx 直接抛 ValueError，含 AVE 返回详情）
        tx_data = await self._create_solana_tx(
            wallet_address, in_amount_raw, usdt_addr, ca, "buy"
        )

        request_tx_id = tx_data.get("requestTxId", "")
        tx_b64 = tx_data.get("txContent", "")
        estimate_out = tx_data.get("estimateOut", "0")
        create_price = float(tx_data.get("createPrice", 0))
        token_decimals = int(tx_data.get("decimals", 6) or 6)  # Solana 代币通常 6 decimals

        # Step 2: 本地签名
        wallet = await wallet_manager.get_wallet_async(chain)
        import base64 as b64
        priv_bytes = b64.b64decode(wallet["private_key"]) if len(wallet["private_key"]) > 64 else bytes.fromhex(wallet["private_key"])
        signed_b64 = self._sign_solana_tx(tx_b64, priv_bytes)

        # Step 3: 发送
        result = await self._send_signed_solana_tx(request_tx_id, signed_b64)
        # AVE sendSignedSolanaTx 返回字段是 "hash"（不是 "txHash"）
        tx_hash = result.get("hash") or result.get("txHash") or ""

        token_amount = int(estimate_out) / (10 ** token_decimals) if estimate_out and estimate_out != "0" else 0.0
        logger.info(f"Solana buy success: {ca} tx={tx_hash}")
        return {"success": True, "tx": tx_hash, "token_amount": token_amount, "price": create_price}

    # ── 公开接口：sell ─────────────────────────────────────────────────────────
    async def sell(
        self,
        ca: str,
        chain: str,
        token_amount: float,
        wallet_address: str,
    ) -> dict:
        """
        卖出代币（链钱包，用户自签名）
        返回: {"success": bool, "tx": str, "usdt_received": float, "price": float}
        """
        from services.wallet_manager import wallet_manager

        chain_name = CHAIN_NAME_MAP.get(chain.upper(), chain.lower())
        out_token = SELL_OUT_TOKEN.get(chain_name)
        if not out_token:
            raise ValueError(f"不支持的链: {chain}")

        try:
            if chain_name == "solana":
                # SOL: 查链上真实 token 余额
                # 使用 getTokenAccountsByOwner 而非 ATA 推导，因为 AVE 可能创建非标准账户
                # （例如 Token-2022 程序的账户，ATA 地址与标准 SPL 不同）
                import httpx as _httpx_sol
                SOL_RPC = "https://api.mainnet-beta.solana.com"

                in_amount_raw = None
                try:
                    async with _httpx_sol.AsyncClient(timeout=8.0) as _c:
                        r = await _c.post(SOL_RPC, json={
                            "jsonrpc": "2.0", "id": 1,
                            "method": "getTokenAccountsByOwner",
                            "params": [
                                wallet_address,
                                {"mint": ca},
                                {"encoding": "jsonParsed"}
                            ]
                        })
                        rj = r.json()
                        accounts = rj.get("result", {}).get("value", [])
                        for acct in accounts:
                            info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                            raw_amount = info.get("tokenAmount", {}).get("amount", "")
                            if raw_amount and int(raw_amount) > 0:
                                in_amount_raw = raw_amount
                                acct_pubkey = acct.get("pubkey", "")
                                logger.info(f"SOL sell: token_acct={acct_pubkey} onchain_balance={raw_amount}")
                                break
                        if not in_amount_raw:
                            logger.warning(f"SOL sell: getTokenAccountsByOwner 余额为0或无账户，fallback DB值: {token_amount}")
                except Exception as _e:
                    logger.warning(f"SOL sell: 查链上余额失败({_e})，fallback DB值")

                if not in_amount_raw:
                    # fallback: 用 DB 值 × 1e6（token 通常 6 decimals）
                    in_amount_raw = str(int(token_amount * 1e6))

                return await self._sell_solana(ca, chain, in_amount_raw, out_token, wallet_address, wallet_manager)
            else:
                # EVM: 先查链上实际余额和代币 decimals
                # 步骤：
                # 1. RPC 查链上真实余额（raw整数）和 decimals（直接从合约读，最准确）
                # 2. 用链上余额作为 in_amount_raw（不依赖 DB 记录的 token_amount）
                # 3. 再用 getAmountOut 查 spender 地址（用链上余额换算的真实数量）

                # Step 1: 从链上 RPC 直接读 decimals 和 balanceOf（多节点确认，防单节点返回旧数据）
                token_dec = 18
                chain_raw_balance = 0
                rpc_query_success = False

                # BSC 多节点备用列表（依次尝试，直到成功）
                RPC_FALLBACKS = {
                    "bsc": [
                        "https://bsc-dataseed1.binance.org",
                        "https://bsc-dataseed2.binance.org",
                        "https://bsc-rpc.publicnode.com",
                        "https://bsc.meowrpc.com",
                    ],
                    "eth": [
                        "https://ethereum-rpc.publicnode.com",
                        "https://eth.drpc.org",
                    ],
                    "base": [
                        "https://mainnet.base.org",
                        "https://base-rpc.publicnode.com",
                    ],
                }
                nodes = RPC_FALLBACKS.get(chain_name, RPC_FALLBACKS.get("bsc", []))

                import httpx as _httpx_sell
                addr_pad = wallet_address[2:].zfill(64) if wallet_address.startswith("0x") else wallet_address.zfill(64)
                balance_results: list[int] = []   # 各节点查到的余额
                last_rpc_err = None

                for _rpc_node in nodes:
                    try:
                        async with _httpx_sell.AsyncClient(timeout=6.0) as _c:
                            r_bal = await _c.post(_rpc_node, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":ca,"data":"0x70a08231"+addr_pad},"latest"],"id":1})
                            r_dec = await _c.post(_rpc_node, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":ca,"data":"0x313ce567"},"latest"],"id":2})
                        bal_hex = r_bal.json().get("result","0x0") or "0x0"
                        dec_hex = r_dec.json().get("result","0x12") or "0x12"
                        node_bal = int(bal_hex, 16) if bal_hex and bal_hex != "0x" else 0
                        _raw_dec = int(dec_hex, 16) if dec_hex and dec_hex != "0x" else 18
                        token_dec = _raw_dec if 1 <= _raw_dec <= 36 else 18  # 防止RPC返回异常值(0/255等)
                        logger.info(f"sell RPC [{_rpc_node[:30]}]: ca={ca[:12]} balance_raw={node_bal} decimals={token_dec} balance={node_bal/(10**token_dec):.4f}")
                        balance_results.append(node_bal)
                        rpc_query_success = True
                        # 找到非零余额则停止，无需再查（优先相信有余额的节点）
                        if node_bal > 0:
                            chain_raw_balance = node_bal
                            break
                    except Exception as _e:
                        last_rpc_err = _e
                        logger.warning(f"sell RPC [{_rpc_node[:30]}] 查询失败: {_e}，尝试下一节点")

                if rpc_query_success and not chain_raw_balance:
                    # 所有成功查询的节点都返回0——多节点一致确认余额为零
                    # 这是真实情况（代币归零/转走），不是 RPC 缓存问题
                    confirmed_zero_count = len(balance_results)
                    raise ValueError(
                        f"链上代币余额为零（{confirmed_zero_count}个RPC节点一致确认 balanceOf=0），"
                        f"无可卖出数量。代币可能已归零、被转走或合约限制。"
                        f" ca={ca} decimals={token_dec}"
                    )
                elif not rpc_query_success:
                    # 所有 RPC 节点均查询异常（网络问题）
                    logger.warning(f"sell RPC 所有节点均查询失败（最后错误: {last_rpc_err}），使用DB数量兜底")

                # Step 2: 确定实际卖出数量（优先用链上余额，RPC全部失败则用DB数量兜底）
                if chain_raw_balance > 0:
                    in_amount_raw = str(chain_raw_balance)  # 直接用链上真实余额，单位已是raw
                else:
                    # RPC全部查询失败时，用DB数量 + 已知decimals兜底
                    in_amount_raw = str(int(token_amount * (10 ** token_dec)))
                logger.info(f"sell in_amount_raw={in_amount_raw} token_dec={token_dec} token_amount={token_amount} chain_raw={chain_raw_balance}")

                # Step 3: 查 spender 地址（用正确的 in_amount_raw）
                spender_from_amt = ""
                try:
                    out_token_tmp = SELL_OUT_TOKEN.get(chain_name, "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")
                    amt_data = await self.get_amount_out(chain_name, in_amount_raw, ca, out_token_tmp, "sell")
                    spender_from_amt = amt_data.get("spender", "")
                except Exception:
                    pass

                return await self._sell_evm(ca, chain, chain_name, in_amount_raw, out_token, wallet_address, wallet_manager, spender=spender_from_amt)
        except Exception as e:
            logger.error(f"Sell error: {e}")
            raise

    async def _approve_calldata_spenders(
        self,
        tx_data: dict,
        token_addr: str,
        known_spender: str,
        private_key: str,
        chain_id: int,
        in_amount_raw: str,
        wallet_address: str,
        chain_name: str,
    ) -> None:
        """从 createEvmTx 返回的 calldata 里提取所有合约地址，对未被 approve 的内层 spender 补充授权。

        根因：AVE Router（外层 spender）内部会把 transferFrom 委托给内层合约执行，
        那个内层合约也需要 token 的 allowance，但 getAmountOut 只返回外层地址。
        通过解析 calldata 的 32字节对齐地址字段，找出所有候选 spender 并按需 approve。
        """
        import re

        tx_content = tx_data.get("txContent", {})
        raw_data = tx_content.get("data", "") or ""

        # 已知不需要 approve 的地址（token 自身、BNB占位符、WBNB、USDT、USDC、已知spender）
        SKIP_ADDRS = {
            token_addr.lower(),
            wallet_address.lower(),
            known_spender.lower() if known_spender else "",
            "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",  # ETH/BNB placeholder
            "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
            "0x55d398326f99059ff775485246999027b3197955",  # USDT BEP20
            "0x10ed43c718714eb63d5aa57b78b54704e256024e",  # PancakeSwap V2 Router
            "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",  # PancakeSwap V3 Router
            "0x0000000000000000000000000000000000000000",
        }

        # 从 calldata 提取所有 32字节对齐的以太坊地址（前12字节为0，后20字节为地址）
        hex_data = raw_data.replace("0x", "").replace("0X", "")
        candidates: list[str] = []
        # 按32字节（64 hex chars）切块，跳过前4字节（函数选择器）
        chunk_start = 8  # 跳过 4 bytes selector
        while chunk_start + 64 <= len(hex_data):
            chunk = hex_data[chunk_start: chunk_start + 64]
            # 前24 hex = 12 bytes 为 0，后40 hex = 20 bytes 为地址
            if chunk[:24] == "0" * 24 and chunk[24:] != "0" * 40:
                addr = "0x" + chunk[24:]
                candidates.append(addr.lower())
            chunk_start += 64

        # 去重，过滤跳过列表
        seen: set[str] = set()
        to_approve: list[str] = []
        for addr in candidates:
            if addr in seen or addr in SKIP_ADDRS:
                continue
            seen.add(addr)
            to_approve.append(addr)

        if not to_approve:
            return False

        logger.info(f"calldata spender 补充 approve 候选: {to_approve}")

        RPC_MAP = {"bsc": "https://bsc-dataseed1.binance.org", "eth": "https://ethereum-rpc.publicnode.com", "base": "https://mainnet.base.org"}
        rpc_url = RPC_MAP.get(chain_name, "https://bsc-dataseed1.binance.org")

        import httpx as _httpx
        from eth_account import Account as _Account
        addr_pad = wallet_address[2:].zfill(64) if wallet_address.startswith("0x") else wallet_address.zfill(64)

        did_approve = False
        for inner_spender in to_approve:
            # 只对真实合约地址（有代码的）补 approve，跳过 EOA
            try:
                async with _httpx.AsyncClient(timeout=5.0) as _c:
                    r_code = await _c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_getCode","params":[inner_spender,"latest"],"id":1})
                    code = r_code.json().get("result", "0x")
                    if not code or code == "0x" or len(code) <= 4:
                        continue  # EOA，跳过

                    # 查当前 allowance
                    data_allow = "0xdd62ed3e" + addr_pad + inner_spender[2:].zfill(64)
                    r_allow = await _c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":token_addr,"data":data_allow},"latest"],"id":2})
                    current = int(r_allow.json().get("result","0x0") or "0x0", 16)
                    if current >= int(in_amount_raw):
                        logger.info(f"内层spender {inner_spender[:20]} 已有足够 allowance，跳过")
                        continue
            except Exception as e:
                logger.warning(f"检查内层spender {inner_spender[:20]} allowance 失败: {e}")
                continue

            logger.info(f"为内层spender {inner_spender} 补充 approve token={token_addr[:20]}")
            try:
                await self._ensure_erc20_approved(
                    token_addr=token_addr,
                    spender=inner_spender,
                    private_key=private_key,
                    chain_id=chain_id,
                    min_amount=int(in_amount_raw),
                )
                did_approve = True
            except Exception as e:
                logger.warning(f"内层spender {inner_spender[:20]} approve 失败（继续尝试卖出）: {e}")

        return did_approve

    async def _sell_evm(self, ca, chain, chain_name, in_amount_raw, out_token, wallet_address, wallet_manager, spender: str = ""):
        CHAIN_ID = {"bsc": 56, "eth": 1, "base": 8453}
        chain_id = CHAIN_ID.get(chain_name, 56)

        # 读取 swap gasPrice 倍数 + 广播模式
        gas_cfg = await _get_gas_cfg()
        try:
            swap_gas_multiplier = float(gas_cfg.get("gas_price_multiplier", "1.2"))
        except Exception:
            swap_gas_multiplier = 1.2
        direct_broadcast = gas_cfg.get("broadcast_mode", "ave") == "direct"

        wallet = await wallet_manager.get_wallet_async(chain)
        private_key = wallet["private_key"]

        # Step 0: 先获取 spender 地址，确保代币已授权给 router，再构造交易
        # 正确流程：getAmountOut → 获取 spender → approve（如需）→ createEvmTx
        # 如果调用方已经查过 spender（sell() 方法中的 get_amount_out），直接复用
        if not spender:
            amount_out_data = await self.get_amount_out(chain_name, in_amount_raw, ca, out_token, "sell")
            spender = amount_out_data.get("spender", "")
        if spender:
            await self._ensure_erc20_approved(
                token_addr=ca,
                spender=spender,
                private_key=private_key,
                chain_id=chain_id,
                min_amount=int(in_amount_raw),
            )

        # meme 代币价格波动大，卖出 slippage 用 5000 (50%)
        tx_data = await self._create_evm_tx(
            chain_name, wallet_address, in_amount_raw,
            ca, out_token, "sell", slippage_bps=5000
        )

        # Step 0b: 解析 calldata 中的内层合约地址，补全 approve
        # 问题根因：AVE Router(外层spender)内部会委托另一个合约做 transferFrom，
        # 那个内层合约同样需要 allowance，但 getAmountOut 只返回外层 spender。
        # 修复：从 createEvmTx 返回的 calldata 中提取所有合约地址，对不在跳过列表内的地址补 approve。
        inner_approved = False
        if tx_data:
            inner_approved = await self._approve_calldata_spenders(
                tx_data=tx_data,
                token_addr=ca,
                known_spender=spender,
                private_key=private_key,
                chain_id=chain_id,
                in_amount_raw=in_amount_raw,
                wallet_address=wallet_address,
                chain_name=chain_name,
            )
        if not tx_data:
            logger.error(f"构造卖出交易失败: ca={ca} chain={chain_name} in_amount={in_amount_raw} out_token={out_token} wallet={wallet_address}")
            raise ValueError("构造卖出交易失败")

        # 如果补充了内层 approve，重新构造 tx（旧 requestTxId 可能已过期）
        if inner_approved:
            logger.info(f"内层 approve 完成，重新构造卖出交易: ca={ca[:16]}")
            tx_data = await self._create_evm_tx(
                chain_name, wallet_address, in_amount_raw,
                ca, out_token, "sell", slippage_bps=5000
            )
            if not tx_data:
                raise ValueError("内层 approve 后重新构造卖出交易失败")

        request_tx_id = tx_data.get("requestTxId", "")
        tx_content = tx_data.get("txContent", {})
        estimate_out_raw = tx_data.get("estimateOut", "0")
        create_price = float(tx_data.get("createPrice", 0))

        # 签名（swap 用高 gasPrice 倍数）- 预分配 nonce 防并发碰撞
        # 关键：整个 batch 重试循环共用同一个 nonce。
        # 原因：3025 simulate 失败意味着 tx 从未广播上链，链上 nonce 不变，
        # 重试时重新 alloc_nonce 还是得到相同的 nonce（链未变），不如直接复用。
        # 重要：approve 若命中缓存不走 _alloc_nonce，_nonce_local 可能未更新，
        # 强制清除本地缓存让 _alloc_nonce 从 pending 重新同步，避免 nonce too low。
        _nonce_local.pop(chain_id, None)
        alloc_nonce = await self._alloc_nonce(private_key, chain_id)
        signed_hex = self._sign_evm_tx(tx_content, private_key, chain_id, gas_multiplier=swap_gas_multiplier,
                                        nonce=alloc_nonce, direct_broadcast=direct_broadcast)
        # 尝试发送，如果 3025 simulate 失败则分批卖出（Four.meme 防倾销限制）
        # 注意：只尝试找到第一个能成功的批次比例，剩余 token 由 position_monitor 下一轮继续处理
        total_usdt_received = 0.0
        last_tx_hash = ""
        sold_ratio = 1.0  # 本次实际成交的比例
        dex_fallback_used = False  # 是否走了 PancakeSwap 直接卖出
        batch_ratios = [1.0, 0.5, 0.25, 0.1, 0.05]
        last_error = None
        for i, batch_ratio in enumerate(batch_ratios):
            batch_amount = str(int(int(in_amount_raw) * batch_ratio))
            if batch_ratio < 1.0:
                logger.info(f"全量卖出失败，尝试按 {batch_ratio*100:.0f}% 分批: ca={ca[:16]} batch={batch_amount} nonce={alloc_nonce}")
                tx_data = await self._create_evm_tx(
                    chain_name, wallet_address, batch_amount,
                    ca, out_token, "sell", slippage_bps=5000
                )
                if not tx_data:
                    continue
                tx_content = tx_data.get("txContent", {})
                estimate_out_raw = tx_data.get("estimateOut", "0")
                create_price = float(tx_data.get("createPrice", 0))
                request_tx_id = tx_data.get("requestTxId", "")
                # 3025 失败意味着 tx 从未上链，nonce 未消耗，直接复用 alloc_nonce
                signed_hex = self._sign_evm_tx(tx_content, private_key, chain_id, gas_multiplier=swap_gas_multiplier,
                                                nonce=alloc_nonce, direct_broadcast=direct_broadcast)
            try:
                if direct_broadcast:
                    result = await self._broadcast_evm_tx_direct(signed_hex, chain_id)
                else:
                    result = await self._send_signed_evm_tx(chain_name, request_tx_id, signed_hex)
                last_tx_hash = result.get("txHash") or result.get("hash", "")
                out_decimals = 18
                native_received = int(estimate_out_raw) / (10 ** out_decimals) if estimate_out_raw and estimate_out_raw != "0" else 0.0
                # 卖出收到主链币（BNB/ETH），需换算成 USDT
                native_price = await _get_native_price_usd_async(chain_name)
                usdt_received = native_received * native_price
                total_usdt_received += usdt_received
                sold_ratio = batch_ratio
                logger.info(f"EVM sell success ({batch_ratio*100:.0f}%): {ca} tx={last_tx_hash} received={native_received:.6f}{chain_name.upper()} @ {native_price:.1f}USD = {usdt_received:.4f}USDT")
                break
            except ValueError as e:
                last_error = e
                if "3025" in str(e):
                    # 3025 = AVE Router 合约模拟失败，属于路由合约问题，不因批次大小而改变。
                    # 不做无意义批量重试，立即 fallback 到 DEX 直接卖出。
                    _nonce_local[chain_id] = alloc_nonce - 1
                    logger.info(f"AVE 3025 simulate 失败，立即尝试 DEX 直接卖出（跳过批量重试）: ca={ca[:16]}")
                    dex_err_msg = None
                    try:
                        pcs_result = await self._sell_via_dex_direct(
                            ca, chain_name, chain_id, in_amount_raw,
                            wallet_address, private_key, swap_gas_multiplier
                        )
                        if pcs_result:
                            native_price = await _get_native_price_usd_async(chain_name)
                            total_usdt_received = pcs_result["native_received"] * native_price
                            last_tx_hash = pcs_result["tx_hash"]
                            sold_ratio = 1.0
                            dex_fallback_used = True
                            logger.info(f"DEX 直接卖出成功: {ca} tx={last_tx_hash} usdt={total_usdt_received:.4f}")
                            break
                    except Exception as dex_err:
                        dex_err_msg = str(dex_err)
                        logger.warning(f"DEX 直接卖出也失败: {dex_err}")
                    # 把 DEX 失败原因附加到最终错误，方便前端日志显示
                    if dex_err_msg:
                        raise ValueError(f"AVE 3025 + DEX fallback 均失败: {dex_err_msg}") from last_error
                    raise
                else:
                    if i < len(batch_ratios) - 1:
                        # 非 3025 错误，继续尝试更小批次
                        # 若是 nonce 类错误，从错误消息提取链上期望 nonce 直接使用（比重查 RPC 更准确）
                        if "nonce" in str(e).lower():
                            import re as _re
                            m = _re.search(r"next nonce[:\s]+(\d+)", str(e), _re.IGNORECASE)
                            if m:
                                alloc_nonce = int(m.group(1))
                                _nonce_local[chain_id] = alloc_nonce
                                logger.info(f"nonce too low，从错误消息提取正确 nonce={alloc_nonce}，重签名继续")
                            else:
                                _nonce_local.pop(chain_id, None)
                                alloc_nonce = await self._alloc_nonce(private_key, chain_id)
                                logger.info(f"nonce too low，重新同步 nonce={alloc_nonce}，继续下一批次")
                        else:
                            logger.info(f"batch {batch_ratio*100:.0f}% 失败（非3025），继续下一批次")
                        continue
                    else:
                        # 所有批次均失败，回退 nonce
                        _nonce_local[chain_id] = alloc_nonce - 1
                        raise

        # sold_token_amount: position_monitor 用来判断是否部分成交
        # 这里按 sold_ratio 比例计算，monitor 会用 pos.token_amount * ratio 更新剩余量
        # 由于 in_amount_raw 已按正确 decimals 编码，直接从 raw 反推：
        sold_token_amount = sold_ratio  # monitor 收到后用 pos.token_amount * sold_ratio 更新

        logger.info(f"EVM sell success: {ca} tx={last_tx_hash} usdt_received={total_usdt_received} sold_ratio={sold_ratio}")
        sell_route = "PancakeSwap Direct" if dex_fallback_used else "AVE Trade"
        return {
            "success": True,
            "tx": last_tx_hash,
            "usdt_received": total_usdt_received,
            "price": create_price,
            "sold_token_amount": sold_token_amount,
            "route": sell_route,
        }

    async def _sell_via_dex_direct(
        self,
        ca: str,
        chain_name: str,
        chain_id: int,
        in_amount_raw: str,
        wallet_address: str,
        private_key: str,
        gas_multiplier: float = 1.2,
    ) -> dict | None:
        """AVE Router 3025 失败时的 fallback：直接构造 DEX swap 交易并广播上链。

        流程：
        1. Approve token 给 DEX Router（BSC 用 PancakeSwap V2）
        2. 构造 swapExactTokensForETHSupportingFeeOnTransferTokens calldata
        3. 本地签名 + 直接 eth_sendRawTransaction 广播（不经过 AVE sendSignedEvmTx）
        4. 等待收据确认

        返回 {"tx_hash": str, "native_received": float} 或 None
        """
        import asyncio as _asyncio
        import httpx as _httpx
        from eth_account import Account as _Account
        from eth_utils import to_checksum_address as _cs

        # 各链 DEX Router（支持 supportingFeeOnTransferTokens 的版本）
        DEX_ROUTER = {
            56:    "0x10ED43C718714eb63d5aA57B78B54704E256024E",  # PancakeSwap V2
            1:     "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",  # Uniswap V2
            8453:  "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",  # Aerodrome / Uniswap V2 on Base
        }
        WETH = {
            56:    "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
            1:     "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
            8453:  "0x4200000000000000000000000000000000000006",  # WETH on Base
        }
        RPC_MAP = {
            56:  "https://bsc-dataseed1.binance.org",
            1:   "https://ethereum-rpc.publicnode.com",
            8453:"https://mainnet.base.org",
        }

        router = DEX_ROUTER.get(chain_id)
        weth   = WETH.get(chain_id)
        rpc    = RPC_MAP.get(chain_id, "https://bsc-dataseed1.binance.org")
        if not router or not weth:
            raise ValueError(f"_sell_via_dex_direct: chain_id={chain_id} 暂不支持直接 DEX 卖出")

        amount_in = int(in_amount_raw)

        # Step 1: Approve DEX Router
        logger.info(f"DEX 直接卖出 approve: token={ca[:16]} spender={router[:16]} chain={chain_id}")
        await self._ensure_erc20_approved(
            token_addr=ca,
            spender=router,
            private_key=private_key,
            chain_id=chain_id,
            min_amount=amount_in,
        )

        # Step 2: getAmountsOut 确认有流动性 + 计算 amountOutMin（0滑点保护，因代币有转账税）
        def ap(addr): return addr.lower()[2:].zfill(64)
        amount_out_min = 0  # 支持转账税的代币通常需要 amountOutMin=0，让合约自行处理

        # Step 3: 构造 swapExactTokensForETHSupportingFeeOnTransferTokens calldata
        # 函数签名: 0x791ac947(uint amountIn, uint amountOutMin, address[] path, address to, uint deadline)
        deadline = 0xFFFFFFFF  # 不过期
        # ABI encode: (uint256, uint256, address[], address, uint256)
        # path = [ca, weth]
        calldata = (
            "0x791ac947"
            + hex(amount_in)[2:].zfill(64)       # amountIn
            + hex(amount_out_min)[2:].zfill(64)   # amountOutMin = 0
            + hex(160)[2:].zfill(64)              # offset to path (5 * 32 = 160)
            + ap(wallet_address)                   # to
            + hex(deadline)[2:].zfill(64)          # deadline
            + hex(2)[2:].zfill(64)                 # path.length = 2
            + ap(ca)                               # path[0] = token
            + ap(weth)                             # path[1] = WETH/WBNB
        )

        # Step 4: 签名 + 广播
        alloc_nonce = await self._alloc_nonce(private_key, chain_id)
        _, gas_price = self._fetch_nonce_and_gas(private_key, chain_id, multiplier=gas_multiplier)
        tx = {
            "to":       _cs(router),
            "data":     calldata,
            "value":    0,
            "gas":      350_000,
            "gasPrice": gas_price,
            "chainId":  chain_id,
            "nonce":    alloc_nonce,
        }
        signed = _Account.sign_transaction(tx, private_key)
        raw_hex = signed.rawTransaction.hex()
        logger.info(f"DEX 直接广播: router={router[:16]} nonce={alloc_nonce} gas={gas_price/1e9:.2f}Gwei")

        async with _httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_sendRawTransaction","params":[raw_hex],"id":1})
            rj = r.json()
            if "error" in rj:
                _nonce_local[chain_id] = alloc_nonce - 1
                raise ValueError(f"DEX eth_sendRawTransaction 失败: {rj['error']}")
            tx_hash = rj.get("result", "")
            logger.info(f"DEX 交易已广播: {tx_hash}")

        # Step 5: 等待收据，最多 60 秒
        bnb_received = 0.0
        for _ in range(30):
            await _asyncio.sleep(2)
            async with _httpx.AsyncClient(timeout=8.0) as c:
                r2 = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[tx_hash],"id":2})
                receipt = r2.json().get("result")
                if receipt:
                    if receipt.get("status") == "0x1":
                        # 从 receipt 的 logs 里找 Withdrawal(WETH) 或直接看 ETH 转账
                        # 简化：用 getAmountsOut 估算收到的 BNB
                        try:
                            r3 = await c.post(rpc, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":router,"data":"0xd06ca61f"+hex(amount_in)[2:].zfill(64)+hex(64)[2:].zfill(64)+hex(2)[2:].zfill(64)+ap(ca)+ap(weth)},"latest"],"id":3})
                            raw3 = r3.json().get("result","")
                            if raw3 and len(raw3) >= 2+4*64:
                                bnb_received = int(raw3[2+3*64:2+4*64], 16) / 1e18
                        except Exception:
                            bnb_received = 0.0
                        logger.info(f"DEX 交易确认: {tx_hash} bnb_received~{bnb_received:.8f}")
                        return {"tx_hash": tx_hash, "native_received": bnb_received}
                    else:
                        _nonce_local[chain_id] = alloc_nonce - 1
                        raise ValueError(f"DEX 交易 revert: {tx_hash}")

        logger.warning(f"DEX 交易 60s 未确认，仍视为成功: {tx_hash}")
        return {"tx_hash": tx_hash, "native_received": bnb_received}

    async def _sell_solana(self, ca, chain, in_amount_raw, out_token, wallet_address, wallet_manager):
        tx_data = await self._create_solana_tx(
            wallet_address, in_amount_raw, ca, out_token, "sell"
        )
        if not tx_data:
            raise ValueError("构造 Solana 卖出交易失败")

        request_tx_id = tx_data.get("requestTxId", "")
        tx_b64 = tx_data.get("txContent", "")
        estimate_out = tx_data.get("estimateOut", "0")
        create_price = float(tx_data.get("createPrice", 0))

        wallet = await wallet_manager.get_wallet_async(chain)
        import base64 as b64
        priv_bytes = b64.b64decode(wallet["private_key"]) if len(wallet["private_key"]) > 64 else bytes.fromhex(wallet["private_key"])
        signed_b64 = self._sign_solana_tx(tx_b64, priv_bytes)
        result = await self._send_signed_solana_tx(request_tx_id, signed_b64)
        tx_hash = result.get("hash") or result.get("txHash") or ""

        # 卖出收到 SOL，按 9 decimals
        sol_received = int(estimate_out) / 1e9 if estimate_out and estimate_out != "0" else 0.0
        return {"success": True, "tx": tx_hash, "usdt_received": sol_received, "price": create_price}

    # ── 查价格（用询价接口估算） ────────────────────────────────────────────────
    async def get_price(self, ca: str, chain: str) -> float:
        """通过询价 1 SOL/USDT 能买多少 token 反推价格（USD）。带20秒缓存，减少API频率。"""
        chain_name = CHAIN_NAME_MAP.get(chain.upper(), chain.lower())
        cache_key = f"{chain_name}:{ca}"
        cached = _token_price_cache.get(cache_key)
        if cached and _time.time() - cached[1] < _TOKEN_PRICE_TTL:
            return cached[0]

        in_token = BUY_IN_TOKEN.get(chain_name)
        if not in_token:
            return 0.0
        try:
            decimals = BUY_IN_DECIMALS.get(chain_name, 18)
            in_amount_raw = str(10 ** decimals)
            data = await self.get_amount_out(chain_name, in_amount_raw, in_token, ca, "buy")
            estimate_out = data.get("estimateOut", "0")
            token_decimals = int(data.get("decimals", "18"))
            if estimate_out and estimate_out != "0":
                token_per_unit = int(estimate_out) / (10 ** token_decimals)
                if token_per_unit <= 0:
                    _token_price_cache[cache_key] = (0.0, _time.time())
                    return 0.0
                unit_usd = 150.0 if chain_name == "solana" else 1.0
                price = unit_usd / token_per_unit
                _token_price_cache[cache_key] = (price, _time.time())
                return price
        except Exception as e:
            logger.debug(f"get_price error {ca}: {e}")
        return 0.0

    async def get_wallet_balance(self, wallet_address: str, chain: str) -> dict:
        """余额查询（已改为 RPC 直查，此方法保留兼容）"""
        return {}


# 单例
ave_client = AveClient()
