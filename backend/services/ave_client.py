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
# Solana 用 SOL（文档: SOL传参"sol"），EVM 用 USDT
BUY_IN_TOKEN = {
    "bsc":     "0x55d398326f99059fF775485246999027B3197955",  # USDT BEP20
    "eth":     "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT ERC20
    "solana":  "sol",   # AVE 文档: SOL 传 "sol"
    "base":    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC on Base
}

# 卖出时 outToken（收到的代币）
# Four.meme 内盘代币只支持卖出为主链币，所以统一用主链币地址
# 主链币用 0xeeee...eeee 表示（EVM 惯例）
SELL_OUT_TOKEN = {
    "bsc":     "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",  # BNB
    "eth":     "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",  # ETH
    "solana":  "sol",
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
    NATIVE_TO_USDT = {
        "bsc":  ("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                 "0x55d398326f99059fF775485246999027B3197955", 18, 18),  # BNB→USDT(18dec)
        "eth":  ("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                 "0xdAC17F958D2ee523a2206206994597C13D831ec7", 18, 6),   # ETH→USDT(6dec)
        "base": ("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                 "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 18, 6),   # ETH→USDC(6dec)
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
    """从 DB 读取 GAS 配置，返回 {gas_price_multiplier, approve_gas_multiplier}"""
    try:
        from database import AsyncSessionLocal, ConfigModel
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ConfigModel).where(
                ConfigModel.key.in_([
                    "gas_price_multiplier",
                    "approve_gas_price_gwei",
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
            logger.error(f"createEvmTx empty data: {body}")
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
        return resp.json().get("data", {})

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
    ) -> str:
        """用 eth_account 签名 EVM 交易，返回签名后的 hex。

        AVE txContent 只包含 data/to/value，不含 gasLimit/gasPrice/nonce。
        - data: 不带 0x 前缀的 hex，需要加上
        - value: 十进制字符串 "0" 或 "1000000..."
        - nonce: 若 >= 0 则直接使用（由 _alloc_nonce 预先分配），否则临时查链上
        - gas_multiplier: gasPrice 倍数（可配置，影响打包速度）
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
            nonce, gas_price = self._fetch_nonce_and_gas(private_key, chain_id, multiplier=gas_multiplier)
        else:
            _, gas_price = self._fetch_nonce_and_gas(private_key, chain_id, multiplier=gas_multiplier)

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
        return signed.raw_transaction.hex()

    def _fetch_nonce_and_gas(
        self,
        private_key: str,
        chain_id: int,
        multiplier: float = 1.2,
    ) -> tuple[int, int]:
        """同步查询链上 nonce 和 gasPrice（在签名前调用，无需 async）

        multiplier: gasPrice 倍数（1.0=不加速, 1.2=20%溢价, 2.0=加速）
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
        # BSC 网络实际 gas 有时低至 0.05 Gwei，但 AVE 模拟要求至少 1 Gwei
        MIN_GAS_PRICE = {
            56:    1_000_000_000,   # BSC  最低 1 Gwei（AVE 模拟要求）
            1:     1_000_000_000,   # ETH  最低 1 Gwei
            8453:     50_000_000,   # Base 最低 0.05 Gwei
        }
        # 默认 gasPrice（RPC 查询失败时使用；正常情况跟随 RPC 返回值）
        DEFAULT_GAS_PRICE = {
            56:    1_000_000_000,   # BSC  1 Gwei
            1:    10_000_000_000,   # ETH  10 Gwei
            8453:    100_000_000,   # Base 0.1 Gwei
        }

        rpc_url = RPC.get(chain_id, "https://bsc-dataseed1.binance.org")
        min_gp  = MIN_GAS_PRICE.get(chain_id, 1_000_000_000)
        def_gp  = DEFAULT_GAS_PRICE.get(chain_id, 3_000_000_000)

        try:
            with _httpx.Client(timeout=8.0) as c:
                r1 = c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_getTransactionCount","params":[address,"pending"],"id":1})
                nonce = int(r1.json()["result"], 16)
                r2 = c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_gasPrice","params":[],"id":2})
                gas_price = int(r2.json()["result"], 16)
                # 低于最低值时用默认值
                if gas_price < min_gp:
                    gas_price = def_gp
                # 乘以可配置倍数（跟随网络行情 + 溢价）
                gas_price = int(gas_price * multiplier)
                # 乘完后再次保证不低于链最低值
                if gas_price < min_gp:
                    gas_price = min_gp
                logger.info(f"RPC nonce={nonce} gasPrice={gas_price} ({gas_price/1e9:.2f}Gwei) chain={chain_id} addr={address[:10]}...")
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
            # 异步查链上 pending nonce（不阻塞事件循环，Lock 才能真正起效）
            try:
                async with _httpx.AsyncClient(timeout=8.0) as c:
                    r = await c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_getTransactionCount","params":[address,"pending"],"id":1})
                    nonce_chain = int(r.json()["result"], 16)
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
        """用 solders 签名 Solana 交易，返回签名后 Base64

        AVE 返回的 txContent 是完整的 VersionedTransaction（含空签名占位符）。
        用 solders 反序列化后重新签名，再序列化回 Base64。
        """
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction
        import base64 as b64
        kp = Keypair.from_bytes(private_key_bytes)
        raw = b64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(raw)
        signed_tx = VersionedTransaction(tx.message, [kp])
        return b64.b64encode(bytes(signed_tx)).decode()

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
        amount_usdt: EVM 链 = USDT 数量；Solana = SOL 数量
        返回: {"success": bool, "tx": str, "token_amount": float, "price": float}
        """
        from services.wallet_manager import wallet_manager

        chain_name = CHAIN_NAME_MAP.get(chain.upper(), chain.lower())
        in_token = BUY_IN_TOKEN.get(chain_name)
        if not in_token:
            raise ValueError(f"不支持的链: {chain}")

        # inToken → 最小精度
        decimals = BUY_IN_DECIMALS.get(chain_name, 18)
        in_amount_raw = str(int(amount_usdt * (10 ** decimals)))

        try:
            if chain_name == "solana":
                return await self._buy_solana(
                    ca, chain, chain_name, in_amount_raw, in_token,
                    wallet_address, wallet_manager
                )
            else:
                return await self._buy_evm(
                    ca, chain, chain_name, in_amount_raw, in_token,
                    wallet_address, wallet_manager
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
        raw_hex = "0x" + signed.raw_transaction.hex()

        with _httpx.Client(timeout=30.0) as c:
            r2 = c.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_sendRawTransaction","params":[raw_hex],"id":2})
            r2j = r2.json()
            if "error" in r2j:
                raise ValueError(f"approve tx failed: {r2j['error']}")
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

    async def _buy_evm(self, ca, chain, chain_name, in_amount_raw, usdt_addr, wallet_address, wallet_manager):
        CHAIN_ID = {"bsc": 56, "eth": 1, "base": 8453}
        chain_id = CHAIN_ID.get(chain_name, 56)

        # 读取 swap gasPrice 倍数（默认 1.2，可在配置页面调整）
        gas_cfg = await _get_gas_cfg()
        try:
            swap_gas_multiplier = float(gas_cfg.get("gas_price_multiplier", "1.2"))
        except Exception:
            swap_gas_multiplier = 1.2

        # Step 0: getAmountOut 预检——验证代币地址有效且有流动性
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

        # Step 1: 确保 USDT 已授权给 AVE router
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
        # token 精度：优先用 createEvmTx 返回的 decimals，默认18
        token_decimals = int(tx_data.get("decimals", 18) or 18)
        logger.info(f"createEvmTx txContent keys={list(tx_content.keys())} gas_in_content={tx_content.get('gas') or tx_content.get('gasLimit')}")

        # Step 2: 本地签名（swap 用高 gasPrice 倍数，加速打包）
        alloc_nonce = await self._alloc_nonce(private_key, chain_id)
        signed_hex = self._sign_evm_tx(tx_content, private_key, chain_id, gas_multiplier=swap_gas_multiplier, nonce=alloc_nonce)

        # Step 3: 发送
        try:
            result = await self._send_signed_evm_tx(chain_name, request_tx_id, signed_hex)
        except Exception:
            # 发送/模拟失败：tx 未上链，nonce 未消耗，回退到 alloc_nonce-1
            # 下次 _alloc_nonce 查链上若仍是 old_nonce，会直接用链上值而非继续递增
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
        # Step 1: 构造
        tx_data = await self._create_solana_tx(
            wallet_address, in_amount_raw, usdt_addr, ca, "buy"
        )
        if not tx_data:
            raise ValueError("构造 Solana 交易失败")

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
        tx_hash = result.get("txHash", "")

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
                # SOL: token_amount 单位 = 代币最小精度，暂按 6 位
                in_amount_raw = str(int(token_amount * 1e6))
                return await self._sell_solana(ca, chain, in_amount_raw, out_token, wallet_address, wallet_manager)
            else:
                # EVM: 先查链上实际余额和代币 decimals
                # 步骤：
                # 1. RPC 查链上真实余额（raw整数）和 decimals（直接从合约读，最准确）
                # 2. 用链上余额作为 in_amount_raw（不依赖 DB 记录的 token_amount）
                # 3. 再用 getAmountOut 查 spender 地址（用链上余额换算的真实数量）

                # Step 1: 从链上 RPC 直接读 decimals 和 balanceOf（最可靠）
                token_dec = 18
                chain_raw_balance = 0
                try:
                    import httpx as _httpx_sell
                    RPC_MAP_SELL = {
                        "bsc": "https://bsc-dataseed1.binance.org",
                        "eth": "https://ethereum-rpc.publicnode.com",
                        "base": "https://mainnet.base.org",
                    }
                    rpc_sell = RPC_MAP_SELL.get(chain_name, "https://bsc-dataseed1.binance.org")
                    addr_pad = wallet_address[2:].zfill(64) if wallet_address.startswith("0x") else wallet_address.zfill(64)
                    async with _httpx_sell.AsyncClient(timeout=8.0) as _c:
                        # 同时查 balanceOf 和 decimals
                        r_bal = await _c.post(rpc_sell, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":ca,"data":"0x70a08231"+addr_pad},"latest"],"id":1})
                        r_dec = await _c.post(rpc_sell, json={"jsonrpc":"2.0","method":"eth_call","params":[{"to":ca,"data":"0x313ce567"},"latest"],"id":2})
                    bal_hex = r_bal.json().get("result","0x0") or "0x0"
                    dec_hex = r_dec.json().get("result","0x12") or "0x12"
                    chain_raw_balance = int(bal_hex, 16) if bal_hex and bal_hex != "0x" else 0
                    token_dec = int(dec_hex, 16) if dec_hex and dec_hex != "0x" else 18
                    logger.info(f"sell RPC: ca={ca[:12]} balance_raw={chain_raw_balance} decimals={token_dec} balance={chain_raw_balance/(10**token_dec):.4f}")
                except Exception as _e:
                    logger.warning(f"sell RPC query failed: {_e}, using DB token_amount with dec=18")

                # Step 2: 确定实际卖出数量（优先用链上余额，RPC失败则用DB数量）
                if chain_raw_balance > 0:
                    in_amount_raw = str(chain_raw_balance)  # 直接用链上真实余额，单位已是raw
                else:
                    # RPC查询失败时，用DB数量 + 已知decimals兜底
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

    async def _sell_evm(self, ca, chain, chain_name, in_amount_raw, out_token, wallet_address, wallet_manager, spender: str = ""):
        CHAIN_ID = {"bsc": 56, "eth": 1, "base": 8453}
        chain_id = CHAIN_ID.get(chain_name, 56)

        # 读取 swap gasPrice 倍数
        gas_cfg = await _get_gas_cfg()
        try:
            swap_gas_multiplier = float(gas_cfg.get("gas_price_multiplier", "1.2"))
        except Exception:
            swap_gas_multiplier = 1.2

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
        if not tx_data:
            logger.error(f"构造卖出交易失败: ca={ca} chain={chain_name} in_amount={in_amount_raw} out_token={out_token} wallet={wallet_address}")
            raise ValueError("构造卖出交易失败")

        request_tx_id = tx_data.get("requestTxId", "")
        tx_content = tx_data.get("txContent", {})
        estimate_out_raw = tx_data.get("estimateOut", "0")
        create_price = float(tx_data.get("createPrice", 0))

        # 签名（swap 用高 gasPrice 倍数）- 预分配 nonce 防并发碰撞
        alloc_nonce = await self._alloc_nonce(private_key, chain_id)
        signed_hex = self._sign_evm_tx(tx_content, private_key, chain_id, gas_multiplier=swap_gas_multiplier, nonce=alloc_nonce)
        # 尝试发送，如果 3025 simulate 失败则分批卖出（Four.meme 防倾销限制）
        # 注意：只尝试找到第一个能成功的批次比例，剩余 token 由 position_monitor 下一轮继续处理
        total_usdt_received = 0.0
        last_tx_hash = ""
        sold_ratio = 1.0  # 本次实际成交的比例
        batch_ratios = [1.0, 0.5, 0.25, 0.1, 0.05]
        last_error = None
        for i, batch_ratio in enumerate(batch_ratios):
            batch_amount = str(int(int(in_amount_raw) * batch_ratio))
            if batch_ratio < 1.0:
                logger.info(f"全量卖出失败，尝试按 {batch_ratio*100:.0f}% 分批: ca={ca[:16]} batch={batch_amount}")
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
                # 重试批量时重新分配新 nonce（之前的 nonce 已用于失败的全量尝试，链上 pending 会增加）
                alloc_nonce = await self._alloc_nonce(private_key, chain_id)
                signed_hex = self._sign_evm_tx(tx_content, private_key, chain_id, gas_multiplier=swap_gas_multiplier, nonce=alloc_nonce)
            try:
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
                # 发送/模拟失败：tx 未上链，nonce 未消耗，回退到 alloc_nonce-1
                _nonce_local[chain_id] = alloc_nonce - 1
                if "3025" in str(e) and i < len(batch_ratios) - 1:
                    continue
                raise

        # sold_token_amount: position_monitor 用来判断是否部分成交
        # 这里按 sold_ratio 比例计算，monitor 会用 pos.token_amount * ratio 更新剩余量
        # 由于 in_amount_raw 已按正确 decimals 编码，直接从 raw 反推：
        sold_token_amount = int(in_amount_raw) * sold_ratio / int(in_amount_raw) * (int(in_amount_raw) / (10 ** 18))
        # 简化：monitor 只关心是否 < pos.token_amount，这里直接返回 sold_ratio * pos_token
        # 但这里没有 pos.token_amount，用 in_amount_raw 原值的比例代替
        # monitor 的判断是 remaining = pos.token_amount - sold_token_amount > threshold
        # 所以只需确保全量卖出时 sold_token_amount == pos.token_amount
        # 此处无法完整还原，返回 sold_ratio 标志位，monitor 侧处理
        sold_token_amount = sold_ratio  # monitor 收到后用 pos.token_amount * sold_ratio 更新

        logger.info(f"EVM sell success: {ca} tx={last_tx_hash} usdt_received={total_usdt_received} sold_ratio={sold_ratio}")
        return {
            "success": True,
            "tx": last_tx_hash,
            "usdt_received": total_usdt_received,
            "price": create_price,
            "sold_token_amount": sold_token_amount,
        }

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
        tx_hash = result.get("txHash", "")

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
