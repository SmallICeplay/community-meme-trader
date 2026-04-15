/**
 * AdminPanel.jsx
 * 管理员功能：登录弹窗 + 总配置页 + 演示钱包管理
 * 通过 localStorage 持久化 admin token
 */
import { useState, useEffect, useCallback } from 'react'
import { clsx } from 'clsx'
import { Card } from './UI'

const ADMIN_TOKEN_KEY = 'hodlo_admin_token'

// ── API helpers ───────────────────────────────────────────────
async function adminFetch(path, options = {}) {
  const token = localStorage.getItem(ADMIN_TOKEN_KEY) || ''
  const res = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
      ...(options.headers || {}),
    },
  })
  if (res.status === 401 || res.status === 403) {
    localStorage.removeItem(ADMIN_TOKEN_KEY)
    throw new Error('UNAUTHORIZED')
  }
  return res
}

// ── 敏感配置分组 ───────────────────────────────────────────────
const ADMIN_SECTIONS = [
  {
    title: 'AVE Trade API',
    tag: '交易执行',
    tagColor: 'bg-yellow-900/30 text-yellow-400',
    keys: [
      { key: 'ave_trade_api_key', label: 'API Key', type: 'password', mono: true },
      { key: 'ave_trade_api_url', label: 'Base URL', type: 'text', mono: true },
    ],
  },
  {
    title: 'AVE Data API',
    tag: '行情数据',
    tagColor: 'bg-blue-900/30 text-blue-400',
    keys: [
      { key: 'ave_data_api_key', label: 'API Key', type: 'password', mono: true },
      { key: 'ave_data_api_url', label: 'Base URL', type: 'text', mono: true },
    ],
  },
  {
    title: 'AI 接口 Key',
    tag: '自定义Key',
    tagColor: 'bg-purple-900/30 text-purple-400',
    keys: [
      { key: 'ai_api_key',  label: 'API Key',  type: 'password', mono: true },
      { key: 'ai_base_url', label: 'Base URL', type: 'text',     mono: true },
    ],
  },
]

