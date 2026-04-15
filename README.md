# Holdo.AI Meme Trader

> **全自动 Meme 币交易机器人** — 从信号接收到链上成交，端到端自动化，延迟 < 1s。

---

## 这是什么

Meme 币市场的核心逻辑只有一条：**谁快谁赚钱**。

人工看盘、手动下单已经落后了。真正的玩家用机器人。

Holdo.AI Meme Trader 是一套**完整的自动化链上交易系统**：
从 WebSocket 接收代币合约地址信号（CA），毫秒级完成多维度风控过滤，自动在链上买入，实时监控涨跌，触发止盈/止损后自动卖出——全程无需人工干预。

支持 **Solana、BSC、Ethereum、Base** 四条链，一套系统统一管理。

---

## 核心能力

### 信号 → 买入，< 1 秒

```
CA 信号到达 → 过滤引擎 → 链上签名广播 → 持仓建立
     ↑
WebSocket 实时推送，指数退避自动重连，断线不丢单
```

- 收到 CA 信号后立即触发过滤
- 通过所有条件后异步发起链上交易
- 内存级别去重锁，同一 CA 不会重复买入
- 买入失败进入 5 分钟冷却黑名单，避免反复踩坑

### 多维度智能过滤

不是所有 CA 都值得买。过滤引擎支持以下条件（全部可配置阈值）：

| 维度 | 说明 |
|------|------|
| 发送者胜率 | 喊单人历史胜率、总喊单数、群组胜率 |
| 发送者最佳倍数 | 历史最高收益倍数门槛 |
| 当前涨幅（cxrzf）| 已涨多少倍，过高的不追 |
| 市值范围 | 最小/最大市值过滤 |
| 持仓集中度 | Top 10 持仓占比，防止大户砸盘 |
| 安全评分 | Honeypot 检测、风险评分 |
| 新人策略 | 无历史记录的发币人：跳过 / 半仓 / 正常买 |
| 跟单模式 | 指定钱包地址，只跟特定高手 |

支持**半仓策略**：条件弱的信号自动减半买入，控制风险。

### 持仓全自动管理

买入后不用盯盘，系统每 10 秒轮询一次价格：

- **止盈**：涨到目标倍数自动卖出
- **止损**：跌破阈值自动止损
- **超时退出**：超过最长持仓时间自动清仓
- **归零检测**：链上余额为 0（被 rug）自动关仓，不再挂单
- **Token-2022 兼容**：完整支持 pump.fun 新代币（SPL + Token-2022 双协议）

### 链上执行质量

- **买入前余额预检**：USDT 不足直接跳过，不发无效 TX 浪费 Gas
- **Receipt 轮询确认**：直播广播模式下等待链上确认，TX revert 不建仓
- **Nonce 回退机制**：交易失败后正确回退 nonce，不卡链
- **SOL 链真实余额查询**：卖出前查链上 ATA 实际余额，避免 `Insufficient token balance`
- **多路由支持**：AVE Trade、PancakeSwap Direct 等多条路由可选

### 多钱包管理

- BIP39 助记词导入，AES 加密存储
- 支持 SOL / BSC / ETH / Base 多链地址统一管理
- 实时余额查询（原生币 + USDT/USDC + 持仓估值）
- 残留代币一键清扫
- Demo 钱包供体验测试

### 数据分析

- 胜率、盈亏比、平均盈亏、最大连胜/连败
- 按小时/日/周/月/季/年多时段统计
- **CA 战绩排行榜**：哪些代币最能赚？多维排序（总盈亏/胜率/最大收益/交易次数）
- 信号统计：接收量、通过率、买入转化率
- 发送者本地信誉积累：独立于外部数据源追踪喊单人胜率
- 每笔交易完整记录（入场价、出场价、出局原因、Gas 费、TX Hash）

### 实时界面

React + WebSocket，所有事件实时推送：

- 买入/卖出执行日志（含 TX Hash、Gas 费）
- CA 信号接收流（含热度、市值、发送者信息）
- 持仓实时盈亏
- 连接状态监控

---

## 技术架构

```
┌─────────────────────────────────────────────────┐
│                  React Frontend                  │
│         (Vite + Tailwind + Recharts)             │
└──────────────────────┬──────────────────────────┘
                       │ REST API + WebSocket
┌──────────────────────▼──────────────────────────┐
│              FastAPI Backend (async)             │
│  ┌──────────┐  ┌────────────┐  ┌─────────────┐ │
│  │CA Listener│  │Trade Engine│  │Position Mon.│ │
│  │(WebSocket)│  │(Filter+Buy)│  │(TP/SL loop) │ │
│  └──────────┘  └────────────┘  └─────────────┘ │
│  ┌──────────┐  ┌────────────┐  ┌─────────────┐ │
│  │AVE Client │  │Wallet Mgr. │  │ Broadcaster │ │
│  │(4 chains) │  │(AES encr.) │  │ (WS push)   │ │
│  └──────────┘  └────────────┘  └─────────────┘ │
│              SQLAlchemy (SQLite / async)          │
└─────────────────────────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
      Solana         BSC          ETH/Base
```

**后端**：Python 3.10+、FastAPI、SQLAlchemy async、httpx
**前端**：React 18、Vite、Tailwind CSS、Recharts
**区块链**：AVE Trading API、eth-account（EVM 签名）、solders（Solana 签名）
**存储**：SQLite（全异步 aiosqlite）

---

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+

### 安装

```bash
git clone <repo-url>
cd meme-trader-main

# 后端依赖
pip install -r requirements.txt

# 前端依赖
cd frontend && npm install && cd ..
```

### 配置

```bash
cp .env.example .env
```

| 变量 | 说明 |
|------|------|
| `AVE_API_KEY` | AVE 交易 API 密钥 |
| `AVE_BASE_URL` | AVE API 地址 |
| `WALLET_MASTER_PASSWORD` | 钱包加密主密码 |
| `CA_WS_URL` | 信号源 WebSocket 地址 |
| `BACKEND_PORT` | 后端端口（默认 8000）|

### 启动

```bash
# Linux/Mac
./start.sh

# Windows
start.bat
```

访问 `http://localhost:5173` 进入管理界面。
API 文档：`http://localhost:8000/docs`

---

## 项目结构

```
meme-trader-main/
├── backend/
│   ├── main.py                  # FastAPI 入口
│   ├── routers/                 # 40+ API 路由
│   └── services/
│       ├── trade_engine.py      # 过滤 + 买入决策
│       ├── position_monitor.py  # 止盈/止损自动化
│       ├── ave_client.py        # 4 链交易客户端
│       ├── ca_listener.py       # 信号 WebSocket 监听
│       ├── wallet_manager.py    # 加密钱包管理
│       └── broadcaster.py       # 实时日志推送
├── frontend/
│   └── src/
│       ├── App.jsx              # 主界面
│       └── components/          # 各功能面板
├── requirements.txt
├── .env.example
└── start.sh / start.bat
```

---

## 更新日志

详见 [CHANGELOG.md](./CHANGELOG.md)

---

## 免责声明

本项目仅供学习研究和黑客松展示。加密货币交易有极高风险，请勿用真实资金盲目跟单。
