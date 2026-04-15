# Holdo.AI Meme Trader

> **社区信号驱动的全自动 Meme 币交易机器人** — 聚合多个 Alpha 社群的实时喊单信号，用算法过滤噪音，毫秒级上链执行。

---

## 核心思路：让社区帮你选币，让机器帮你下单

Meme 币的 Alpha 藏在 Telegram/微信 社群里。每天无数个群在喊单，但：

- **99% 都是噪音** — 庄家自导自演、新人乱喊、追高砸盘
- **真正的信号转瞬即逝** — 好币被多个顶级 Alpha 群同时喊到时，留给普通人的时间窗口只有几秒

Holdo.AI Meme Trader 解决的就是这个问题：

**接入多个 Alpha 社区的实时信号流 → 算法识别高质量信号 → 自动上链交易 → 自动止盈止损**

人负责选社区，机器负责其他一切。

---

## 社区信号智能分析

系统接收的每条 CA 信号都携带完整的社区热度数据，过滤引擎基于这些数据做决策：

### 社区热度维度

| 字段 | 含义 | 用途 |
|------|------|------|
| `qwfc` 全网发送次数 | 这个 CA 在所有接入社区里被喊了多少次 | 热度门槛，低于阈值不买 |
| `bqfc` 本群发送次数 | 在当前信号源群里的提及次数 | 群内共识强度 |
| `fgq` 覆盖群数量 | 同时有多少个独立社群在喊这个 CA | **关键指标：跨社区共鸣** |
| `grcxcs` 个人查询次数 | 有多少人主动查过这个币 | 真实用户关注度 |
| `cxrzf` 当前涨幅 | 信号发出时币已经涨了多少倍 | 防追高，超过上限跳过 |

**核心逻辑**：一个 CA 同时被多个独立社群喊到（`fgq` 高），且喊单人历史胜率高，才是真正的 Alpha 信号。

### 喊单人信誉体系

系统为每个喊单人建立本地信誉档案，独立追踪其历史表现：

| 维度 | 说明 |
|------|------|
| `sender_win_rate` | 全局历史胜率 |
| `sender_group_win_rate` | 在本群的胜率（群内更精准的参考） |
| `sender_total_tokens` | 总喊单数（样本量） |
| `sender_best_multiple` | 历史最高收益倍数 |

新人（无历史记录）可配置为：跳过 / 半仓试水 / 正常买入。

### 过滤决策流

```
收到 CA 信号
      ↓
喊单人检查 → 胜率 / 喊单量 / 历史最高倍数不达标？跳过
      ↓
防追高检查 → 已涨超过 N 倍？跳过
      ↓
社区热度检查 → 全网次数 / 覆盖群数 / 查询次数不足？跳过
      ↓
市场数据检查 → 市值 / 5分钟涨幅 / 1小时买量不达标？跳过
      ↓
安全检查 → 蜜罐 / 可增发 / 风险评分过高 / 持仓过集中？跳过
      ↓
全部通过 → 自动买入（全仓 or 半仓）
```

所有阈值均可在配置面板实时调整，无需重启。

---

## 从信号到成交，全程自动化

```
社区 Alpha 群（多源）
       ↓ WebSocket 实时推送
CA 监听器（指数退避自动重连）
       ↓ 毫秒级触发
过滤引擎（多维度风控）
       ↓ 通过
AVE 链上执行（Solana / BSC / ETH / Base）
       ↓ 建仓
持仓监控（每 10 秒轮询）
       ↓
止盈 / 止损 / 超时 / 归零检测
       ↓
自动卖出 → 记录盈亏
```

**延迟**：信号到达→链上广播 < 1 秒

---

## 链上执行质量

买入不只是发交易，还要保证每笔都真实有效：

