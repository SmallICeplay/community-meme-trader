"""
链检测器：根据 CA 格式或消息中的 chain 字段判断链类型
优先级：SOL > BSC > ETH > XLAYER
"""
import re

CHAIN_PRIORITY = ["SOL", "BSC", "ETH", "XLAYER"]

# SOL: Base58，32-44 字符，无 0x 前缀
_SOL_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
# EVM: 0x + 40 hex
_EVM_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')


def detect_chain_from_ca(ca: str) -> str | None:
    """仅根据 CA 格式推断链（无法区分 BSC/ETH/XLAYER）"""
    if _SOL_RE.match(ca):
        return "SOL"
    if _EVM_RE.match(ca):
        return "BSC"  # 默认 EVM 优先 BSC
    return None


def detect_chain(message: dict) -> str | None:
    """
    从 WS 消息中提取链信息。
    消息格式未知，尝试常见字段名：chain, chainId, network, blockchain
    如果消息里有明确的链字段，用它；否则从 CA 推断。
    """
    ca = message.get("ca") or message.get("address") or message.get("token") or ""

    # 尝试从消息字段获取链信息
    raw_chain = (
        message.get("chain")
        or message.get("chainName")
        or message.get("network")
        or message.get("blockchain")
        or ""
    ).upper()

    # 标准化常见别名
    chain_map = {
        "SOLANA": "SOL",
        "SOL": "SOL",
        "BNB": "BSC",
        "BSC": "BSC",
        "BINANCE": "BSC",
        "ETH": "ETH",
        "ETHEREUM": "ETH",
        "XLAYER": "XLAYER",
        "X LAYER": "XLAYER",
    }
    if raw_chain in chain_map:
        return chain_map[raw_chain]

    # 检查 chainId 数字
    chain_id = message.get("chainId") or message.get("chain_id")
    if chain_id is not None:
        chain_id = int(chain_id)
        chain_id_map = {
            1: "ETH",
            56: "BSC",
            196: "XLAYER",
        }
        if chain_id in chain_id_map:
            return chain_id_map[chain_id]

    # fallback：从 CA 格式推断
    return detect_chain_from_ca(ca)


def is_enabled_chain(chain: str, enabled_chains: list[str]) -> bool:
    return chain in enabled_chains
