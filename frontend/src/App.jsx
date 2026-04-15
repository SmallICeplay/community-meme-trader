import { useState, useEffect, useRef, useCallback } from 'react'
import { getTradeStats, getPositions, getConfig, updateConfig, getTradeHistory, getRecentSignals, getSignalOverview } from './api'
import { useWebSocket } from './hooks/useWebSocket'
import ConfigPanel from './components/ConfigPanel'
import { GasAnalysisPanel } from './components/ConfigPanel'
import PositionsTable from './components/PositionsTable'
import TradeHistory from './components/TradeHistory'
import LiveLog from './components/LiveLog'
import WalletPanel from './components/WalletPanel'
import WalletPortfolio from './components/WalletPortfolio'
import AnalyticsPanel from './components/AnalyticsPanel'
import AiChat from './components/AiChat'
import Changelog from './components/Changelog'
import SocialLeaderboard from './components/SocialLeaderboard'
import CommunityLeaderboard from './components/CommunityLeaderboard'
import AdminPanel, { ADMIN_TOKEN_KEY, LoginModal } from './components/AdminPanel'
import { TokenLogo } from './components/PositionsTable'
import { StatCard, Toggle, Card } from './components/UI'
import { clsx } from 'clsx'

// ── 音效合成（Web Audio API，无需音频文件） ───────────────────────────────────
// AudioContext 懒加载：仅在用户有过交互后才创建，避免 autoplay 策略报错
let _audioCtx = null
let _userInteracted = false

// 监听首次用户交互，解锁音频
;['click', 'keydown', 'touchstart'].forEach(evt =>
  document.addEventListener(evt, () => { _userInteracted = true }, { once: true, passive: true })
)

function getCtx() {
  if (!_userInteracted) return null
  if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)()
  if (_audioCtx.state === 'suspended') _audioCtx.resume()
  return _audioCtx
}

function playSound(type) {
  try {
    const ctx = getCtx()
    if (!ctx) return   // 用户还没有交互，静默跳过
    const now = ctx.currentTime

    if (type === 'signal') {
      ;[0, 0.12].forEach((delay, i) => {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.connect(gain); gain.connect(ctx.destination)
        osc.type = 'sine'
        osc.frequency.setValueAtTime(880 + i * 220, now + delay)
        osc.frequency.exponentialRampToValueAtTime(1320 + i * 220, now + delay + 0.08)
        gain.gain.setValueAtTime(0.18, now + delay)
        gain.gain.exponentialRampToValueAtTime(0.001, now + delay + 0.1)
        osc.start(now + delay); osc.stop(now + delay + 0.12)
      })
    } else if (type === 'buy') {
      [[261.6, 0], [329.6, 0.05]].forEach(([freq, delay]) => {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.connect(gain); gain.connect(ctx.destination)
        osc.type = 'triangle'
        osc.frequency.value = freq
        gain.gain.setValueAtTime(0, now + delay)
        gain.gain.linearRampToValueAtTime(0.22, now + delay + 0.04)
        gain.gain.exponentialRampToValueAtTime(0.001, now + delay + 0.35)
        osc.start(now + delay); osc.stop(now + delay + 0.4)
      })
    } else if (type === 'sell') {
      [[784, 0], [659.3, 0.1], [523.3, 0.2]].forEach(([freq, delay]) => {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.connect(gain); gain.connect(ctx.destination)
        osc.type = 'sine'
        osc.frequency.value = freq
        gain.gain.setValueAtTime(0.2, now + delay)
        gain.gain.exponentialRampToValueAtTime(0.001, now + delay + 0.12)
        osc.start(now + delay); osc.stop(now + delay + 0.15)
      })
    }
  } catch {}
}

// ── 音效 hook：监听 logs 触发对应音效 ───────────────────────────────────────
function useSoundEffect(logs, muted) {
  const lastIdRef = useRef(null)
  useEffect(() => {
    if (muted || !logs.length) return
    const newest = logs[0]
    if (!newest || newest.id === lastIdRef.current) return
    lastIdRef.current = newest.id
    if (newest.type === 'ca_received') playSound('signal')
    else if (newest.type === 'buy') playSound('buy')
    else if (newest.type === 'sell') playSound('sell')
  }, [logs, muted])
}

const TABS = [
  { id: 'leaderboard',  label: '🏆 牛人榜' },
  { id: 'community',    label: '🏘 社群榜' },
  { id: 'dashboard',    label: '仪表盘' },
  { id: 'data',         label: '数据' },
  { id: 'config',       label: '配置' },
]

// 数字滚动 hook：值变化时触发向上滚入动画，返回 [显示值, className]
function useCountUp(value, format) {
  const [display, setDisplay] = useState({ val: value, key: 0 })
  const prev = useRef(value)
  useEffect(() => {
    if (prev.current === value) return
    prev.current = value
    setDisplay(d => ({ val: value, key: d.key + 1 }))
  }, [value])
  const formatted = value === null || value === undefined
    ? '—'
    : format ? format(value) : String(value)
  return [formatted, display.key]
}

