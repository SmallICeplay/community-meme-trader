import { useState, useEffect, useRef, useCallback } from 'react'
import { clsx } from 'clsx'
import { getAiConfig, saveAiConfig, sendAiChat } from '../api'

// ── 简单 Markdown 渲染（加粗/代码/换行）────────────────────────────
function MdText({ text }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\n)/)
  return (
    <span>
      {parts.map((p, i) => {
        if (p.startsWith('**') && p.endsWith('**'))
          return <strong key={i} className="text-white font-semibold">{p.slice(2, -2)}</strong>
        if (p.startsWith('`') && p.endsWith('`'))
          return <code key={i} className="bg-dark-600 text-green-300 px-1 rounded text-[10px] font-mono">{p.slice(1, -1)}</code>
        if (p === '\n') return <br key={i} />
        return p
      })}
    </span>
  )
}

// ── 设置面板（精简版，Key 配置统一到配置页）────────────────────────
function AiSettings({ onClose, onSaved }) {
  const [cfg, setCfg]       = useState(null)
  const [saving, setSaving] = useState(false)
  const [maxTokens, setMaxTokens] = useState(1024)
  const [temp, setTemp]     = useState(0.7)
  const [enabled, setEnabled] = useState(false)

  useEffect(() => {
    getAiConfig().then(d => {
      setCfg(d)
      setEnabled(d.enabled || false)
      setMaxTokens(d.max_tokens || 1024)
      setTemp(d.temperature || 0.7)
    }).catch(() => {})
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      await saveAiConfig({ enabled, max_tokens: maxTokens, temperature: temp })
      onSaved?.()
      onClose()
    } catch {}
    finally { setSaving(false) }
  }

  return (
    <div className="p-3 space-y-3 text-xs">
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-semibold text-gray-200">AI 快捷设置</span>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300">✕</button>
      </div>

      {/* 启用开关 */}
      <label className="flex items-center gap-2 cursor-pointer">
        <div
          onClick={() => setEnabled(v => !v)}
          className={clsx('w-8 h-4 rounded-full relative transition-colors', enabled ? 'bg-accent-blue' : 'bg-dark-500')}
        >
          <div className={clsx('absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all', enabled ? 'left-4' : 'left-0.5')} />
        </div>
        <span className="text-gray-400">启用 AI 对话</span>
      </label>

      {/* 用量展示 */}
      {cfg && (
        <div className="p-2 rounded bg-dark-700 border border-dark-600 space-y-1">
          {cfg.use_builtin && cfg.builtin_available ? (
            <>
              <div className="text-gray-400">内置 Key · 今日剩余
                <span className={clsx('ml-1 font-mono font-semibold',
                  cfg.builtin_remaining > 10 ? 'text-green-400' :
                  cfg.builtin_remaining > 0  ? 'text-yellow-400' : 'text-red-400'
                )}>
                  {cfg.builtin_remaining}/{cfg.builtin_daily_limit}
                </span> 次
              </div>
              <div className="w-full bg-dark-600 rounded-full h-1 overflow-hidden">
                <div
                  className={clsx('h-full rounded-full', cfg.builtin_remaining > 10 ? 'bg-green-400' : cfg.builtin_remaining > 0 ? 'bg-yellow-400' : 'bg-red-400')}
                  style={{ width: `${Math.min(100, (cfg.builtin_used_today / cfg.builtin_daily_limit) * 100)}%` }}
                />
              </div>
            </>
          ) : (
            <div className="text-gray-400">
              {cfg.api_key_set
                ? <span>自定义 Key 已配置 <span className="text-green-400">✓</span></span>
                : <span className="text-yellow-400">⚠ 未配置 Key</span>
              }
            </div>
          )}
        </div>
      )}

      {/* 引导到配置页 */}
      <div className="text-[10px] text-gray-500 bg-dark-700 border border-dark-600 rounded px-2 py-1.5">
        API Key、模型选择、内置Key开关 → 请前往 <span className="text-accent-blue">配置</span> 页面 → AI 助手接口
      </div>

      {/* 高级参数 */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-gray-500 block mb-1">Max Tokens</label>
          <input
            type="number" min={128} max={8192}
            value={maxTokens}
            onChange={e => setMaxTokens(Number(e.target.value))}
            className="w-full bg-dark-700 border border-dark-500 text-gray-200 rounded px-2 py-1.5 text-xs"
          />
        </div>
        <div>
          <label className="text-gray-500 block mb-1">Temperature</label>
          <input
            type="number" min={0} max={2} step={0.1}
            value={temp}
            onChange={e => setTemp(Number(e.target.value))}
            className="w-full bg-dark-700 border border-dark-500 text-gray-200 rounded px-2 py-1.5 text-xs"
          />
        </div>
      </div>

      <button
        onClick={save}
        disabled={saving}
        className="w-full py-1.5 rounded bg-accent-blue/80 hover:bg-accent-blue text-white text-xs font-semibold transition-colors"
      >
        {saving ? '保存中...' : '保存'}
      </button>
    </div>
  )
}

// ── 主对话框组件 ──────────────────────────────────────────────────
const WELCOME_MSG = `你好！我是 Hodlo.AI 交易助手 🤖

我实时掌握以下数据，可以直接回答：
• **社区热度** — 哪个社区最活跃、哪些 CA 刚被大量推送
• **持仓分析** — 当前哪笔盈亏最多、该不该继续持有
• **发币人战绩** — 某个发币人历史胜率、推过的代币表现
• **交易统计** — 近期胜率、盈亏、平均持仓时长

请先在 ⚙ 中配置并启用 AI 接口。`

const QUICK_CHIPS = [
  { label: '今日社区热度', prompt: '根据近2小时社区信号，哪个社区最活跃？有哪些高热度CA值得关注？' },
  { label: '当前持仓分析', prompt: '分析一下当前持仓情况，哪些盈利最好、哪些风险最高？' },
  { label: '近期胜率', prompt: '分析近期交易数据，胜率和盈亏情况怎么样？有什么规律？' },
  { label: '遗漏了哪些CA', prompt: '最近2小时有哪些高热度但没买的CA？为什么没买？是过滤器拦截了吗？' },
  { label: '策略建议', prompt: '根据当前胜率和持仓情况，止盈止损参数需要调整吗？' },
]

export default function AiChat() {
  const [open, setOpen]         = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [messages, setMessages] = useState([
    { role: 'assistant', content: WELCOME_MSG }
  ])
  const [input, setInput]       = useState('')
  const [loading, setLoading]   = useState(false)
  const [aiEnabled, setAiEnabled] = useState(false)
  const [error, setError]       = useState(null)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  useEffect(() => {
    getAiConfig().then(d => setAiEnabled(d.enabled)).catch(() => {})
  }, [])

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, open])

  const send = useCallback(async (overrideText) => {
    const text = (overrideText ?? input).trim()
    if (!text || loading) return
    setInput('')
    setError(null)

    const newMsg = { role: 'user', content: text }
    const history = [...messages, newMsg]
    setMessages(history)
    setLoading(true)

    try {
      const apiMsgs = history.filter(m => m.role !== 'system').map(m => ({ role: m.role, content: m.content }))
      const res = await sendAiChat(apiMsgs, true)
      if (res.error) {
        setError(res.error)
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: res.reply, model: res.model }])
      }
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || '请求失败')
    } finally {
      setLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [input, loading, messages])

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  const clearHistory = () => {
    setMessages([{ role: 'assistant', content: WELCOME_MSG }])
    setError(null)
  }

  return (
    <div className="border-t border-dark-600 flex flex-col" style={{ minHeight: open ? 320 : 'auto' }}>
      {/* 标题栏 */}
      <div
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-dark-700/40 transition-colors shrink-0"
        onClick={() => setOpen(v => !v)}
      >
        <div className="flex items-center gap-2">
          <span className="text-sm">🤖</span>
          <span className="text-xs font-semibold text-gray-300">AI 助手</span>
          {aiEnabled
            ? <span className="w-1.5 h-1.5 rounded-full bg-accent-blue animate-pulse shrink-0" />
            : <span className="text-[10px] text-gray-600">未启用</span>}
        </div>
        <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
          {open && (
            <>
              <button
                onClick={clearHistory}
                className="text-[10px] text-gray-600 hover:text-gray-400 px-1"
                title="清空对话"
              >✕</button>
              <button
                onClick={() => setShowSettings(v => !v)}
                className={clsx('text-[10px] px-1.5 py-0.5 rounded border transition-colors',
                  showSettings
                    ? 'border-accent-blue/50 text-accent-blue bg-accent-blue/10'
                    : 'border-dark-500 text-gray-600 hover:text-gray-400'
                )}
              >⚙</button>
            </>
          )}
          <span className={clsx('text-gray-600 text-xs transition-transform', open ? 'rotate-180' : '')}>▼</span>
        </div>
      </div>

      {open && (
        <div className="flex flex-col flex-1 overflow-hidden">
          {/* 设置面板 */}
          {showSettings ? (
            <div className="flex-1 overflow-y-auto bg-dark-850">
              <AiSettings
                onClose={() => setShowSettings(false)}
                onSaved={() => getAiConfig().then(d => setAiEnabled(d.enabled)).catch(() => {})}
              />
            </div>
          ) : (
            <>
              {/* 消息列表 */}
              <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
                {messages.map((m, i) => (
                  <div
                    key={i}
                    className={clsx(
                      'rounded-lg px-2.5 py-2 text-xs leading-relaxed max-w-[92%]',
                      m.role === 'user'
                        ? 'ml-auto bg-accent-blue/20 border border-accent-blue/30 text-gray-200'
                        : 'bg-dark-700/60 border border-dark-600 text-gray-300'
                    )}
                  >
                    {m.role === 'assistant' && (
                      <div className="text-[10px] text-gray-600 mb-0.5 flex items-center gap-1">
                        🤖 <span>{m.model || 'AI'}</span>
                      </div>
                    )}
                    <MdText text={m.content} />
                  </div>
                ))}

                {/* 加载中动效 */}
                {loading && (
                  <div className="bg-dark-700/60 border border-dark-600 rounded-lg px-2.5 py-2 max-w-[60%]">
                    <div className="flex items-center gap-1">
                      {[0, 0.15, 0.3].map(d => (
                        <span
                          key={d}
                          className="w-1.5 h-1.5 rounded-full bg-accent-blue/60 animate-bounce"
                          style={{ animationDelay: `${d}s` }}
                        />
                      ))}
                    </div>
                  </div>
                )}

                {/* 错误提示 */}
                {error && (
                  <div className="text-[11px] text-red-400 bg-red-900/20 border border-red-800/40 rounded px-2 py-1.5">
                    ⚠ {error}
                  </div>
                )}

                <div ref={bottomRef} />
              </div>

              {/* 快捷问题 chips */}
              {messages.length <= 1 && aiEnabled && (
                <div className="px-3 pb-1.5 flex flex-wrap gap-1">
                  {QUICK_CHIPS.map(chip => (
                    <button
                      key={chip.label}
                      onClick={() => send(chip.prompt)}
                      disabled={loading}
                      className="text-[10px] px-2 py-0.5 rounded-full border border-dark-500 text-gray-500 hover:border-accent-blue/50 hover:text-accent-blue transition-colors"
                    >
                      {chip.label}
                    </button>
                  ))}
                </div>
              )}

              {/* 输入框 */}
              <div className="px-2 py-2 border-t border-dark-600 shrink-0">
                <div className="flex gap-1.5">
                  <textarea
                    ref={inputRef}
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={onKeyDown}
                    placeholder={aiEnabled
                      ? '问问今日哪个社区最热 / 当前持仓风险如何 / 近期胜率趋势...'
                      : '请先在 ⚙ 中配置并启用 AI'}
                    disabled={!aiEnabled || loading}
                    rows={2}
                    className={clsx(
                      'flex-1 bg-dark-700 border border-dark-500 text-gray-200 text-xs rounded px-2 py-1.5 resize-none outline-none',
                      'focus:border-accent-blue/60 transition-colors placeholder-gray-600',
                      (!aiEnabled || loading) && 'opacity-50 cursor-not-allowed'
                    )}
                  />
                  <button
                    onClick={() => send()}
                    disabled={!aiEnabled || loading || !input.trim()}
                    className={clsx(
                      'px-2.5 rounded text-xs font-semibold transition-colors shrink-0',
                      aiEnabled && !loading && input.trim()
                        ? 'bg-accent-blue hover:bg-accent-blue/80 text-white'
                        : 'bg-dark-600 text-gray-600 cursor-not-allowed'
                    )}
                  >
                    {loading ? '…' : '发送'}
                  </button>
                </div>
                <div className="text-[10px] text-gray-700 mt-1 text-right">Shift+Enter 换行</div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