// ── 登录弹窗 ─────────────────────────────────────────────────
function LoginModal({ onSuccess, onClose }) {
  const [pwd, setPwd]         = useState('')
  const [error, setError]     = useState('')
  const [loading, setLoading] = useState(false)

  const login = async () => {
    if (!pwd.trim()) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch('/api/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: pwd }),
      })
      if (res.ok) {
        const data = await res.json()
        localStorage.setItem(ADMIN_TOKEN_KEY, data.token)
        onSuccess()
      } else {
        const err = await res.json().catch(() => ({}))
        setError(err.detail || '密码错误')
      }
    } catch {
      setError('连接失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70" onClick={onClose}>
      <div
        className="bg-dark-800 border border-dark-500 rounded-xl p-6 w-80"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 mb-4">
          <span className="text-lg">🔐</span>
          <h3 className="text-sm font-semibold text-gray-200">管理员登录</h3>
        </div>
        <p className="text-xs text-gray-500 mb-4">输入管理员密码以访问敏感配置和演示钱包管理</p>
        <input
          type="password"
          autoFocus
          value={pwd}
          onChange={e => setPwd(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && login()}
          placeholder="管理员密码"
          className="w-full bg-dark-700 border border-dark-500 text-gray-200 rounded-lg px-3 py-2 text-sm font-mono mb-3 focus:outline-none focus:border-accent-blue"
        />
        {error && <div className="text-xs text-red-400 mb-3">⚠ {error}</div>}
        <div className="flex gap-2">
          <button
            onClick={onClose}
            className="flex-1 py-2 rounded-lg text-xs text-gray-400 border border-dark-500 hover:text-gray-200 transition-colors"
          >
            取消
          </button>
          <button
            onClick={login}
            disabled={loading || !pwd.trim()}
            className="flex-1 py-2 rounded-lg text-xs font-semibold bg-accent-blue/80 hover:bg-accent-blue text-white transition-colors disabled:opacity-40"
          >
            {loading ? '验证中...' : '登录'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 演示钱包管理 ──────────────────────────────────────────────
function DemoWalletManager({ onMsg }) {
  const [status, setStatus]   = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await adminFetch('/api/admin/wallet/demo_status')
      if (res.ok) setStatus(await res.json())
    } catch {}
  }, [])

  useEffect(() => { load() }, [load])

  const saveDemo = async () => {
    setLoading(true)
    try {
      const res = await adminFetch('/api/admin/wallet/save_demo', { method: 'POST' })
      const d = await res.json()
      onMsg(res.ok ? { type: 'ok', text: d.message || '已保存为演示钱包' } : { type: 'err', text: d.detail || '失败' })
      load()
    } catch { onMsg({ type: 'err', text: '请求失败' }) }
    finally { setLoading(false) }
  }

  const restoreDemo = async () => {
    setLoading(true)
    try {
      const res = await adminFetch('/api/admin/wallet/restore_demo', { method: 'POST' })
      const d = await res.json()
      onMsg(res.ok ? { type: 'ok', text: d.message || '已恢复演示钱包' } : { type: 'err', text: d.detail || '失败' })
      load()
    } catch { onMsg({ type: 'err', text: '请求失败' }) }
    finally { setLoading(false) }
  }

  return (
    <Card>
      <h3 className="text-sm font-semibold text-gray-200 mb-1">演示钱包管理</h3>
      <p className="text-xs text-gray-500 mb-3">配置对外演示使用的默认钱包</p>

      {status && (
        <div className="mb-3 p-2 rounded bg-dark-700 border border-dark-600 text-xs space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-gray-500">当前模式:</span>
            <span className={clsx('font-semibold', status.wallet_mode === 'demo' ? 'text-blue-400' : 'text-orange-400')}>
              {status.wallet_mode === 'demo' ? '演示钱包' : '自定义钱包'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-gray-500">演示钱包快照:</span>
            <span className={status.has_demo ? 'text-green-400' : 'text-gray-600'}>
              {status.has_demo ? `已配置 (${status.demo_source})` : '未配置'}
            </span>
          </div>
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={saveDemo}
          disabled={loading}
          className="flex-1 py-1.5 rounded text-xs border border-dark-500 text-gray-400 hover:text-gray-200 hover:border-dark-400 transition-colors"
        >
          保存当前为演示钱包
        </button>
        <button
          onClick={restoreDemo}
          disabled={loading || !status?.has_demo}
          className={clsx(
            'flex-1 py-1.5 rounded text-xs font-semibold transition-colors',
            status?.has_demo
              ? 'bg-blue-900/30 border border-blue-800/40 text-blue-400 hover:bg-blue-900/50'
              : 'bg-dark-700 border border-dark-600 text-gray-600 cursor-not-allowed'
          )}
        >
          恢复演示钱包
        </button>
      </div>
    </Card>
  )
}

// ── 总配置面板（敏感 Key 管理）────────────────────────────────
function AdminConfigPanel({ onMsg }) {
  const [configs, setConfigs]   = useState({})
  const [edits, setEdits]       = useState({})
  const [saving, setSaving]     = useState(false)
  const [loading, setLoading]   = useState(true)

  useEffect(() => {
    adminFetch('/api/admin/config')
      .then(r => r.json())
      .then(d => { setConfigs(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const set = (key, val) => setEdits(prev => ({ ...prev, [key]: val }))
  const get = (key) => edits[key] !== undefined ? edits[key] : (configs[key] || '')

  const save = async () => {
    if (Object.keys(edits).length === 0) return
    setSaving(true)
    try {
      const res = await adminFetch('/api/admin/config', {
        method: 'PUT',
        body: JSON.stringify({ configs: edits }),
      })
      if (res.ok) {
        setConfigs(prev => ({ ...prev, ...edits }))
        setEdits({})
        onMsg({ type: 'ok', text: '总配置已保存' })
      } else {
        const e = await res.json().catch(() => ({}))
        onMsg({ type: 'err', text: e.detail || '保存失败' })
      }
    } catch { onMsg({ type: 'err', text: '请求失败' }) }
    finally { setSaving(false) }
  }

  if (loading) return <Card><div className="text-xs text-gray-500">加载中...</div></Card>

  return (
    <Card>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-200">API Keys（敏感配置）</h3>
          <p className="text-xs text-gray-500 mt-0.5">仅管理员可见，普通用户页面自动隐藏</p>
        </div>
        {Object.keys(edits).length > 0 && (
          <span className="text-[10px] text-yellow-400 bg-yellow-900/20 border border-yellow-800/30 px-2 py-0.5 rounded">
            {Object.keys(edits).length} 项未保存
          </span>
        )}
      </div>

      <div className="space-y-4">
        {ADMIN_SECTIONS.map(section => (
          <div key={section.title} className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-gray-300">{section.title}</span>
              <span className={clsx('text-[10px] px-1.5 py-0.5 rounded', section.tagColor)}>{section.tag}</span>
            </div>
            <div className="space-y-1.5">
              {section.keys.map(f => (
                <div key={f.key}>
                  <label className="text-[10px] text-gray-500 mb-0.5 block">{f.label}</label>
                  <input
                    type={f.type}
                    value={get(f.key)}
                    onChange={e => set(f.key, e.target.value)}
                    placeholder={configs[f.key] ? '留空保持不变（当前已设置）' : `输入 ${f.label}`}
                    className={clsx(
                      'w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-xs text-gray-200 placeholder-gray-600',
                      'focus:outline-none focus:border-accent-blue',
                      f.mono && 'font-mono'
                    )}
                  />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <button
        onClick={save}
        disabled={saving || Object.keys(edits).length === 0}
        className={clsx(
          'mt-4 w-full py-2 rounded-lg text-xs font-semibold transition-colors',
          Object.keys(edits).length > 0
            ? 'bg-red-900/40 hover:bg-red-900/60 border border-red-800/40 text-red-300'
            : 'bg-dark-700 border border-dark-600 text-gray-600 cursor-not-allowed'
        )}
      >
        {saving ? '保存中...' : '保存敏感配置'}
      </button>
    </Card>
  )
}

// ── 主导出：AdminPanel ────────────────────────────────────────
export default function AdminPanel({ onClose }) {
  const [msg, setMsg] = useState(null)

  useEffect(() => {
    if (msg) {
      const t = setTimeout(() => setMsg(null), 3000)
      return () => clearTimeout(t)
    }
  }, [msg])

  return (
    <div className="space-y-4 max-w-2xl">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
            🔐 管理员控制台
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">敏感配置 · 演示钱包 · 系统总控</p>
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-sm">✕</button>
      </div>

      {/* 全局消息提示 */}
      {msg && (
        <div className={clsx(
          'text-xs px-3 py-2 rounded border',
          msg.type === 'ok'
            ? 'bg-green-900/20 border-green-800/40 text-green-400'
            : 'bg-red-900/20 border-red-800/40 text-red-400'
        )}>
          {msg.type === 'ok' ? '✓ ' : '⚠ '}{msg.text}
        </div>
      )}

      <DemoWalletManager onMsg={setMsg} />
      <AdminConfigPanel onMsg={setMsg} />
    </div>
  )
}

// ── 导出工具函数 ──────────────────────────────────────────────
export { ADMIN_TOKEN_KEY, LoginModal }
