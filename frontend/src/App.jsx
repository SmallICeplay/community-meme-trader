import { useState, useEffect, useRef } from 'react'
import { getTradeStats, getPositions, getConfig, updateConfig, getTradeHistory } from './api'
import { useWebSocket } from './hooks/useWebSocket'
import ConfigPanel from './components/ConfigPanel'
import { GasAnalysisPanel } from './components/ConfigPanel'
import PositionsTable from './components/PositionsTable'
import TradeHistory from './components/TradeHistory'
import LiveLog from './components/LiveLog'
import WalletPanel from './components/WalletPanel'
import WalletPortfolio from './components/WalletPortfolio'
import AnalyticsPanel from './components/AnalyticsPanel'
import { TokenLogo } from './components/PositionsTable'
import { StatCard, Toggle, Card } from './components/UI'
import { clsx } from 'clsx'

const TABS = [
  { id: 'dashboard', label: '仪表盘' },
  { id: 'positions', label: '持仓' },
  { id: 'history', label: '历史' },
  { id: 'wallet', label: '钱包' },
  { id: 'gas', label: '⛽ Gas' },
  { id: 'config', label: '配置' },
]

export default function App() {
  const [tab, setTab] = useState('dashboard')

  useEffect(() => {
    window.__switchTab = setTab
    return () => { delete window.__switchTab }
  }, [])

  const [stats, setStats] = useState(null)
  const [posCount, setPosCount] = useState(0)
  const [botEnabled, setBotEnabled] = useState(false)
  const { logs, connected } = useWebSocket()

  const loadStats = async () => {
    try {
      const [s, p, cfg] = await Promise.all([getTradeStats(), getPositions(), getConfig()])
      setStats(s)
      setPosCount(p.length)
      setBotEnabled(cfg.bot_enabled === 'true')
    } catch { }
  }

  useEffect(() => {
    loadStats()
    const t = setInterval(loadStats, 10000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    const last = logs[0]
    if (last?.type === 'buy' || last?.type === 'sell' || last?.type === 'buy_failed' || last?.type === 'sell_failed') loadStats()
  }, [logs])

  const handleBotToggle = async (v) => {
    setBotEnabled(v)
    await updateConfig({ bot_enabled: v ? 'true' : 'false' })
    loadStats()
  }

  const [logWidth, setLogWidth] = useState(320)

  const startDrag = (e) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = logWidth
    const onMove = (ev) => {
      const delta = startX - ev.clientX
      setLogWidth(Math.max(200, Math.min(600, startW + delta)))
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div className="min-h-screen bg-dark-900 flex flex-col">
      {/* 顶部导航 */}
      <header className="border-b border-dark-600 bg-dark-800 sticky top-0 z-30 shrink-0">
        <div className="px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-lg font-bold text-white">Holdo.AI × AVE Trader</span>
            <div className={clsx(
              'text-xs px-2 py-0.5 rounded-full',
              connected ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'
            )}>
              {connected ? 'WS 已连接' : 'WS 断开'}
            </div>
          </div>
          <Toggle
            checked={botEnabled}
            onChange={handleBotToggle}
            label={botEnabled ? 'Bot 运行中' : 'Bot 已停止'}
          />
        </div>
      </header>

      {/* 主体：左侧内容区 + 右侧固定日志列 */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── 左侧主内容（可纵向滚动） ─────────────────────── */}
        <div className="flex-1 overflow-y-auto min-w-0">
          <div className="px-4 py-4">

            {/* Tab 导航 */}
            <div className="flex gap-1 mb-5 border-b border-dark-600">
              {TABS.map(t => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={clsx(
                    'px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px',
                    tab === t.id
                      ? 'border-accent-blue text-accent-blue'
                      : 'border-transparent text-gray-500 hover:text-gray-300'
                  )}
                >
                  {t.label}
                  {t.id === 'positions' && posCount > 0 && (
                    <span className="ml-1.5 text-xs bg-accent-blue/30 text-accent-blue px-1.5 py-0.5 rounded-full">
                      {posCount}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* ── 仪表盘 ──────────────────────────────────── */}
            {tab === 'dashboard' && (
              <Dashboard stats={stats} posCount={posCount} onRefresh={loadStats} />
            )}

            {/* ── 持仓 ────────────────────────────────────── */}
            {tab === 'positions' && (
              <PositionsTable onRefresh={loadStats} />
            )}

            {/* ── 历史 ────────────────────────────────────── */}
            {tab === 'history' && (
              <div className="space-y-4">
                {stats && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-8 gap-3">
                    <MiniStat label="总交易" value={stats.total_trades} />
                    <MiniStat label="盈利" value={stats.win_trades} color="text-accent-green" />
                    <MiniStat label="亏损" value={stats.loss_trades} color="text-accent-red" />
                    <MiniStat label="胜率" value={stats.win_rate + '%'} />
                    <MiniStat
                      label="总盈亏"
                      value={(stats.total_pnl_usdt >= 0 ? '+' : '') + stats.total_pnl_usdt?.toFixed(3) + 'U'}
                      color={stats.total_pnl_usdt >= 0 ? 'text-accent-green' : 'text-accent-red'}
                    />
                    <MiniStat label="Gas总计" value={stats.total_gas_usd != null ? `~${stats.total_gas_usd.toFixed(3)}U` : '—'} color="text-orange-400" />
                    <MiniStat
                      label="净盈亏"
                      value={stats.total_pnl_usdt != null && stats.total_gas_usd != null
                        ? ((stats.total_pnl_usdt - stats.total_gas_usd) >= 0 ? '+' : '') + (stats.total_pnl_usdt - stats.total_gas_usd).toFixed(3) + 'U'
                        : '—'}
                      color={(stats.total_pnl_usdt - stats.total_gas_usd) >= 0 ? 'text-accent-green' : 'text-red-400'}
                    />
                    <MiniStat label="总投入" value={stats.total_invested?.toFixed(2) + 'U'} />
                  </div>
                )}
                <TradeHistory />
              </div>
            )}

            {/* ── 钱包管理 ────────────────────────────────── */}
            {tab === 'wallet' && (
              <div className="max-w-2xl">
                <WalletPanel logs={logs} />
              </div>
            )}

            {/* ── Gas 分析 ─────────────────────────────────── */}
            {tab === 'gas' && (
              <div className="max-w-2xl">
                <GasAnalysisPanel />
              </div>
            )}

            {/* ── 配置 ────────────────────────────────────── */}
            {tab === 'config' && (
              <div className="max-w-2xl">
                <ConfigPanel onConfigSaved={loadStats} />
              </div>
            )}

          </div>
        </div>

        {/* ── 拖拽分隔条 ───────────────────────────────────── */}
        <div
          onMouseDown={startDrag}
          className="w-1 shrink-0 cursor-col-resize hover:bg-accent-blue/40 active:bg-accent-blue/60 transition-colors border-l border-dark-600"
        />

        {/* ── 右侧实时日志（固定，不随页面滚动） ──────────── */}
        <div className="shrink-0 border-l border-dark-600 bg-dark-850 flex flex-col overflow-hidden" style={{ width: logWidth }}>
          <SideLog logs={logs} connected={connected} />
        </div>

      </div>
    </div>
  )
}

// ── 仪表盘（长页面，含全部分析内容） ─────────────────────────────────────────
function Dashboard({ stats, posCount, onRefresh }) {
  const [days, setDays] = useState(7)

  return (
    <div className="space-y-5">

      {/* 1. 统计卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard
          label="总交易次数"
          value={stats?.total_trades ?? '—'}
          sub={`胜率 ${stats?.win_rate ?? 0}%`}
        />
        <StatCard
          label="总盈亏(含Gas)"
          value={stats ? (() => {
            const net = (stats.total_pnl_usdt ?? 0) - (stats.total_gas_usd ?? 0)
            return (net >= 0 ? '+' : '') + net.toFixed(3) + 'U'
          })() : '—'}
          color={stats && ((stats.total_pnl_usdt ?? 0) - (stats.total_gas_usd ?? 0)) >= 0 ? 'green' : 'red'}
          sub={`交易P&L ${stats ? (stats.total_pnl_usdt >= 0 ? '+' : '') + stats.total_pnl_usdt?.toFixed(3) : '—'}U`}
        />
        <StatCard
          label="胜/负"
          value={stats ? `${stats.win_trades}/${stats.loss_trades}` : '—'}
          color="white"
          sub={`总投入 ${stats?.total_invested?.toFixed(2) ?? 0}U`}
        />
        <StatCard
          label="当前持仓"
          value={posCount}
          color={posCount > 0 ? 'yellow' : 'white'}
          sub="活跃仓位"
        />
        <StatCard
          label="Gas 消耗"
          value={stats?.total_gas_usd != null ? `~${stats.total_gas_usd.toFixed(3)}U` : '—'}
          color="white"
          sub={`均${stats?.total_trades ? (stats.total_gas_usd / stats.total_trades).toFixed(3) : '—'}U/笔`}
        />
      </div>

      {/* 2. 持仓表 */}
      <PositionsTable onRefresh={onRefresh} />

      {/* 3. 钱包资产总览 */}
      <WalletPortfolio />

      {/* 时间范围选择器 */}
      <div className="flex items-center gap-2 pt-2 border-t border-dark-600">
        <span className="text-xs text-gray-500 font-medium">分析时段:</span>
        {[1, 7, 14, 30].map(d => (
          <button
            key={d}
            onClick={() => setDays(d)}
            className={clsx(
              'text-xs px-3 py-1 rounded-full border transition-colors',
              days === d
                ? 'border-accent-blue text-accent-blue bg-accent-blue/10'
                : 'border-dark-500 text-gray-500 hover:text-gray-300'
            )}
          >
            {d === 1 ? '今天' : `${d} 天`}
          </button>
        ))}
      </div>

      {/* 4. 分析内容 */}
      <AnalyticsPanel days={days} />

      {/* 5. Gas 消耗明细 */}
      <GasBreakdown />
    </div>
  )
}

// ── 右侧固定日志栏 ────────────────────────────────────────────────────────────

// 链颜色标签
const CHAIN_COLOR = {
  BSC: 'text-yellow-400 bg-yellow-400/10',
  ETH: 'text-blue-400 bg-blue-400/10',
  SOL: 'text-purple-400 bg-purple-400/10',
  XLAYER: 'text-cyan-400 bg-cyan-400/10',
}

// 卖出原因中文+图标
const REASON_DISPLAY = {
  take_profit: { label: '止盈', icon: '🎯', cls: 'text-green-400 bg-green-400/10' },
  stop_loss:   { label: '止损', icon: '🛡', cls: 'text-red-400 bg-red-400/10' },
  time_limit:  { label: '超时', icon: '⏰', cls: 'text-orange-400 bg-orange-400/10' },
  manual:      { label: '手动', icon: '👆', cls: 'text-blue-400 bg-blue-400/10' },
  zero_balance:{ label: '归零', icon: '💀', cls: 'text-gray-400 bg-gray-400/10' },
  sell_failed: { label: '放弃', icon: '⚠', cls: 'text-gray-400 bg-gray-400/10' },
}

// 日志行图标 & 颜色（根据消息内容智能匹配）
function logMeta(msg = '') {
  if (msg.startsWith('✅') || msg.includes('买入成功'))  return { dot: 'bg-green-400', cls: 'text-green-300' }
  if (msg.startsWith('🟢') || msg.includes('卖出止盈'))  return { dot: 'bg-green-400', cls: 'text-green-300' }
  if (msg.startsWith('🔴'))                              return { dot: 'bg-red-400',   cls: 'text-red-300' }
  if (msg.startsWith('🔔'))                              return { dot: 'bg-yellow-400', cls: 'text-yellow-300' }
  if (msg.startsWith('❌') || msg.includes('失败'))      return { dot: 'bg-red-500',   cls: 'text-red-400' }
  if (msg.startsWith('🚫') || msg.includes('拦截'))      return { dot: 'bg-orange-500', cls: 'text-orange-400' }
  if (msg.startsWith('⚡') || msg.startsWith('⏸'))       return { dot: 'bg-yellow-500', cls: 'text-yellow-400' }
  if (msg.startsWith('💸') || msg.startsWith('📥'))      return { dot: 'bg-blue-400',  cls: 'text-blue-300' }
  return null // 走 level 兜底
}

function ChainBadge({ chain }) {
  if (!chain) return null
  const cls = CHAIN_COLOR[chain?.toUpperCase()] || 'text-gray-400 bg-gray-400/10'
  return (
    <span className={clsx('px-1 py-0.5 rounded text-[9px] font-bold shrink-0', cls)}>
      {chain}
    </span>
  )
}

function SideLog({ logs, connected }) {
  const listRef = useRef(null)
  const [autoScroll, setAutoScroll] = useState(true)

  // 自动滚动到底部（最新消息在下方）
  useEffect(() => {
    if (autoScroll && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [logs, autoScroll])

  const handleScroll = () => {
    const el = listRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setAutoScroll(atBottom)
  }

  const LEVEL_STYLE = {
    info:  'text-gray-300',
    warn:  'text-yellow-400',
    error: 'text-red-400',
  }

  const renderLog = (log) => {
    if (log.type === 'ping') return null
    const ts = log.ts ? new Date(log.ts).toLocaleTimeString('zh-CN', { hour12: false }) : ''

    // ── 买入卡片 ──────────────────────────────────────────────────────────────
    if (log.type === 'buy') {
      const d = log.data || {}
      const name = d.symbol || d.token_name || (d.ca ? d.ca.slice(0, 8) + '…' : '—')
      return (
        <div key={log.id} className="rounded-xl border border-green-800/50 bg-green-900/15 px-3 py-2.5 space-y-1.5">
          {/* 头部：icon + 名称 + 链 + 时间 */}
          <div className="flex items-center gap-1.5 min-w-0">
            <TokenLogo url={d.logo_url} name={name} size={16} />
            <span className="text-[10px] font-bold text-green-400 shrink-0">买入</span>
            <span className="text-gray-100 font-semibold text-xs truncate flex-1">{name}</span>
            <ChainBadge chain={d.chain} />
            <span className="text-gray-600 text-[10px] shrink-0 tabular-nums">{ts}</span>
          </div>
          {/* 详情行 */}
          <div className="flex items-center gap-3 text-[10px] text-gray-400 pl-0.5 flex-wrap">
            <span>投入 <span className="text-gray-200 font-mono">{d.amount_usdt}U</span></span>
            <span>入场 <span className="text-gray-200 font-mono">{fmtPrice(d.entry_price)}</span></span>
            <span>数量 <span className="text-gray-200 font-mono">{d.token_amount > 1000
              ? (d.token_amount / 1000).toFixed(1) + 'K'
              : Number(d.token_amount?.toFixed(2))}</span></span>
            {d.gas_fee_usd > 0 && (
              <span className="ml-auto text-orange-400/80">
                ⛽ <span className="font-mono">{d.gas_fee_usd.toFixed(4)}U</span>
              </span>
            )}
          </div>
        </div>
      )
    }

    // ── 卖出卡片 ──────────────────────────────────────────────────────────────
    if (log.type === 'sell') {
      const d = log.data || {}
      const name = d.symbol || d.token_name || (d.ca ? d.ca.slice(0, 8) + '…' : '—')
      const profit = d.pnl_usdt >= 0
      const pnlSign = profit ? '+' : ''
      const r = REASON_DISPLAY[d.reason] || { label: d.reason, icon: '•', cls: 'text-gray-400 bg-gray-400/10' }
      const multiple = d.entry_price > 0 ? d.exit_price / d.entry_price : null
      const hasGas = d.gas_fee_usd > 0
      const netPnl = hasGas ? (d.pnl_usdt || 0) - d.gas_fee_usd : null
      return (
        <div key={log.id} className={clsx(
          'rounded-xl border px-3 py-2.5 space-y-1.5',
          profit ? 'border-green-800/50 bg-green-900/15' : 'border-red-900/50 bg-red-900/10'
        )}>
          {/* 头部 */}
          <div className="flex items-center gap-1.5 min-w-0">
            <TokenLogo url={d.logo_url} name={name} size={16} />
            <span className={clsx('text-[10px] font-bold shrink-0', profit ? 'text-green-400' : 'text-red-400')}>
              卖出
            </span>
            <span className="text-gray-100 font-semibold text-xs truncate flex-1">{name}</span>
            {/* 原因标签 */}
            <span className={clsx('px-1.5 py-0.5 rounded text-[9px] font-bold shrink-0', r.cls)}>
              {r.icon} {r.label}
            </span>
            <span className="text-gray-600 text-[10px] shrink-0 tabular-nums">{ts}</span>
          </div>
          {/* PnL 核心数据 */}
          <div className="flex items-center gap-3 pl-0.5">
            <span className={clsx('text-sm font-bold font-mono tabular-nums', profit ? 'text-green-400' : 'text-red-400')}>
              {pnlSign}{d.pnl_pct?.toFixed(1)}%
            </span>
            <span className={clsx('text-xs font-mono tabular-nums', profit ? 'text-green-300' : 'text-red-300')}>
              {pnlSign}{d.pnl_usdt?.toFixed(3)}U
            </span>
            {multiple !== null && (
              <span className="text-gray-500 text-[10px] tabular-nums">
                {multiple >= 1 ? `${multiple.toFixed(2)}x` : `${(multiple * 100).toFixed(0)}%`}
              </span>
            )}
            {/* Gas + 净盈亏 */}
            {hasGas && (
              <span className="ml-auto flex items-center gap-1.5 text-[10px]">
                <span className="text-orange-400/80">⛽{d.gas_fee_usd.toFixed(4)}U</span>
                <span className="text-gray-600">→</span>
                <span className={clsx('font-mono font-semibold', netPnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                  净{netPnl >= 0 ? '+' : ''}{netPnl?.toFixed(3)}U
                </span>
              </span>
            )}
          </div>
          {/* 价格对比行 */}
          <div className="flex items-center gap-1.5 text-[10px] text-gray-500 pl-0.5">
            <ChainBadge chain={d.chain} />
            <span>入 <span className="text-gray-400 font-mono">{fmtPrice(d.entry_price)}</span></span>
            <span className="text-gray-700">→</span>
            <span>出 <span className={clsx('font-mono', profit ? 'text-green-400' : 'text-red-400')}>{fmtPrice(d.exit_price)}</span></span>
            {d.hold_minutes != null && (
              <span className="ml-auto text-gray-600">持仓 {d.hold_minutes < 60
                ? d.hold_minutes.toFixed(0) + 'm'
                : (d.hold_minutes / 60).toFixed(1) + 'h'}</span>
            )}
          </div>
        </div>
      )
    }

    // ── 买入失败卡片 ──────────────────────────────────────────────────────────
    if (log.type === 'buy_failed') {
      const d = log.data || {}
      const name = d.token_name || (d.ca ? d.ca.slice(0, 8) + '…' : '—')
      return (
        <div key={log.id} className="rounded-xl border border-red-800/60 bg-red-900/15 px-3 py-2.5 space-y-1.5">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="text-[10px] font-bold text-red-400 shrink-0">❌ 买入失败</span>
            <span className="text-gray-100 font-semibold text-xs truncate flex-1">{name}</span>
            {d.tried_amounts
              ? <span className="text-gray-500 text-[10px] shrink-0">试: {d.tried_amounts}</span>
              : d.amount_usdt > 0 && <span className="text-gray-500 text-[10px] shrink-0">{d.amount_usdt}U</span>
            }
            <ChainBadge chain={d.chain} />
            <span className="text-gray-600 text-[10px] shrink-0 tabular-nums">{ts}</span>
          </div>
          <div className="text-[10px] text-red-300/90 leading-relaxed pl-0.5">
            {d.reason}
          </div>
          {d.error && (() => {
            // 只显示 status=XXXX 部分，避免和 reason 重复
            const m = d.error.match(/status=(\d+)/)
            return m ? (
              <div className="text-[9px] text-gray-600 font-mono pl-0.5">
                AVE error {m[1]}
              </div>
            ) : null
          })()}
        </div>
      )
    }

    // ── 卖出失败卡片 ──────────────────────────────────────────────────────────
    if (log.type === 'sell_failed') {
      const d = log.data || {}
      const name = d.token_name || (d.ca ? d.ca.slice(0, 8) + '…' : '—')
      const SELL_REASON_ZH = { take_profit: '止盈', stop_loss: '止损', time_limit: '超时', manual: '手动', zero_balance: '归零' }
      const triggerLabel = SELL_REASON_ZH[d.sell_reason] || ''
      const isAbandoned = d.abandoned === true
      return (
        <div key={log.id} className={`rounded-xl border px-3 py-2.5 space-y-1.5 ${isAbandoned ? 'border-red-700/80 bg-red-950/30' : 'border-orange-800/60 bg-orange-900/10'}`}>
          <div className="flex items-center gap-1.5 min-w-0">
            <span className={`text-[10px] font-bold shrink-0 ${isAbandoned ? 'text-red-400' : 'text-orange-400'}`}>
              {isAbandoned ? '🚨 放弃卖出' : '⚠ 卖出失败'}
            </span>
            <span className="text-gray-100 font-semibold text-xs truncate flex-1">{name}</span>
            {triggerLabel && !isAbandoned && (
              <span className="text-[9px] text-orange-300/70 bg-orange-900/30 px-1.5 py-0.5 rounded shrink-0">
                触发{triggerLabel}
              </span>
            )}
            <ChainBadge chain={d.chain} />
            <span className="text-gray-600 text-[10px] shrink-0 tabular-nums">{ts}</span>
          </div>
          <div className="flex items-center gap-2 pl-0.5">
            <span className={`text-[10px] leading-relaxed flex-1 ${isAbandoned ? 'text-red-300' : 'text-orange-300/90'}`}>
              {d.reason}
            </span>
            {d.fail_count > 0 && (
              <span className="text-[9px] text-gray-600 shrink-0">累计失败 {d.fail_count} 次</span>
            )}
          </div>
          {d.error && (
            <div className="text-[9px] text-gray-600 font-mono break-all leading-relaxed pl-0.5 border-t border-orange-900/40 pt-1">
              {d.error.slice(0, 160)}
            </div>
          )}
        </div>
      )
    }

    // ── CA 收到 ───────────────────────────────────────────────────────────────
    if (log.type === 'ca_received') {
      const d = log.data || {}
      const name = d.symbol || ''
      const caShort = d.ca ? d.ca.slice(0, 6) + '…' + d.ca.slice(-4) : '—'
      const pushCount = d.push_count || 1
      const isFirst = pushCount === 1
      const qwfcDelta = d.qwfc_delta || 0
      const isHot = qwfcDelta >= 10  // 热度暴涨标记
      return (
        <div key={log.id} className="flex items-center gap-1.5 px-1 py-0.5 hover:bg-white/[0.02] rounded group">
          <span className="text-gray-700 shrink-0 tabular-nums text-[10px]">{ts}</span>
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isFirst ? 'bg-blue-500/60' : 'bg-gray-600/60'}`} />
          {isFirst
            ? <span className="text-blue-400 text-[10px] font-bold shrink-0">新CA</span>
            : <span className="text-gray-500 text-[10px] font-bold shrink-0">第{pushCount}次</span>
          }
          {name
            ? <span className={`text-xs font-semibold truncate ${isFirst ? 'text-blue-200' : 'text-gray-400'}`}>{name}</span>
            : <span className="text-blue-400/60 text-[10px] font-mono truncate">{caShort}</span>
          }
          {name && <span className="text-gray-600 text-[10px] font-mono hidden group-hover:inline truncate">{caShort}</span>}
          {!isFirst && d.qwfc > 0 && (
            <span className={`text-[9px] shrink-0 tabular-nums ${isHot ? 'text-orange-400' : 'text-gray-600'}`}>
              {isHot ? `🔥+${qwfcDelta}` : `+${qwfcDelta}`}
            </span>
          )}
          <ChainBadge chain={d.chain} />
        </div>
      )
    }

    // ── 普通 log 行 ───────────────────────────────────────────────────────────
    const msg = log.data?.message || ''
    const meta = logMeta(msg)
    const textCls = meta?.cls || LEVEL_STYLE[log.level] || 'text-gray-400'
    const dotCls = meta?.dot || (log.level === 'error' ? 'bg-red-500' : log.level === 'warn' ? 'bg-yellow-500' : 'bg-gray-600')
    return (
      <div key={log.id} className="flex items-start gap-1.5 px-1 py-0.5 hover:bg-white/[0.02] rounded">
        <span className="text-gray-700 shrink-0 tabular-nums text-[10px] mt-0.5">{ts}</span>
        <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0 mt-1', dotCls)} />
        <span className={clsx('text-[11px] break-all leading-relaxed', textCls)}>{msg}</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-dark-600 shrink-0">
        <span className="text-xs font-semibold text-gray-300">实时日志</span>
        <div className="flex items-center gap-2">
          {!autoScroll && (
            <button
              onClick={() => { setAutoScroll(true); if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight }}
              className="text-[10px] text-blue-400 hover:text-blue-300 px-1.5 py-0.5 rounded border border-blue-800/50 hover:bg-blue-900/20 transition-colors"
            >
              ↓ 最新
            </button>
          )}
          <div className="flex items-center gap-1.5">
            <div className={clsx('w-1.5 h-1.5 rounded-full', connected ? 'bg-green-400 animate-pulse' : 'bg-red-500')} />
            <span className="text-[10px] text-gray-600">{connected ? '已连接' : '断开'}</span>
          </div>
        </div>
      </div>

      {/* 日志列表 */}
      <div
        ref={listRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-2 py-2 font-mono text-xs space-y-1 scroll-smooth"
      >
        {logs.length === 0 && (
          <div className="text-gray-600 text-center py-8 text-[11px]">等待事件...</div>
        )}
        {/* 正序显示，最新在底部 */}
        {[...logs].reverse().map(log => renderLog(log))}
      </div>
    </div>
  )
}

function fmtPrice(p) {
  if (!p || p === 0) return '—'
  if (p < 0.000001) return p.toExponential(2)
  if (p < 0.01) return p.toFixed(6)
  return p.toFixed(4)
}

// ── 历史页小统计格子 ──────────────────────────────────────────────────────────
function MiniStat({ label, value, color = 'text-gray-200' }) {
  return (
    <div className="bg-dark-800 rounded-lg px-3 py-2 border border-dark-600">
      <div className="text-xs text-gray-500 mb-0.5">{label}</div>
      <div className={clsx('text-sm font-semibold font-mono', color)}>{value}</div>
    </div>
  )
}

function Row({ label, value, valueColor = 'text-gray-300' }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-gray-500">{label}</span>
      <span className={clsx('font-mono', valueColor)}>{value}</span>
    </div>
  )
}

// ── Gas 消耗明细列表 ─────────────────────────────────────────────────────────
const REASON_ICON = {
  take_profit: '🎯', stop_loss: '🛡', time_limit: '⏰',
  manual: '👆', zero_balance: '💀', sell_failed: '⚠',
}
const REASON_ZH = {
  take_profit: '止盈', stop_loss: '止损', time_limit: '超时',
  manual: '手动', zero_balance: '归零', sell_failed: '放弃',
}

function GasBreakdown() {
  const [trades, setTrades] = useState([])
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    getTradeHistory(50, 0).then(d => setTrades(d)).catch(() => {})
  }, [])

  const withGas = trades.filter(t => t.gas_fee_usd > 0).sort((a, b) => b.gas_fee_usd - a.gas_fee_usd)
  const totalGas = withGas.reduce((s, t) => s + t.gas_fee_usd, 0)
  const avgGas = withGas.length ? totalGas / withGas.length : 0
  const shown = expanded ? withGas : withGas.slice(0, 8)

  if (withGas.length === 0) return null

  return (
    <div className="bg-dark-800 border border-dark-600 rounded-xl p-4 space-y-3">
      {/* 标题 + 汇总 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-300">Gas 消耗明细</span>
          <span className="text-xs text-gray-500">({withGas.length} 笔)</span>
        </div>
        <div className="flex items-center gap-4 text-[11px]">
          <span className="text-gray-500">累计 <span className="font-mono text-orange-400">{totalGas.toFixed(3)}U</span></span>
          <span className="text-gray-500">均值 <span className="font-mono text-orange-400">{avgGas.toFixed(3)}U/笔</span></span>
        </div>
      </div>

      {/* Gas 条形列表 */}
      <div className="space-y-1.5">
        {shown.map(t => {
          const display = t.symbol || t.token_name || (t.ca.slice(0, 6) + '…' + t.ca.slice(-4))
          const netPnl = (t.pnl_usdt || 0) - t.gas_fee_usd
          const barPct = totalGas > 0 ? Math.min((t.gas_fee_usd / totalGas) * 100, 100) : 0
          const icon = REASON_ICON[t.reason] || '•'
          const zh = REASON_ZH[t.reason] || t.reason
          return (
            <div key={t.id} className="flex items-center gap-2 group">
              {/* 图标 + 代币名 */}
              <span className="text-[10px] shrink-0">{icon}</span>
              <span className="text-xs text-gray-300 w-20 shrink-0 truncate" title={display}>{display}</span>
              {/* 进度条 */}
              <div className="flex-1 h-1.5 bg-dark-600 rounded-full overflow-hidden">
                <div className="h-full bg-orange-500/60 rounded-full" style={{ width: `${barPct}%` }} />
              </div>
              {/* Gas 金额 */}
              <span className="font-mono text-orange-400 text-[11px] w-16 text-right shrink-0">
                {t.gas_fee_usd.toFixed(3)}U
              </span>
              {/* 净盈亏 */}
              <span className={clsx('font-mono text-[11px] w-16 text-right shrink-0', netPnl >= 0 ? 'text-accent-green' : 'text-red-400')}>
                {netPnl >= 0 ? '+' : ''}{netPnl.toFixed(3)}U
              </span>
              {/* 原因标签 */}
              <span className="text-gray-600 text-[10px] w-8 text-right shrink-0">{zh}</span>
            </div>
          )
        })}
      </div>

      {withGas.length > 8 && (
        <button
          onClick={() => setExpanded(v => !v)}
          className="text-xs text-gray-500 hover:text-gray-300 w-full text-center py-1 border-t border-dark-600 mt-1"
        >
          {expanded ? '收起' : `显示全部 ${withGas.length} 笔`}
        </button>
      )}
    </div>
  )
}