export default function App() {
  const [tab, setTab] = useState('leaderboard')

  // ── 管理员状态 ─────────────────────────────────────────────
  const [isAdmin, setIsAdmin]           = useState(() => !!localStorage.getItem(ADMIN_TOKEN_KEY))
  const [showLogin, setShowLogin]       = useState(false)
  const [showAdminPanel, setShowAdminPanel] = useState(false)

  const checkAdmin = async () => {
    const token = localStorage.getItem(ADMIN_TOKEN_KEY)
    if (!token) { setIsAdmin(false); return }
    try {
      const res = await fetch('/api/admin/me', { headers: { Authorization: `Bearer ${token}` } })
      if (!res.ok) { localStorage.removeItem(ADMIN_TOKEN_KEY); setIsAdmin(false) }
      else setIsAdmin(true)
    } catch { setIsAdmin(false) }
  }

  useEffect(() => { checkAdmin() }, [])

  useEffect(() => {
    window.__switchTab = setTab
    return () => { delete window.__switchTab }
  }, [])

  const [stats, setStats] = useState(null)
  const [posCount, setPosCount] = useState(0)
  const [botEnabled, setBotEnabled] = useState(false)
  const [autoBuyEnabled, setAutoBuyEnabled] = useState(false)
  const { logs, connected } = useWebSocket()

  // 音效静音开关（localStorage 持久化）
  const [muted, setMuted] = useState(() => localStorage.getItem('sound_muted') === 'true')
  const toggleMute = () => setMuted(v => {
    const next = !v
    localStorage.setItem('sound_muted', next)
    return next
  })
  useSoundEffect(logs, muted)

  const loadStats = async () => {
    try {
      const [s, p, cfg] = await Promise.all([getTradeStats(), getPositions(), getConfig()])
      setStats(s)
      setPosCount(p.length)
      setBotEnabled(cfg.bot_enabled === 'true')
      setAutoBuyEnabled(cfg.auto_buy_enabled === 'true')
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

  const [showLiveLog, setShowLiveLog] = useState(() => localStorage.getItem('show_live_log') !== 'false')
  useEffect(() => {
    const handler = (e) => setShowLiveLog(e.detail)
    window.addEventListener('show_live_log_change', handler)
    return () => window.removeEventListener('show_live_log_change', handler)
  }, [])
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

  // 移动端检测
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768)
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [])

  return (
    <div className="min-h-screen bg-dark-900 flex flex-col">
      {/* 顶部导航 */}
      <header className="border-b border-dark-600 bg-dark-800 sticky top-0 z-30 shrink-0">
        <div className="px-3 md:px-4 h-14 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0 shrink-0">
            <div className="flex items-center gap-1.5">
              <div className={clsx(
                'w-2 h-2 rounded-full shrink-0',
                botEnabled ? 'bg-accent-green bot-glow' : 'bg-gray-600'
              )} />
              <span className="text-sm md:text-lg font-bold text-white whitespace-nowrap">Hodlo.AI × AVE</span>
            </div>
            <div className={clsx(
              'text-xs px-1.5 py-0.5 rounded-full hidden sm:block',
              connected ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'
            )}>
              {connected ? 'WS 已连接' : 'WS 断开'}
            </div>
            {/* 移动端 WS 状态点 */}
            <div className={clsx(
              'w-1.5 h-1.5 rounded-full sm:hidden shrink-0',
              connected ? 'bg-green-400' : 'bg-red-500'
            )} />
          </div>
          {/* 实时指标 — 桌面端显示 */}
          <div className="hidden md:flex flex-1 justify-center overflow-hidden">
            <HeaderStats stats={stats} posCount={posCount} />
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {/* 管理员入口 */}
            {isAdmin ? (
              <button
                onClick={() => setShowAdminPanel(true)}
                title="管理员控制台"
                className="w-8 h-8 flex items-center justify-center rounded-lg border border-yellow-700/40 text-yellow-500 bg-yellow-900/10 hover:bg-yellow-900/25 transition-all text-sm"
              >
                🔐
              </button>
            ) : (
              <button
                onClick={() => setShowLogin(true)}
                title="管理员登录"
                className="w-8 h-8 flex items-center justify-center rounded-lg border border-dark-500 text-gray-600 hover:text-gray-400 hover:border-dark-400 transition-all text-sm"
              >
                🔒
              </button>
            )}
            {/* 音效开关 */}
            <button
              onClick={toggleMute}
              title={muted ? '点击开启音效' : '点击静音'}
              className={clsx(
                'w-8 h-8 flex items-center justify-center rounded-lg border text-base transition-all',
                muted
                  ? 'border-dark-500 text-gray-600 bg-dark-700 hover:border-gray-500 hover:text-gray-400'
                  : 'border-accent-blue/40 text-accent-blue bg-accent-blue/10 hover:bg-accent-blue/20'
              )}
            >
              {muted ? '🔕' : '🔔'}
            </button>
            <Toggle
              checked={botEnabled}
              onChange={handleBotToggle}
              label={isMobile ? (botEnabled ? 'ON' : 'OFF') : (botEnabled ? 'Bot 运行中' : 'Bot 已停止')}
            />
          </div>
        </div>
        {/* 移动端资产简要行 */}
        <div className="md:hidden border-t border-dark-700 px-3 py-1.5 flex items-center gap-3 overflow-x-auto scrollbar-none">
          <MobileHeaderStats stats={stats} posCount={posCount} connected={connected} />
        </div>
      </header>

      {/* 状态横幅 */}
      {!botEnabled ? (
        <div className="bg-yellow-900/30 border-b border-yellow-700/40 px-3 py-1.5 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-yellow-400 text-xs">
            <span>⚠️</span>
            <span className="font-medium">Bot 未启用 — 观察模式</span>
          </div>
          <button
            onClick={() => handleBotToggle(true)}
            className="text-xs px-2.5 py-1 rounded border border-yellow-600/50 text-yellow-400 hover:bg-yellow-900/40 transition-colors whitespace-nowrap shrink-0"
          >启用</button>
        </div>
      ) : autoBuyEnabled ? (
        <div className="bg-orange-900/20 border-b border-orange-700/30 px-3 py-1.5 flex items-center gap-1.5 text-orange-400 text-xs">
          <span>⚡</span>
          <span className="hidden sm:inline">Bot 运行中 · 信息流自动购买已开启 — 过滤通过的 CA 将自动买入</span>
          <span className="sm:hidden">自动买入模式</span>
        </div>
      ) : (
        <div className="bg-blue-900/20 border-b border-blue-700/30 px-3 py-1.5 flex items-center gap-1.5 text-blue-400 text-xs">
          <span>🔗</span>
          <span className="hidden sm:inline">Bot 运行中 · 跟单模式 — 仅对已配置跟单的喊单人执行买入，其余信号只记录</span>
          <span className="sm:hidden">跟单模式</span>
        </div>
      )}

      {/* 主体：左侧内容区 + 右侧固定日志列（移动端无日志列） */}
      <div className="flex flex-1 md:overflow-hidden">

        {/* ── 左侧主内容（可纵向滚动） ─────────────────────── */}
        <div className="flex-1 md:overflow-y-auto min-w-0">
          <div className="px-3 md:px-4 py-3 md:py-4">

            {/* Tab 导航 — 移动端可横向滚动 */}
            <div className="flex gap-0.5 mb-4 md:mb-5 border-b border-dark-600 overflow-x-auto scrollbar-none -mx-3 md:mx-0 px-3 md:px-0">
              {TABS.map(t => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={clsx(
                    'px-3 md:px-4 py-2 text-xs md:text-sm font-medium border-b-2 transition-colors -mb-px whitespace-nowrap shrink-0',
                    tab === t.id
                      ? 'border-accent-blue text-accent-blue'
                      : 'border-transparent text-gray-500 hover:text-gray-300'
                  )}
                >
                  {t.label}
                  {t.id === 'positions' && posCount > 0 && (
                    <span className="ml-1 text-xs bg-accent-blue/30 text-accent-blue px-1.5 py-0.5 rounded-full">
                      {posCount}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* ── 社群牛人榜 ──────────────────────────────── */}
            {tab === 'leaderboard' && (
              <div className="-mx-4 -mt-4 px-4 pt-4">
                <SocialLeaderboard />
              </div>
            )}

            {/* ── 社群胜率榜 ──────────────────────────────── */}
            {tab === 'community' && (
              <div className="-mx-4 -mt-4 px-4 pt-4">
                <CommunityLeaderboard />
              </div>
            )}

            {/* ── 仪表盘 ──────────────────────────────────── */}
            {tab === 'dashboard' && (
              <Dashboard posCount={posCount} onRefresh={loadStats} />
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
              <div className="max-w-2xl space-y-4">
                <WalletPanel logs={logs} />
                <DemoWalletSwitcher />
              </div>
            )}

            {/* ── Gas 分析 ─────────────────────────────────── */}
            {tab === 'gas' && (
              <div className="max-w-2xl">
                <GasAnalysisPanel />
              </div>
            )}

            {/* ── 数据（持仓/历史/钱包/Gas/日志 合并） ────── */}
            {tab === 'data' && (
              <DataPanel stats={stats} logs={logs} posCount={posCount} onRefresh={loadStats} />
            )}

            {/* ── 配置 ────────────────────────────────────── */}
            {tab === 'config' && (
              <div className="max-w-2xl">
                <ConfigPanel onConfigSaved={loadStats} />
              </div>
            )}

            {/* ── 变更日志（顶层 tab 保留，data 子 tab 也有） ── */}
            {tab === 'changelog' && (
              <Changelog />
            )}

          </div>
        </div>

        {/* ── 拖拽分隔条（仅桌面端） ────────────────────────── */}
        {!isMobile && showLiveLog && (
          <div
            onMouseDown={startDrag}
            className="w-1 shrink-0 cursor-col-resize hover:bg-accent-blue/40 active:bg-accent-blue/60 transition-colors border-l border-dark-600"
          />
        )}

        {/* ── 右侧实时日志（仅桌面端） ─────────────────────── */}
        {!isMobile && showLiveLog && (
          <div className="shrink-0 border-l border-dark-600 bg-dark-850 flex flex-col overflow-hidden" style={{ width: logWidth }}>
            <div className="flex-1 overflow-hidden flex flex-col min-h-0">
              <SideLog logs={logs} connected={connected} />
            </div>
            <AiChat />
          </div>
        )}

      </div>

      {/* ── 管理员登录弹窗 ──────────────────────────────────── */}
      {showLogin && (
        <LoginModal
          onSuccess={() => { setIsAdmin(true); setShowLogin(false) }}
          onClose={() => setShowLogin(false)}
        />
      )}

      {/* ── 管理员控制台（全屏覆盖层） ───────────────────────── */}
      {showAdminPanel && (
        <div className="fixed inset-0 z-40 bg-dark-900/95 overflow-y-auto">
          <div className="max-w-2xl mx-auto px-4 py-8">
            <AdminPanel onClose={() => setShowAdminPanel(false)} />
          </div>
        </div>
      )}
    </div>
  )
}


// ── 数据面板（持仓/历史/钱包/Gas/日志 合并） ──────────────────────────────────
const DATA_SUBTABS = [
  { id: 'positions', label: '持仓' },
  { id: 'history',   label: '历史交易' },
  { id: 'wallet',    label: '钱包' },
  { id: 'gas',       label: '⛽ Gas' },
  { id: 'changelog', label: '📋 更新日志' },
]

function DataPanel({ stats, logs, posCount, onRefresh }) {
  const [sub, setSub] = useState('positions')

  return (
    <div className="space-y-4">
      {/* 子 tab 栏 */}
      <div className="flex gap-1 border-b border-dark-700">
        {DATA_SUBTABS.map(t => (
          <button
            key={t.id}
            onClick={() => setSub(t.id)}
            className={clsx(
              'px-3 py-1.5 text-sm font-medium border-b-2 transition-colors -mb-px',
              sub === t.id
                ? 'border-accent-blue text-accent-blue'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            )}
          >
            {t.label}
            {t.id === 'positions' && posCount > 0 && (
              <span className="ml-1.5 text-xs bg-accent-blue/30 text-accent-blue px-1.5 py-0.5 rounded-full">{posCount}</span>
            )}
          </button>
        ))}
      </div>

      {sub === 'positions' && <PositionsTable onRefresh={onRefresh} />}

      {sub === 'history' && (
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

      {sub === 'wallet' && (
        <div className="max-w-2xl space-y-4">
          <WalletPanel logs={logs} />
          <DemoWalletSwitcher />
        </div>
      )}

      {sub === 'gas' && (
        <div className="max-w-2xl">
          <GasAnalysisPanel />
        </div>
      )}

      {sub === 'changelog' && <Changelog />}
    </div>
  )
}

// ── 演示钱包切换器（钱包页下方） ─────────────────────────────────────────────
function DemoWalletSwitcher() {
  const [status, setStatus]   = useState(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg]         = useState(null)

  useEffect(() => {
    fetch('/api/wallet/status')
      .then(r => r.json())
      .then(setStatus)
      .catch(() => {})
  }, [])

  if (!status || !status.has_demo) return null

  const useDemo = async () => {
    if (!confirm('确认切换到演示钱包？当前自定义钱包将被替换（资产请先转出）')) return
    setLoading(true)
    try {
      const res = await fetch('/api/wallet/use_demo', { method: 'POST' })
      const d = await res.json()
      setMsg(res.ok ? { ok: true, text: d.message } : { ok: false, text: d.detail || '失败' })
      if (res.ok) {
        const s2 = await fetch('/api/wallet/status').then(r => r.json())
        setStatus(s2)
      }
    } catch { setMsg({ ok: false, text: '请求失败' }) }
    finally { setLoading(false) }
  }

  return (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-200">演示钱包</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            当前模式：
            <span className={clsx('ml-1 font-medium', status.wallet_mode === 'demo' ? 'text-blue-400' : 'text-orange-400')}>
              {status.wallet_mode === 'demo' ? '演示钱包' : '自定义钱包'}
            </span>
          </p>
        </div>
        {status.wallet_mode !== 'demo' && (
          <button
            onClick={useDemo}
            disabled={loading}
            className="px-3 py-1.5 text-xs rounded-lg bg-blue-900/30 border border-blue-800/40 text-blue-400 hover:bg-blue-900/50 transition-colors"
          >
            {loading ? '切换中...' : '恢复演示钱包'}
          </button>
        )}
        {status.wallet_mode === 'demo' && (
          <span className="text-[10px] text-blue-400/60 bg-blue-900/20 border border-blue-800/30 px-2 py-1 rounded">
            当前使用中
          </span>
        )}
      </div>
      {msg && (
        <div className={clsx(
          'mt-2 text-xs px-2 py-1.5 rounded border',
          msg.ok ? 'bg-green-900/20 border-green-800/40 text-green-400' : 'bg-red-900/20 border-red-800/40 text-red-400'
        )}>
          {msg.text}
        </div>
      )}
    </Card>
  )
}

// ── 仪表盘（长页面，含全部分析内容） ─────────────────────────────────────────
const STAT_PERIODS = [
  { key: 'hour',  label: '时' },
  { key: 'day',   label: '天' },
  { key: 'week',  label: '周' },
  { key: 'month', label: '月' },
  { key: 'year',  label: '年' },
  { key: 'all',   label: '全' },
]

function Dashboard({ posCount, onRefresh }) {
  const [days, setDays] = useState(7)
  const [statPeriod, setStatPeriod] = useState('all')
  const [stats, setStats] = useState(null)
  const [statsLoading, setStatsLoading] = useState(false)

  const loadStats = useCallback(async () => {
    setStatsLoading(true)
    try {
      const s = await getTradeStats(statPeriod)
      setStats(s)
    } catch {}
    finally { setStatsLoading(false) }
  }, [statPeriod])

  useEffect(() => {
    loadStats()
    const t = setInterval(loadStats, 15000)
    return () => clearInterval(t)
  }, [loadStats])

  return (
    <div className="space-y-5">

      {/* 1. 统计卡片 + 时段切换 */}
      <div>
        {/* 时段切换条 */}
        <div className="flex items-center gap-1.5 mb-2">
          <span className="text-[11px] text-gray-600 mr-1">卡片时段</span>
          {STAT_PERIODS.map(p => (
            <button
              key={p.key}
              onClick={() => setStatPeriod(p.key)}
              className={clsx(
                'text-xs px-2.5 py-0.5 rounded-full border transition-all',
                statPeriod === p.key
                  ? 'border-accent-blue text-accent-blue bg-accent-blue/15 font-semibold'
                  : 'border-dark-500 text-gray-600 hover:text-gray-300 hover:border-gray-500'
              )}
            >{p.label}</button>
          ))}
          {statsLoading && (
            <span className="w-1.5 h-1.5 rounded-full bg-accent-blue animate-ping ml-1" />
          )}
          {stats && statPeriod !== 'all' && (
            <span className="text-[10px] text-gray-600 ml-auto">
              {statPeriod === 'hour' ? '近1小时' : statPeriod === 'day' ? '近24小时' :
               statPeriod === 'week' ? '近7天' : statPeriod === 'month' ? '近30天' : '近1年'}
              · {stats.total_trades} 笔
            </span>
          )}
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2 md:gap-3">
          <div className="col-span-2 md:col-span-1">
            <SignalOverviewCard />
          </div>
          <TradeCountCard stats={stats} index={0} period={statPeriod} />
          <PnlCard stats={stats} index={1} period={statPeriod} />
          <WinLossCard stats={stats} index={2} period={statPeriod} />
          <PositionCard posCount={posCount} index={3} />
          <GasCard stats={stats} index={4} period={statPeriod} />
        </div>
      </div>

      {/* 2. 信号流 */}
      <SignalFeed />

      {/* 3. 持仓表 */}
      <PositionsTable onRefresh={onRefresh} />

      {/* 3. 钱包资产总览 */}
      <WalletPortfolio />

      {/* 时间范围选择器（分析面板用） */}
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
    <span className={clsx('px-1 py-0.5 rounded text-xs font-bold shrink-0', cls)}>
      {chain}
    </span>
  )
}

function SideLog({ logs, connected }) {
  const listRef = useRef(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const seenIds = useRef(new Set())   // 已渲染过的 log id，不再加动画

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
    const ts = log.ts ? new Date(log.ts).toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }) : ''
    const isNew = !seenIds.current.has(log.id)
    if (isNew) seenIds.current.add(log.id)
    const enterCls = isNew ? 'card-pop' : ''
    const rowCls   = isNew ? 'log-item-enter' : ''

    // ── 买入卡片 ──────────────────────────────────────────────────────────────
    if (log.type === 'buy') {
      const d = log.data || {}
      const name = d.symbol || d.token_name || (d.ca ? d.ca.slice(0, 8) + '…' : '—')
      return (
        <div key={log.id} className={`rounded-xl border border-green-800/50 bg-green-900/15 px-3 py-2.5 space-y-1.5 ${enterCls}`}>
          {/* 头部：icon + 名称 + 链 + 时间 */}
          <div className="flex items-center gap-1.5 min-w-0">
            <TokenLogo url={d.logo_url} name={name} size={16} />
            <span className="text-xs font-bold text-green-400 shrink-0">买入</span>
            <span className="text-gray-100 font-semibold text-sm truncate flex-1">{name}</span>
            <ChainBadge chain={d.chain} />
            <span className="text-gray-600 text-xs shrink-0 tabular-nums">{ts}</span>
          </div>
          {/* 详情行 */}
          <div className="flex items-center gap-3 text-xs text-gray-400 pl-0.5 flex-wrap">
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
          {/* 路由标签 */}
          <div className="pl-0.5">
            <span className="inline-flex items-center gap-1 text-[10px] text-blue-400/80 bg-blue-900/20 border border-blue-800/30 px-1.5 py-0.5 rounded font-mono">
              ⚡ {d.route || 'AVE Trade'}
            </span>
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
          'rounded-xl border px-3 py-2.5 space-y-1.5', enterCls,
          profit ? 'border-green-800/50 bg-green-900/15' : 'border-red-900/50 bg-red-900/10'
        )}>
          {/* 头部 */}
          <div className="flex items-center gap-1.5 min-w-0">
            <TokenLogo url={d.logo_url} name={name} size={16} />
            <span className={clsx('text-xs font-bold shrink-0', profit ? 'text-green-400' : 'text-red-400')}>
              卖出
            </span>
            <span className="text-gray-100 font-semibold text-sm truncate flex-1">{name}</span>
            {/* 原因标签 */}
            <span className={clsx('px-1.5 py-0.5 rounded text-xs font-bold shrink-0', r.cls)}>
              {r.icon} {r.label}
            </span>
            <span className="text-gray-600 text-xs shrink-0 tabular-nums">{ts}</span>
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
              <span className="text-gray-500 text-xs tabular-nums">
                {multiple >= 1 ? `${multiple.toFixed(2)}x` : `${((multiple - 1) * 100).toFixed(0)}%`}
              </span>
            )}
            {/* Gas + 净盈亏 */}
            {hasGas && (
              <span className="ml-auto flex items-center gap-1.5 text-xs">
                <span className="text-orange-400/80">⛽{d.gas_fee_usd.toFixed(4)}U</span>
                <span className="text-gray-600">→</span>
                <span className={clsx('font-mono font-semibold', netPnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                  净{netPnl >= 0 ? '+' : ''}{netPnl?.toFixed(3)}U
                </span>
              </span>
            )}
          </div>
          {/* 价格对比行 */}
          <div className="flex items-center gap-1.5 text-xs text-gray-500 pl-0.5">
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
          {/* 路由标签 */}
          <div className="pl-0.5">
            <span className={clsx(
              'inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded font-mono border',
              d.route === 'PancakeSwap Direct'
                ? 'text-yellow-400/80 bg-yellow-900/20 border-yellow-800/30'
                : 'text-blue-400/80 bg-blue-900/20 border-blue-800/30'
            )}>
              ⚡ {d.route || 'AVE Trade'}
            </span>
          </div>
        </div>
      )
    }

    // ── 买入失败卡片 ──────────────────────────────────────────────────────────
    if (log.type === 'buy_failed') {
      const d = log.data || {}
      const name = d.token_name || (d.ca ? d.ca.slice(0, 8) + '…' : '—')
      return (
        <div key={log.id} className={`rounded-xl border border-red-800/60 bg-red-900/15 px-3 py-2.5 space-y-1.5 ${enterCls}`}>
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="text-xs font-bold text-red-400 shrink-0">❌ 买入失败</span>
            <span className="text-gray-100 font-semibold text-sm truncate flex-1">{name}</span>
            {d.tried_amounts
              ? <span className="text-gray-500 text-xs shrink-0">试: {d.tried_amounts}</span>
              : d.amount_usdt > 0 && <span className="text-gray-500 text-xs shrink-0">{d.amount_usdt}U</span>
            }
            <ChainBadge chain={d.chain} />
            <span className="text-gray-600 text-xs shrink-0 tabular-nums">{ts}</span>
          </div>
          <div className="text-xs text-red-300/90 leading-relaxed pl-0.5">
            {d.reason}
          </div>
          {d.error && (() => {
            const m = d.error.match(/status=(\d+)/)
            return m ? (
              <div className="text-xs text-gray-600 font-mono pl-0.5">
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
        <div key={log.id} className={`rounded-xl border px-3 py-2.5 space-y-1.5 ${enterCls} ${isAbandoned ? 'border-red-700/80 bg-red-950/30' : 'border-orange-800/60 bg-orange-900/10'}`}>
          <div className="flex items-center gap-1.5 min-w-0">
            <span className={`text-xs font-bold shrink-0 ${isAbandoned ? 'text-red-400' : 'text-orange-400'}`}>
              {isAbandoned ? '🚨 放弃卖出' : '⚠ 卖出失败'}
            </span>
            <span className="text-gray-100 font-semibold text-sm truncate flex-1">{name}</span>
            {triggerLabel && !isAbandoned && (
              <span className="text-xs text-orange-300/70 bg-orange-900/30 px-1.5 py-0.5 rounded shrink-0">
                触发{triggerLabel}
              </span>
            )}
            <ChainBadge chain={d.chain} />
            <span className="text-gray-600 text-xs shrink-0 tabular-nums">{ts}</span>
          </div>
          <div className="flex items-center gap-2 pl-0.5">
            <span className={`text-xs leading-relaxed flex-1 ${isAbandoned ? 'text-red-300' : 'text-orange-300/90'}`}>
              {d.reason}
            </span>
            {d.fail_count > 0 && (
              <span className="text-xs text-gray-600 shrink-0">累计失败 {d.fail_count} 次</span>
            )}
          </div>
          {d.error && (
            <div className="text-xs text-gray-600 font-mono break-all leading-relaxed pl-0.5 border-t border-orange-900/40 pt-1">
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
        <div key={log.id} className={`flex items-center gap-1.5 px-1 py-0.5 hover:bg-white/[0.02] rounded group ${rowCls}`}>
          <span className="text-gray-700 shrink-0 tabular-nums text-xs">{ts}</span>
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isFirst ? 'bg-blue-500/60' : 'bg-gray-600/60'}`} />
          {isFirst
            ? <span className="text-blue-400 text-xs font-bold shrink-0">新CA</span>
            : <span className="text-gray-500 text-xs font-bold shrink-0">第{pushCount}次</span>
          }
          {name
            ? <span className={`text-sm font-semibold truncate ${isFirst ? 'text-blue-200' : 'text-gray-400'}`}>{name}</span>
            : <span className="text-blue-400/60 text-xs font-mono truncate">{caShort}</span>
          }
          {name && <span className="text-gray-600 text-xs font-mono hidden group-hover:inline truncate">{caShort}</span>}
          {!isFirst && d.qwfc > 0 && (
            <span className={`text-xs shrink-0 tabular-nums ${isHot ? 'text-orange-400' : 'text-gray-600'}`}>
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
      <div key={log.id} className={`flex items-start gap-1.5 px-1 py-0.5 hover:bg-white/[0.02] rounded ${rowCls}`}>
        <span className="text-gray-700 shrink-0 tabular-nums text-xs mt-0.5">{ts}</span>
        <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0 mt-1', dotCls)} />
        <span className={clsx('text-xs break-all leading-relaxed', textCls)}>{msg}</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-dark-600 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-gray-300">实时日志</span>
          {/* 心跳波形 */}
          <HeartbeatLine active={connected} />
        </div>
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
            <span className="text-[10px] text-gray-600">{connected ? '已连接' : '断开'}</span>          </div>
        </div>
      </div>

      {/* 日志列表 */}
      <div
        ref={listRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-2 py-2 font-mono space-y-1 scroll-smooth"
      >
        {logs.length === 0 && (
          <div className="text-gray-600 text-center py-8 text-xs">等待事件...</div>
        )}
        {/* 正序显示，最新在底部 */}
        {[...logs].reverse().map(log => renderLog(log))}
      </div>
    </div>
  )
}

// ── 链颜色配置 ────────────────────────────────────────────────────
const CHAIN_CFG = {
  SOL:    { color: '#9945FF', dot: 'bg-purple-500', text: 'text-purple-300', dimText: 'text-purple-400/60' },
  BSC:    { color: '#F0B90B', dot: 'bg-yellow-400', text: 'text-yellow-300', dimText: 'text-yellow-400/60' },
  ETH:    { color: '#627EEA', dot: 'bg-blue-400',   text: 'text-blue-300',   dimText: 'text-blue-400/60'   },
  XLAYER: { color: '#00D4AA', dot: 'bg-teal-400',   text: 'text-teal-300',   dimText: 'text-teal-400/60'   },
}

// ── 移动端 Header 简要资产行 ──────────────────────────────────────
function MobileHeaderStats({ stats, posCount, connected }) {
  const [portfolio, setPortfolio] = useState(null)

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch('/api/analytics/portfolio')
        if (r.ok) setPortfolio(await r.json())
      } catch {}
    }
    load()
    const t = setInterval(load, 20000)
    return () => clearInterval(t)
  }, [])

  const todayPnl = stats ? ((stats.total_pnl_usdt ?? 0) - (stats.total_gas_usd ?? 0)) : null
  const totalAsset = portfolio
    ? portfolio.chains.reduce((s, c) => s + (Number(c.usdt_balance) || 0), 0) + (portfolio.total_position_value_usdt || 0)
    : null

  return (
    <>
      {totalAsset !== null && (
        <span className="flex flex-col items-center shrink-0">
          <span className="text-[9px] text-gray-600">总资产</span>
          <span className="text-xs font-mono font-bold text-yellow-400">${totalAsset.toFixed(2)}</span>
        </span>
      )}
      {todayPnl !== null && (
        <>
          <span className="w-px h-5 bg-dark-600 shrink-0" />
          <span className="flex flex-col items-center shrink-0">
            <span className="text-[9px] text-gray-600">净盈亏</span>
            <span className={clsx('text-xs font-mono font-bold', todayPnl >= 0 ? 'text-accent-green' : 'text-accent-red')}>
              {todayPnl >= 0 ? '+' : ''}{todayPnl.toFixed(2)}U
            </span>
          </span>
        </>
      )}
      {stats && (
        <>
          <span className="w-px h-5 bg-dark-600 shrink-0" />
          <span className="flex flex-col items-center shrink-0">
            <span className="text-[9px] text-gray-600">持仓</span>
            <span className={clsx('text-xs font-mono font-bold', posCount > 0 ? 'text-accent-yellow' : 'text-gray-500')}>{posCount}</span>
          </span>
          <span className="w-px h-5 bg-dark-600 shrink-0" />
          <span className="flex flex-col items-center shrink-0">
            <span className="text-[9px] text-gray-600">交易</span>
            <span className="text-xs font-mono font-bold text-gray-300">{stats.total_trades}</span>
          </span>
        </>
      )}
      {portfolio?.chains.map(c => {
        const cfg = CHAIN_CFG[c.chain] || { dot: 'bg-gray-500', text: 'text-gray-300' }
        if (c.usdt_balance == null) return null
        return (
          <span key={c.chain} className="flex flex-col items-center shrink-0">
            <span className="flex items-center gap-0.5">
              <span className={clsx('w-1 h-1 rounded-full', cfg.dot)} />
              <span className="text-[9px] text-gray-600">{c.chain}</span>
            </span>
            <span className={clsx('text-xs font-mono font-bold', cfg.text)}>{Number(c.usdt_balance).toFixed(1)}U</span>
          </span>
        )
      })}
    </>
  )
}

// ── Header 实时指标 ───────────────────────────────────────────────
function HeaderStats({ stats, posCount }) {
  const todayPnl = stats ? ((stats.total_pnl_usdt ?? 0) - (stats.total_gas_usd ?? 0)) : null
  const trades   = stats?.total_trades ?? null

  // 资产数据
  const [portfolio, setPortfolio]       = useState(null)
  const [assetLoading, setAssetLoading] = useState(false)

  const fetchPortfolio = useCallback(async () => {
    setAssetLoading(true)
    try {
      const r = await fetch('/api/analytics/portfolio')
      if (r.ok) setPortfolio(await r.json())
    } catch {}
    finally { setAssetLoading(false) }
  }, [])

  useEffect(() => {
    fetchPortfolio()
    const t = setInterval(fetchPortfolio, 15000)
    return () => clearInterval(t)
  }, [fetchPortfolio])

  const totalAsset = portfolio
    ? portfolio.chains.reduce((s, c) => s + (Number(c.usdt_balance) || 0), 0) + (portfolio.total_position_value_usdt || 0)
    : null

  const [assetStr, assetKey]  = useCountUp(totalAsset, v => v === null ? '—' : `$${v.toFixed(2)}`)
  const [pnlStr,   pnlKey]    = useCountUp(todayPnl,   v => v === null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}U`)
  const [tradeStr, tradeKey]  = useCountUp(trades,     v => v === null ? '—' : String(v))
  const [posStr,   posKey]    = useCountUp(posCount,   v => String(v))

  const Num = ({ val, rollKey, color }) => (
    <span key={rollKey} className={clsx('text-sm font-bold font-mono tabular-nums count-roll', color)}>
      {val}
    </span>
  )

  return (
    <div className="flex items-center gap-4">

      {/* ── 各链资产（直接平铺） ── */}
      {portfolio?.chains.map((c, i) => {
        const cfg = CHAIN_CFG[c.chain] || { dot: 'bg-gray-500', text: 'text-gray-300', dimText: 'text-gray-500' }
        const hasNative = c.native_balance !== null && c.native_balance !== undefined
        const hasUsdt   = c.usdt_balance   !== null && c.usdt_balance   !== undefined
        if (!hasNative && !hasUsdt) return null
        return (
          <div
            key={c.chain}
            className="flex flex-col items-center stat-enter"
            style={{ animationDelay: `${i * 60}ms` }}
          >
            <div className="flex items-center gap-1 leading-none mb-0.5">
              <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', cfg.dot,
                assetLoading ? 'animate-pulse' : ''
              )} />
              <span className={clsx('text-[10px] font-bold', cfg.dimText)}>{c.chain}</span>
            </div>
            <div className="flex flex-col items-center gap-px">
              {hasNative && (
                <span key={`${c.chain}-n-${c.native_balance}`}
                  className={clsx('text-xs font-mono tabular-nums leading-none count-roll', cfg.text)}>
                  {Number(c.native_balance).toFixed(3)}
                  <span className="text-[9px] text-gray-600 ml-0.5">{c.native_symbol}</span>
                </span>
              )}
              {hasUsdt && (
                <span key={`${c.chain}-u-${c.usdt_balance}`}
                  className="text-[11px] font-mono tabular-nums leading-none count-roll text-green-400">
                  {Number(c.usdt_balance).toFixed(2)}
                  <span className="text-[9px] text-gray-600 ml-0.5">U</span>
                </span>
              )}
            </div>
          </div>
        )
      })}

      {portfolio?.chains.some(c => c.native_balance !== null || c.usdt_balance !== null) && (
        <div className="w-px h-8 bg-dark-500" />
      )}

      {/* ── 总资产 ── */}
      <div className="flex flex-col items-center">
        <span className="text-[10px] text-gray-600 leading-none mb-0.5 flex items-center gap-1">
          总资产
          {assetLoading && <span className="w-1 h-1 rounded-full bg-yellow-500/80 animate-ping" />}
        </span>
        <Num val={assetStr} rollKey={assetKey} color="text-yellow-400" />
      </div>
      <div className="w-px h-6 bg-dark-500" />
      <div className="flex flex-col items-center">
        <span className="text-[10px] text-gray-600 leading-none mb-0.5">净盈亏</span>
        <Num val={pnlStr} rollKey={pnlKey} color={todayPnl === null ? 'text-gray-600' : todayPnl >= 0 ? 'text-accent-green' : 'text-accent-red'} />
      </div>
      <div className="w-px h-6 bg-dark-500" />
      <div className="flex flex-col items-center">
        <span className="text-[10px] text-gray-600 leading-none mb-0.5">总交易</span>
        <Num val={tradeStr} rollKey={tradeKey} color="text-gray-300" />
      </div>
      <div className="w-px h-6 bg-dark-500" />
      <div className="flex flex-col items-center">
        <span className="text-[10px] text-gray-600 leading-none mb-0.5">持仓</span>
        <Num val={posStr} rollKey={posKey} color={posCount > 0 ? 'text-accent-yellow' : 'text-gray-500'} />
      </div>
    </div>
  )
}

// ── 心跳波形 ─────────────────────────────────────────────────────
const BARS = [0.15, 0.4, 1, 0.6, 0.2, 0.8, 0.35, 0.9, 0.5, 0.15]
function HeartbeatLine({ active }) {
  if (!active) return <span className="text-[10px] text-gray-700">· · ·</span>
  return (
    <div className="flex items-end gap-px h-3.5">
      {BARS.map((h, i) => (
        <div
          key={i}
          className="w-px bg-accent-green rounded-full pulse-bar"
          style={{
            height: `${h * 100}%`,
            '--dur': `${0.6 + i * 0.1}s`,
            animationDelay: `${i * 0.07}s`,
          }}
        />
      ))}
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

// ── 信号流（喊单记录） ─────────────────────────────────────────────────────────
const CHAIN_BADGE = {
  bsc:    'text-yellow-400 bg-yellow-400/10',
  solana: 'text-purple-400 bg-purple-400/10',
  eth:    'text-blue-400 bg-blue-400/10',
  xlayer: 'text-cyan-400 bg-cyan-400/10',
}

function SignalFeed() {
  const [signals, setSignals] = useState([])
  const [loading, setLoading] = useState(true)
  const [collapsed, setCollapsed] = useState(false)
  const [limit, setLimit] = useState(5)
  const [refreshKey, setRefreshKey] = useState(0)
  const [fresh, setFresh] = useState(false)

  useEffect(() => {
    let alive = true
    const load = () => {
      getRecentSignals(60).then(d => {
        if (!alive) return
        setSignals(prev => {
          if (prev.length > 0 && d[0]?.id !== prev[0]?.id) {
            setFresh(true)
            setTimeout(() => setFresh(false), 800)
            setRefreshKey(k => k + 1)
          }
          return d
        })
        setLoading(false)
      }).catch(() => { if (alive) setLoading(false) })
    }
    load()
    const t = setInterval(load, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const fmt = (iso) => {
    const d = new Date(iso)
    const part = d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
    return part
  }

  const shown = signals.slice(0, limit)

  return (
    <div className="bg-dark-800 rounded-xl border border-dark-600 overflow-hidden">
      {/* 标题栏 */}
      <div
        className="flex items-center justify-between px-4 py-2.5 border-b border-dark-600"
      >
        <div className="flex items-center gap-2 cursor-pointer select-none" onClick={() => setCollapsed(v => !v)}>
          <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', fresh ? 'bg-accent-green animate-ping' : 'bg-gray-700')} />
          <span className="text-sm font-semibold text-gray-300">喊单信号流</span>
          <span className="text-xs text-gray-600">({signals.length})</span>
          <span className={clsx('text-gray-600 text-xs transition-transform', collapsed ? '' : 'rotate-180')}>▼</span>
        </div>
        {/* 显示条数控制 */}
        {!collapsed && (
          <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
            <span className="text-xs text-gray-600">显示</span>
            {[5, 10, 20, 50].map(n => (
              <button
                key={n}
                onClick={() => setLimit(n)}
                className={clsx(
                  'text-xs px-2 py-0.5 rounded border transition-colors',
                  limit === n
                    ? 'border-accent-blue text-accent-blue bg-accent-blue/10'
                    : 'border-dark-500 text-gray-600 hover:text-gray-300 hover:border-gray-500'
                )}
              >{n}</button>
            ))}
          </div>
        )}
      </div>

      {!collapsed && (
        loading ? (
          <div className="text-center text-gray-600 py-4 text-xs">加载中...</div>
        ) : signals.length === 0 ? (
          <div className="text-center text-gray-600 py-4 text-xs">暂无信号</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-600 border-b border-dark-700">
                  <th className="text-left px-2 md:px-4 py-2 font-medium">时间</th>
                  <th className="text-left px-2 py-2 font-medium hidden sm:table-cell">社区</th>
                  <th className="text-left px-2 py-2 font-medium">喊单人</th>
                  <th className="text-right px-2 py-2 font-medium hidden sm:table-cell">
                    <span title="来源方自报胜率，定义不透明，仅供参考" className="cursor-help border-b border-dashed border-gray-600">胜率*</span>
                  </th>
                  <th className="text-left px-2 py-2 font-medium">链</th>
                  <th className="text-left px-2 py-2 font-medium">代币</th>
                  <th className="text-left px-2 py-2 font-medium hidden md:table-cell">合约</th>
                  <th className="text-center px-2 py-2 font-medium hidden sm:table-cell">第几次</th>
                  <th className="text-center px-2 py-2 font-medium">过滤</th>
                  <th className="text-center px-2 md:px-4 py-2 font-medium">买入</th>
                </tr>
              </thead>
              <tbody key={refreshKey}>
                {shown.map((s, i) => {
                  const chainCls = CHAIN_BADGE[s.chain?.toLowerCase()] || 'text-gray-400 bg-gray-400/10'
                  const isPassed = s.filter_passed
                  const isBought = s.bought
                  const rowStyle = {
                    animationDelay: `${i * 30}ms`,
                    ...(isBought ? { backgroundColor: 'rgba(0,255,135,0.05)' } :
                      i === 0 ? { backgroundColor: 'rgba(0,255,135,0.08)', borderLeft: '2px solid rgba(0,255,135,0.7)' } :
                      i === 1 ? { backgroundColor: 'rgba(59,130,246,0.10)', borderLeft: '2px solid rgba(59,130,246,0.6)' } :
                      i === 2 ? { backgroundColor: 'rgba(168,85,247,0.08)', borderLeft: '2px solid rgba(168,85,247,0.5)' } : {})
                  }
                  return (
                    <tr key={s.id}
                      className="border-b border-dark-700/50 hover:brightness-110 transition-all log-item-enter"
                      style={rowStyle}
                    >
                      <td className="px-2 md:px-4 py-1.5 font-mono text-gray-500 whitespace-nowrap">{fmt(s.received_at)}</td>
                      <td className="px-2 py-1.5 hidden sm:table-cell">
                        {s.group_id
                          ? <span className="font-mono text-blue-400/80 bg-blue-900/20 px-1.5 py-0.5 rounded text-[11px]">社区#{s.group_id}</span>
                          : <span className="text-gray-700">—</span>}
                      </td>
                      <td className="px-2 py-1.5">
                        {s.sender_id
                          ? <span className="font-mono text-orange-400/80 bg-orange-900/20 px-1.5 py-0.5 rounded text-[11px]">#{s.sender_id}</span>
                          : <span className="text-gray-700">—</span>}
                      </td>
                      <td className="px-2 py-1.5 text-right hidden sm:table-cell">
                        {s.sender_win_rate != null
                          ? <span className={clsx('font-mono text-[11px]',
                              s.sender_win_rate >= 70 ? 'text-green-400' :
                              s.sender_win_rate >= 50 ? 'text-yellow-400' : 'text-red-400'
                            )}>{s.sender_win_rate}%</span>
                          : <span className="text-gray-700">—</span>}
                      </td>
                      <td className="px-2 py-1.5">
                        <span className={clsx('px-1.5 py-0.5 rounded font-bold text-[11px]', chainCls)}>
                          {s.chain?.toUpperCase()}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-gray-300 max-w-[60px] md:max-w-[80px] truncate" title={s.symbol}>{s.symbol || '—'}</td>
                      <td className="px-2 py-1.5 font-mono text-gray-600 text-[11px] hidden md:table-cell">
                        <span title={s.ca}>{s.ca.slice(0, 6)}…{s.ca.slice(-4)}</span>
                      </td>
                      <td className="px-2 py-1.5 text-center hidden sm:table-cell">
                        {s.push_count > 1
                          ? <span className={s.push_count >= 5 ? 'text-red-400 font-bold' : 'text-orange-400 font-bold'}>×{s.push_count}</span>
                          : <span className="text-gray-600">首次</span>}
                      </td>
                      <td className="px-2 py-1.5 text-center">
                        {isPassed
                          ? <span className="text-green-400">✓</span>
                          : <span className="text-gray-700">✗</span>}
                      </td>
                      <td className="px-2 md:px-4 py-1.5 text-center">
                        {isBought
                          ? <span className="text-green-400 font-bold">已买</span>
                          : <span className="text-gray-700">—</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  )
}

// ── 时段徽章 ──────────────────────────────────────────────────────
const PERIOD_BADGE_LABEL = { hour: '时', day: '天', week: '周', month: '月', year: '年', all: '全' }
const PERIOD_BADGE_COLOR = {
  hour:  'text-cyan-400 bg-cyan-400/10 border-cyan-400/30',
  day:   'text-green-400 bg-green-400/10 border-green-400/30',
  week:  'text-blue-400 bg-blue-400/10 border-blue-400/30',
  month: 'text-purple-400 bg-purple-400/10 border-purple-400/30',
  year:  'text-orange-400 bg-orange-400/10 border-orange-400/30',
  all:   'text-gray-500 bg-gray-500/10 border-gray-500/20',
}
function PeriodBadge({ period }) {
  if (!period || period === 'all') return null
  return (
    <span className={clsx('text-[10px] px-1.5 py-0.5 rounded-full border font-semibold', PERIOD_BADGE_COLOR[period] || PERIOD_BADGE_COLOR.all)}>
      {PERIOD_BADGE_LABEL[period] || period}
    </span>
  )
}

// ── 总交易次数富卡片 ──────────────────────────────────────────────
function TradeCountCard({ stats, index, period = 'all' }) {
  const total   = stats?.total_trades ?? 0
  const wins    = stats?.win_trades   ?? 0
  const losses  = stats?.loss_trades  ?? 0
  const winRate = stats?.win_rate     ?? 0
  const [totalStr, rollKey] = useCountUp(total, v => String(v))

  // 环形进度：胜率
  const R = 18, C = 2 * Math.PI * R
  const dash = stats ? (winRate / 100) * C : 0

  return (
    <div className="bg-dark-800 rounded-xl border border-dark-600 p-4 stat-enter" style={{ animationDelay: `${index * 80}ms` }}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-500">总交易次数</span>
        <PeriodBadge period={period} />
      </div>
      <div className="flex items-center gap-3">
        {/* 环形进度 */}
        <svg width="44" height="44" className="shrink-0 -ml-1">
          <circle cx="22" cy="22" r={R} fill="none" stroke="#1e1e2e" strokeWidth="4" />
          <circle cx="22" cy="22" r={R} fill="none" stroke={winRate >= 50 ? '#00ff87' : winRate >= 30 ? '#facc15' : '#ff4466'}
            strokeWidth="4" strokeDasharray={`${dash} ${C}`} strokeDashoffset={C / 4}
            strokeLinecap="round"
            style={{ transition: 'stroke-dasharray 1s cubic-bezier(0.22,1,0.36,1)' }}
          />
          <text x="22" y="22" textAnchor="middle" dominantBaseline="central"
            style={{ fontSize: 9, fill: '#9ca3af', fontFamily: 'monospace' }}>
            {winRate}%
          </text>
        </svg>
        <div>
          <div key={rollKey} className="text-2xl font-bold font-mono text-white count-roll tabular-nums">{totalStr}</div>
          <div className="text-[11px] text-gray-500">胜率</div>
        </div>
      </div>
      {/* 胜/负/投入 小格 */}
      <div className="flex gap-2 mt-2 text-[11px]">
        <span className="flex items-center gap-0.5 text-green-400"><span className="w-1.5 h-1.5 rounded-full bg-green-400 inline-block" />{wins}胜</span>
        <span className="flex items-center gap-0.5 text-red-400"><span className="w-1.5 h-1.5 rounded-full bg-red-400 inline-block" />{losses}负</span>
        <span className="text-gray-600 ml-auto">{stats?.total_invested?.toFixed(1) ?? 0}U投</span>
      </div>
      {/* 胜率进度条 */}
      <div className="mt-1.5 h-1 bg-dark-600 rounded-full overflow-hidden">
        <div className="h-full rounded-full bar-fill transition-all duration-1000"
          key={rollKey}
          style={{
            width: `${Math.min(winRate, 100)}%`,
            backgroundColor: winRate >= 50 ? '#00ff87' : winRate >= 30 ? '#facc15' : '#ff4466',
          }} />
      </div>
    </div>
  )
}

// ── 总盈亏富卡片 ──────────────────────────────────────────────────
function PnlCard({ stats, index, period = 'all' }) {
  const pnl     = stats?.total_pnl_usdt ?? 0
  const gas     = stats?.total_gas_usd  ?? 0
  const net     = pnl - gas
  const wins    = stats?.win_trades     ?? 0
  const losses  = stats?.loss_trades    ?? 0
  const [netStr, rollKey] = useCountUp(net, v => (v >= 0 ? '+' : '') + v.toFixed(3) + 'U')
  const isPos   = net >= 0

  const avgWin  = stats?.avg_win  != null ? stats.avg_win.toFixed(3)          : '—'
  const avgLoss = stats?.avg_loss != null ? Math.abs(stats.avg_loss).toFixed(3) : '—'

  const absPnl  = Math.abs(pnl)
  const gasPct  = absPnl > 0 ? Math.min((gas / (absPnl + gas)) * 100, 100) : 0

  return (
    <div className="bg-dark-800 rounded-xl border border-dark-600 p-4 stat-enter" style={{ animationDelay: `${index * 80}ms` }}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-500">总盈亏(含Gas)</span>
        <PeriodBadge period={period} />
      </div>
      <div key={rollKey} className={clsx('text-2xl font-bold font-mono count-roll tabular-nums', isPos ? 'text-accent-green' : 'text-accent-red')}>
        {netStr}
      </div>
      {/* 交易P&L vs Gas 拆解 */}
      <div className="mt-2 space-y-1">
        <div className="flex justify-between text-[11px]">
          <span className="text-gray-500">交易P&L</span>
          <span className={pnl >= 0 ? 'text-green-400 font-mono' : 'text-red-400 font-mono'}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(3)}U</span>
        </div>
        <div className="flex justify-between text-[11px]">
          <span className="text-gray-500">Gas消耗</span>
          <span className="text-orange-400 font-mono">-{gas.toFixed(3)}U</span>
        </div>
        {/* P&L vs Gas 占比条 */}
        <div className="h-1 bg-dark-600 rounded-full overflow-hidden flex">
          <div key={rollKey} className="h-full rounded-l-full bar-fill"
            style={{ width: `${100 - gasPct}%`, backgroundColor: pnl >= 0 ? '#00ff87' : '#ff4466', opacity: 0.8 }} />
          <div className="h-full rounded-r-full bg-orange-500/60"
            style={{ width: `${gasPct}%` }} />
        </div>
        <div className="flex justify-between text-[10px] text-gray-600">
          <span>均盈 +{avgWin}U</span>
          <span>均亏 -{avgLoss}U</span>
        </div>
      </div>
    </div>
  )
}

// ── 当前持仓富卡片 ────────────────────────────────────────────────
function PositionCard({ stats, posCount, index }) {
  const [positions, setPositions] = useState([])

  useEffect(() => {
    let alive = true
    const load = () => getPositions().then(d => { if (alive) setPositions(d) }).catch(() => {})
    load()
    const t = setInterval(load, 8000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const [cntStr, cntKey] = useCountUp(posCount, v => String(v))

  // 按链分组计数
  const byChain = positions.reduce((acc, p) => {
    acc[p.chain] = (acc[p.chain] || 0) + 1
    return acc
  }, {})

  // 浮动盈亏
  const floatPnl    = positions.reduce((s, p) => s + (p.pnl_usdt || 0), 0)
  const floatInvest = positions.reduce((s, p) => s + (p.amount_usdt || 0), 0)
  const isPos = floatPnl >= 0

  // 各仓盈亏小条（最多显示5个）
  const topPos = [...positions].sort((a, b) => Math.abs(b.pnl_pct) - Math.abs(a.pnl_pct)).slice(0, 5)

  const CHAIN_DOT = { SOL: 'bg-purple-400', BSC: 'bg-yellow-400', ETH: 'bg-blue-400', XLAYER: 'bg-teal-400' }

  return (
    <div className="bg-dark-800 rounded-xl border border-dark-600 p-4 stat-enter" style={{ animationDelay: `${index * 80}ms` }}>
      <div className="text-xs text-gray-500 mb-1">当前持仓</div>

      {/* 主数字 + 浮盈 */}
      <div className="flex items-baseline gap-2 mb-2">
        <span key={cntKey} className="text-2xl font-bold font-mono text-yellow-400 count-roll tabular-nums">{cntStr}</span>
        <span className="text-xs text-gray-500">笔</span>
        {posCount > 0 && (
          <span className={clsx('text-xs font-mono ml-auto', isPos ? 'text-green-400' : 'text-red-400')}>
            {isPos ? '+' : ''}{floatPnl.toFixed(3)}U
          </span>
        )}
      </div>

      {posCount === 0 ? (
        <div className="text-xs text-gray-600 text-center py-2">暂无持仓</div>
      ) : (
        <>
          {/* 各仓 pnl 迷你条 */}
          <div className="space-y-1 mb-2">
            {topPos.map(p => {
              const sym = p.symbol || p.ca?.slice(0, 6) + '…'
              const pct = p.pnl_pct || 0
              const barW = Math.min(Math.abs(pct) / 50 * 100, 100)
              return (
                <div key={p.id} className="flex items-center gap-1.5">
                  <span className="text-[10px] text-gray-500 w-10 truncate shrink-0">{sym}</span>
                  <div className="flex-1 h-1 bg-dark-600 rounded-full overflow-hidden">
                    <div className="h-full rounded-full"
                      style={{
                        width: `${barW}%`,
                        backgroundColor: pct >= 0 ? '#00ff87' : '#ff4466',
                        opacity: 0.8,
                        transition: 'width 0.6s ease',
                      }}
                    />
                  </div>
                  <span className={clsx('text-[10px] font-mono tabular-nums w-10 text-right shrink-0',
                    pct >= 0 ? 'text-green-400' : 'text-red-400')}>
                    {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                  </span>
                </div>
              )
            })}
          </div>

          {/* 按链分布 + 投入 */}
          <div className="flex items-center gap-2 pt-1 border-t border-dark-600/60">
            <div className="flex items-center gap-1.5 flex-1">
              {Object.entries(byChain).map(([chain, cnt]) => (
                <span key={chain} className="flex items-center gap-0.5 text-[10px] text-gray-400">
                  <span className={clsx('w-1.5 h-1.5 rounded-full', CHAIN_DOT[chain] || 'bg-gray-500')} />
                  {chain} ×{cnt}
                </span>
              ))}
            </div>
            <span className="text-[10px] text-gray-600 shrink-0">{floatInvest.toFixed(2)}U投</span>
          </div>
        </>
      )}
    </div>
  )
}

// ── Gas 消耗富卡片 ────────────────────────────────────────────────
function GasCard({ stats, index, period = 'all' }) {
  const total   = stats?.total_gas_usd   ?? null
  const trades  = stats?.total_trades    ?? 0
  const avgGas  = trades > 0 && total !== null ? total / trades : null
  const pnl     = stats?.total_pnl_usdt  ?? 0
  const net     = pnl - (total ?? 0)

  // Gas 占 PnL 比例（Gas效率）
  const gasRatio = pnl > 0 && total !== null ? Math.min((total / pnl) * 100, 100) : 0
  // Gas vs 净收入 色彩
  const efficient = gasRatio < 20  // Gas < 20% of PnL = 高效

  const [totalStr, totalKey] = useCountUp(total, v => v === null ? '—' : `${v.toFixed(3)}U`)

  // 模拟最近几笔 gas 迷你折线（用 avg 近似，实际可扩展）
  const [gasHistory] = useState(() => Array.from({ length: 8 }, () => 0.002 + Math.random() * 0.008))

  const miniMax = Math.max(...gasHistory, 0.001)
  const points = gasHistory.map((v, i) => {
    const x = (i / (gasHistory.length - 1)) * 60
    const y = 16 - (v / miniMax) * 14
    return `${x},${y}`
  }).join(' ')

  return (
    <div className="bg-dark-800 rounded-xl border border-dark-600 p-4 stat-enter" style={{ animationDelay: `${index * 80}ms` }}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-500">Gas 消耗</span>
        <PeriodBadge period={period} />
      </div>

      {/* 主数字 + 趋势迷你折线 */}
      <div className="flex items-start justify-between mb-2">
        <div>
          <span key={totalKey} className="text-2xl font-bold font-mono text-orange-400 count-roll tabular-nums">
            ~{totalStr}
          </span>
        </div>
        {/* 迷你折线 */}
        <svg width="64" height="18" viewBox="0 0 64 18" className="mt-0.5 opacity-60">
          <polyline points={points} fill="none" stroke="#f97316" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx={60} cy={gasHistory.length > 1 ? 16 - (gasHistory[gasHistory.length-1] / miniMax) * 14 : 8}
            r="2" fill="#f97316" opacity="0.9" />
        </svg>
      </div>

      {/* 指标格子 */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] mb-2">
        <div className="flex justify-between">
          <span className="text-gray-600">均Gas/笔</span>
          <span className="font-mono text-orange-300">{avgGas !== null ? avgGas.toFixed(4) + 'U' : '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-600">净收益</span>
          <span className={clsx('font-mono', net >= 0 ? 'text-green-400' : 'text-red-400')}>
            {net >= 0 ? '+' : ''}{net.toFixed(3)}U
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-600">Gas/PnL</span>
          <span className={clsx('font-mono', efficient ? 'text-green-400' : gasRatio < 50 ? 'text-yellow-400' : 'text-red-400')}>
            {pnl > 0 ? gasRatio.toFixed(1) + '%' : '—'}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-600">笔数</span>
          <span className="font-mono text-gray-400">{trades}</span>
        </div>
      </div>

      {/* Gas效率条 */}
      <div className="space-y-0.5">
        <div className="flex justify-between text-[10px] text-gray-600">
          <span>Gas效率</span>
          <span className={efficient ? 'text-green-400' : 'text-yellow-400'}>{efficient ? '高效' : '偏高'}</span>
        </div>
        <div className="h-1 bg-dark-600 rounded-full overflow-hidden">
          <div
            key={totalKey}
            className="h-full rounded-full bar-fill"
            style={{
              width: `${Math.min(gasRatio, 100)}%`,
              backgroundColor: gasRatio < 20 ? '#00ff87' : gasRatio < 50 ? '#facc15' : '#ff4466',
            }}
          />
        </div>
      </div>
    </div>
  )
}

// ── 胜/负富卡片 ──────────────────────────────────────────────────
function WinLossCard({ stats, index, period = 'all' }) {
  const wins   = stats?.win_trades   ?? 0
  const losses = stats?.loss_trades  ?? 0
  const total  = stats?.total_trades ?? 0
  const invested = stats?.total_invested ?? 0

  // 新字段（后端已扩展）
  const avgWin      = stats?.avg_win        ?? null
  const avgLoss     = stats?.avg_loss       ?? null
  const maxWin      = stats?.max_win        ?? null
  const maxLoss     = stats?.max_loss       ?? null
  const pf          = stats?.profit_factor  ?? null
  const bestStreak  = stats?.best_streak    ?? 0
  const worstStreak = stats?.worst_streak   ?? 0

  const [wStr, wKey] = useCountUp(wins,   v => String(v))
  const [lStr, lKey] = useCountUp(losses, v => String(v))

  // 胜负堆叠条比例
  const winPct  = total > 0 ? (wins  / total) * 100 : 0
  const lossPct = total > 0 ? (losses / total) * 100 : 0

  return (
    <div className="bg-dark-800 rounded-xl border border-dark-600 p-4 stat-enter" style={{ animationDelay: `${index * 80}ms` }}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500">胜 / 负</span>
        <PeriodBadge period={period} />
      </div>

      {/* 主数字：胜/负 大字 */}
      <div className="flex items-baseline gap-1.5 mb-2">
        <span key={wKey} className="text-2xl font-bold font-mono text-green-400 count-roll tabular-nums">{wStr}</span>
        <span className="text-gray-600 text-lg">/</span>
        <span key={lKey} className="text-2xl font-bold font-mono text-red-400 count-roll tabular-nums">{lStr}</span>
      </div>

      {/* 胜/负堆叠条 */}
      <div className="h-1.5 bg-dark-600 rounded-full overflow-hidden flex mb-2">
        <div
          key={`w-${wins}`}
          className="h-full rounded-l-full bar-fill"
          style={{ width: `${winPct}%`, backgroundColor: '#00ff87', opacity: 0.85 }}
        />
        <div
          className="h-full rounded-r-full"
          style={{ width: `${lossPct}%`, backgroundColor: '#ff4466', opacity: 0.75 }}
        />
      </div>

      {/* 指标网格 */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <div className="flex justify-between">
          <span className="text-gray-600">均盈</span>
          <span className="font-mono text-green-400">{avgWin !== null ? `+${avgWin.toFixed(3)}U` : '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-600">均亏</span>
          <span className="font-mono text-red-400">{avgLoss !== null ? `${avgLoss.toFixed(3)}U` : '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-600">最大盈</span>
          <span className="font-mono text-green-300">{maxWin !== null ? `+${maxWin.toFixed(3)}U` : '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-600">最大亏</span>
          <span className="font-mono text-red-300">{maxLoss !== null ? `${maxLoss.toFixed(3)}U` : '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-600">盈利因子</span>
          <span className={clsx('font-mono', pf === null ? 'text-gray-600' : pf >= 1.5 ? 'text-green-400' : pf >= 1 ? 'text-yellow-400' : 'text-red-400')}>
            {pf !== null ? pf.toFixed(2) : '—'}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-600">投入</span>
          <span className="font-mono text-gray-400">{invested.toFixed(1)}U</span>
        </div>
      </div>

      {/* 连胜/连败标签 */}
      {(bestStreak > 0 || worstStreak > 0) && (
        <div className="flex gap-2 mt-2 pt-1.5 border-t border-dark-600/60">
          {bestStreak > 0 && (
            <span className="flex items-center gap-1 text-[11px] text-green-400/80">
              <span className="text-[10px]">🔥</span>最长连胜 {bestStreak}
            </span>
          )}
          {worstStreak > 0 && (
            <span className="flex items-center gap-1 text-[11px] text-red-400/70 ml-auto">
              最长连败 {worstStreak}<span className="text-[10px]">💀</span>
            </span>
          )}
        </div>
      )}
    </div>
  )
}

// ── MEME信号总览卡片 ──────────────────────────────────────────────
const PERIOD_LABELS = { hour: '时', day: '天', week: '周', month: '月', year: '年' }

function SignalOverviewCard() {
  const [period, setPeriod] = useState('day')
  const [data, setData] = useState(null)
  const [animKey, setAnimKey] = useState(0)
  // 实时跳动波形：记录每次刷新时 total 的增量，保留最近 30 个点
  const [liveWave, setLiveWave] = useState([])
  const prevTotal = useRef(null)

  useEffect(() => {
    let alive = true
    const load = () => {
      getSignalOverview(period).then(d => {
        if (!alive) return
        setData(prev => {
          // 计算增量，追加到波形
          const delta = prev ? Math.max(0, d.total - prev.total) : 0
          setLiveWave(w => {
            const next = [...w, delta]
            return next.length > 30 ? next.slice(-30) : next
          })
          if (!prev || d.total !== prev.total) setAnimKey(k => k + 1)
          return d
        })
      }).catch(() => {})
    }
    // 首次加载用 series 数据初始化波形
    getSignalOverview(period).then(d => {
      if (!alive) return
      setData(d)
      setAnimKey(k => k + 1)
      // 用历史 series 的最后 30 个 bucket cnt 初始化波形
      const init = (d.series || []).slice(-30).map(s => s.cnt)
      setLiveWave(init)
      prevTotal.current = d.total
    }).catch(() => {})
    const t = setInterval(load, 3000)
    return () => { alive = false; clearInterval(t) }
  }, [period])

  // 实时跳动波形 SVG
  const LiveWave = ({ vals, color }) => {
    if (!vals?.length) return null
    const w = 100, h = 40
    const max = Math.max(...vals, 1)
    // 平滑曲线用 cubic bezier
    const points = vals.map((v, i) => ({
      x: (i / (vals.length - 1 || 1)) * w,
      y: h - (v / max) * (h - 4) - 2,
    }))
    let d = `M ${points[0].x} ${points[0].y}`
    for (let i = 1; i < points.length; i++) {
      const p = points[i - 1], c = points[i]
      const mx = (p.x + c.x) / 2
      d += ` C ${mx} ${p.y} ${mx} ${c.y} ${c.x} ${c.y}`
    }
    const area = d + ` L ${w} ${h} L 0 ${h} Z`
    // 最后一个点高亮脉冲
    const last = points[points.length - 1]
    return (
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ height: 40 }} preserveAspectRatio="none">
        <defs>
          <linearGradient id="lwg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.4" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#lwg)" />
        <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        {/* 最新点脉冲圆点 */}
        <circle cx={last.x} cy={last.y} r="2.5" fill={color} opacity="0.9" />
        <circle cx={last.x} cy={last.y} r="5" fill="none" stroke={color} strokeWidth="1" opacity="0.4">
          <animate attributeName="r" values="3;8;3" dur="2s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.6;0;0.6" dur="2s" repeatCount="indefinite" />
        </circle>
      </svg>
    )
  }

  return (
    <div className="bg-dark-800 rounded-xl border border-dark-600 p-3 h-full stat-enter flex flex-col gap-2">
      {/* 标题 + 时段切换 */}
      <div className="flex items-start justify-between gap-1">
        <div className="text-xs text-gray-500 leading-tight">收到MEME信号</div>
        <div className="flex gap-0.5 shrink-0">
          {Object.entries(PERIOD_LABELS).map(([k, v]) => (
            <button
              key={k}
              onClick={() => setPeriod(k)}
              className={clsx(
                'text-[10px] px-1 py-0.5 rounded transition-colors',
                period === k ? 'bg-accent-blue/30 text-accent-blue' : 'text-gray-600 hover:text-gray-400'
              )}
            >{v}</button>
          ))}
        </div>
      </div>

      {data ? (
        <>
          {/* 核心数字 */}
          <div key={animKey} className="count-roll">
            <div className="text-2xl font-bold font-mono text-white tabular-nums">{data.total.toLocaleString()}</div>
            <div className="text-[11px] text-gray-500">{data.unique_ca} 个币种</div>
          </div>

          {/* 实时跳动波形 */}
          <div className="flex-1 min-h-0">
            <LiveWave vals={liveWave} color="#3b82f6" />
          </div>

          {/* 底部指标行 */}
          <div className="grid grid-cols-3 gap-1 text-center border-t border-dark-600 pt-1.5">
            <div>
              <div className="text-[10px] text-gray-600">过滤通过</div>
              <div className="text-xs font-mono text-green-400">{data.pass_rate}%</div>
            </div>
            <div>
              <div className="text-[10px] text-gray-600">买入率</div>
              <div className="text-xs font-mono text-yellow-400">{data.buy_rate}%</div>
            </div>
            <div>
              <div className="text-[10px] text-gray-600">已买入</div>
              <div className="text-xs font-mono text-accent-blue">{data.bought}</div>
            </div>
          </div>
        </>
      ) : (
        <div className="flex-1 flex items-center justify-center text-gray-700 text-xs">加载中...</div>
      )}
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
            <div key={t.id} className="flex items-center gap-1.5 md:gap-2 group">
              {/* 图标 + 代币名 */}
              <span className="text-[10px] shrink-0">{icon}</span>
              <span className="text-xs text-gray-300 w-16 md:w-20 shrink-0 truncate" title={display}>{display}</span>
              {/* 进度条 */}
              <div className="flex-1 h-1.5 bg-dark-600 rounded-full overflow-hidden">
                <div className="h-full bg-orange-500/60 rounded-full bar-fill" style={{ width: `${barPct}%`, animationDuration: '1s' }} />
              </div>
              {/* Gas 金额 */}
              <span className="font-mono text-orange-400 text-[11px] w-12 md:w-16 text-right shrink-0">
                {t.gas_fee_usd.toFixed(3)}U
              </span>
              {/* 净盈亏 */}
              <span className={clsx('font-mono text-[11px] w-12 md:w-16 text-right shrink-0', netPnl >= 0 ? 'text-accent-green' : 'text-red-400')}>
                {netPnl >= 0 ? '+' : ''}{netPnl.toFixed(3)}U
              </span>
              {/* 原因标签 — 移动端隐藏 */}
              <span className="text-gray-600 text-[10px] w-8 text-right shrink-0 hidden sm:block">{zh}</span>
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