- **买入前余额预检** — USDT 不足直接跳过，不浪费 Gas
- **Receipt 轮询确认** — 等待链上 confirm，TX revert 不建仓，不产生假持仓
- **Nonce 回退** — 交易失败正确释放 nonce，不卡链
- **Token-2022 兼容** — 完整支持 pump.fun 新代币（SPL + Token-2022 双协议）
- **SOL 真实余额查询** — 卖出前查链上 ATA 实际余额，不依赖 DB 估算值
- **同 CA 去重锁** — 内存级别防止同一 CA 并发重复买入

---

## 持仓管理

| 退出条件 | 说明 |
|----------|------|
| 止盈 | 涨到目标倍数自动卖出 |
| 止损 | 跌破阈值自动清仓 |
| 超时 | 超过最长持仓时间强制退出 |
| 归零检测 | 链上余额为 0（rug）自动关仓 |

所有参数可配置，全程无需盯盘。

---

## 数据分析

交易结束不是终点，系统持续积累数据帮你优化策略：

- **CA 战绩排行榜** — 哪些 CA 最能赚？多维排序（总盈亏/胜率/最大收益），可按时段筛选
- **信号漏斗统计** — 接收量 → 通过率 → 买入转化率，找出过滤瓶颈
- **喊单人信誉积累** — 本地追踪每个喊单人的真实胜率，越用越准
- **完整交易记录** — 每笔含入场价、出场价、出局原因、Gas 费、TX Hash

---

## 技术架构

```
┌─────────────────────────────────────────────────┐
│              React 18 Frontend                   │
│      (Vite + Tailwind CSS + Recharts)            │
└──────────────────────┬──────────────────────────┘
                       │ REST API + WebSocket
┌──────────────────────▼──────────────────────────┐
│           FastAPI Backend (全异步)               │
│                                                  │
│  CA Listener  →  Trade Engine  →  AVE Client     │
│  (WS 多源)       (过滤+决策)      (4链执行)      │
│                                                  │
│  Position Monitor  ←→  Broadcaster               │
│  (TP/SL/超时/归零)      (WS 实时推送)            │
│                                                  │
│         SQLAlchemy async (SQLite)                │
└─────────────────────────────────────────────────┘
              ↓              ↓         ↓        ↓
           Solana           BSC       ETH      Base
```

| 层 | 技术 |
|----|------|
| 后端 | Python 3.10+, FastAPI, SQLAlchemy async |
| 前端 | React 18, Vite, Tailwind CSS, Recharts |
| 链上 | AVE Trading API, eth-account (EVM), solders (Solana) |
| 实时 | WebSocket 双向（信号接收 + 前端推送） |

---

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+

### 安装

```bash
git clone <repo-url>
cd meme-trader-main

pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### 配置

```bash
cp .env.example .env
```

| 变量 | 说明 |
|------|------|
| `AVE_API_KEY` | AVE 交易 API 密钥 |
| `CA_WS_URL` | 社区信号源 WebSocket 地址 |
| `WALLET_MASTER_PASSWORD` | 钱包加密主密码 |
| `BACKEND_PORT` | 后端端口（默认 8000）|

### 启动

```bash
./start.sh      # Linux/Mac
start.bat       # Windows
```

- 管理界面：`http://localhost:5173`
- API 文档：`http://localhost:8000/docs`

---

## 项目结构

```
backend/
├── services/
│   ├── ca_listener.py       # 社区信号 WebSocket 监听
│   ├── trade_engine.py      # 信号过滤 + 买入决策
│   ├── position_monitor.py  # 止盈/止损自动化
│   ├── ave_client.py        # 4链链上交易执行
│   └── wallet_manager.py    # AES 加密钱包管理
└── routers/                 # 40+ REST API 端点

frontend/src/
├── App.jsx                  # 主界面
└── components/              # 持仓、历史、分析、配置面板
```

---

## 更新日志

详见 [CHANGELOG.md](./CHANGELOG.md)

---

## 免责声明

本项目仅供学习研究和黑客松展示。加密货币交易有极高风险，请勿用真实资金盲目跟单。
#   c o m m u n i t y - m e m e - t r a d e r - o k x 
 
 
