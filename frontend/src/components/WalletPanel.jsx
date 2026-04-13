import { useState, useEffect, useRef } from 'react'
import { getWalletStatus, createWallet, importWallet, deleteWallet, sweepResidualTokens } from '../api'
import { Card, Button, Badge } from './UI'
import { clsx } from 'clsx'

const CHAIN_COLOR = { SOL: 'purple', BSC: 'yellow', ETH: 'blue', XLAYER: 'gray' }

export default function WalletPanel({ logs = [] }) {
  const [status, setStatus] = useState(null)  // null=loading, {exists,addresses}
  const [mode, setMode] = useState(null)       // null | 'create' | 'import'
  const [mnemonicInput, setMnemonicInput] = useState('')
  const [newWalletResult, setNewWalletResult] = useState(null)  // 新建成功后显示助记词
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [confirmed, setConfirmed] = useState(false)  // 用户确认已备份

  const load = async () => {
    try {
      const data = await getWalletStatus()
      setStatus(data)
    } catch {
      setStatus({ exists: false, addresses: {} })
    }
  }

  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    setLoading(true)
    setError('')
    try {
      const result = await createWallet()
      setNewWalletResult(result)
      setMode(null)
      await load()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleImport = async () => {
    if (!mnemonicInput.trim()) return
    setLoading(true)
    setError('')
    try {
      await importWallet(mnemonicInput.trim())
      setMnemonicInput('')
      setMode(null)
      await load()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async () => {
    if (!confirm('确认删除钱包？此操作不可逆，请确保链上资产已转出！')) return
    setLoading(true)
    try {
      await deleteWallet()
      setNewWalletResult(null)
      setConfirmed(false)
      await load()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  if (!status) {
    return <Card><div className="text-center text-gray-500 py-8 text-sm">加载中...</div></Card>
  }

  return (
    <div className="space-y-4">
      {/* 新建钱包后显示助记词（一次性）*/}
      {newWalletResult && (
        <Card className="border-accent-yellow/50 bg-yellow-900/10">
          <div className="flex items-start gap-2 mb-3">
            <span className="text-accent-yellow text-lg">⚠️</span>
            <div>
              <p className="text-sm font-semibold text-accent-yellow">请立即备份助记词！关闭此页面后将无法再次查看</p>
              <p className="text-xs text-gray-500 mt-0.5">将以下12个单词按顺序抄写在纸上，妥善保管</p>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-2 mb-4">
            {newWalletResult.mnemonic?.split(' ').map((word, i) => (
              <div key={i} className="bg-dark-700 rounded-lg px-3 py-2 text-center">
                <span className="text-gray-600 text-xs">{i + 1}.</span>
                <span className="text-white text-sm ml-1 font-mono">{word}</span>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2 mb-3">
            <input
              type="checkbox"
              id="backup-confirm"
              checked={confirmed}
              onChange={e => setConfirmed(e.target.checked)}
              className="accent-accent-green"
            />
            <label htmlFor="backup-confirm" className="text-xs text-gray-300 cursor-pointer">
              我已抄写备份助记词，并了解丢失后无法找回
            </label>
          </div>
          <Button
            variant="ghost"
            size="sm"
            disabled={!confirmed}
            onClick={() => setNewWalletResult(null)}
          >
            确认已备份，关闭此提示
          </Button>
        </Card>
      )}

      {/* 钱包状态 */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-gray-200">钱包状态</h3>
          {status.exists && (
            <button
              onClick={handleDelete}
              disabled={loading}
              className="text-xs text-red-500 hover:text-red-400 disabled:opacity-40"
            >
              删除钱包
            </button>
          )}
        </div>

        {status.exists ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-xs text-accent-green">
              <div className="w-2 h-2 bg-accent-green rounded-full" />
              钱包已配置，加密存储在本地数据库
            </div>
            {/* 各链地址 */}
            <div className="space-y-2 mt-3">
              {Object.entries(status.addresses || {}).map(([chain, addr]) => (
                <div key={chain} className="flex items-center gap-3 bg-dark-700 rounded-lg px-3 py-2">
                  <Badge color={CHAIN_COLOR[chain] || 'gray'}>{chain}</Badge>
                  <span className="font-mono text-xs text-gray-300 break-all flex-1">{addr}</span>
                  <button
                    onClick={() => { navigator.clipboard.writeText(addr); }}
                    className="text-xs text-gray-500 hover:text-gray-300 shrink-0"
                    title="复制地址"
                  >
                    复制
                  </button>
                </div>
              ))}
            </div>
            <p className="text-xs text-gray-600 mt-2">
              私钥由 WALLET_MASTER_PASSWORD 加密保护，交易时自动解密使用
            </p>
          </div>
        ) : (
          <div className="text-center py-6">
            <p className="text-gray-500 text-sm mb-4">尚未配置钱包，请新建或导入</p>
            <div className="flex gap-3 justify-center">
              <Button onClick={() => { setMode('create'); setError('') }}>
                新建钱包
              </Button>
              <Button variant="ghost" onClick={() => { setMode('import'); setError('') }}>
                导入助记词
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* 新建确认 */}
      {mode === 'create' && (
        <Card className="border-accent-blue/30">
          <h3 className="text-sm font-semibold text-gray-200 mb-2">新建钱包</h3>
          <p className="text-xs text-gray-500 mb-4">
            系统将自动生成12个助记词，并派生 SOL / BSC / ETH / XLAYER 四条链的地址。
            <br />
            <span className="text-accent-yellow">助记词只显示一次，请务必备份！</span>
          </p>
          {error && <p className="text-xs text-red-400 mb-3">{error}</p>}
          <div className="flex gap-2">
            <Button onClick={handleCreate} disabled={loading}>
              {loading ? '生成中...' : '确认生成'}
            </Button>
            <Button variant="ghost" onClick={() => setMode(null)}>取消</Button>
          </div>
        </Card>
      )}

      {/* 导入助记词 */}
      {mode === 'import' && (
        <Card className="border-accent-blue/30">
          <h3 className="text-sm font-semibold text-gray-200 mb-2">导入助记词</h3>
          <p className="text-xs text-gray-500 mb-3">
            输入12或24个英文单词（空格分隔），系统将验证并加密保存
          </p>
          <textarea
            value={mnemonicInput}
            onChange={e => setMnemonicInput(e.target.value)}
            placeholder="word1 word2 word3 word4 word5 word6 word7 word8 word9 word10 word11 word12"
            rows={3}
            className="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-accent-blue resize-none mb-3"
          />
          {error && <p className="text-xs text-red-400 mb-3">{error}</p>}
          <div className="flex gap-2">
            <Button
              onClick={handleImport}
              disabled={loading || !mnemonicInput.trim()}
            >
              {loading ? '导入中...' : '确认导入'}
            </Button>
            <Button variant="ghost" onClick={() => { setMode(null); setMnemonicInput('') }}>取消</Button>
          </div>
        </Card>
      )}

      {/* 残留代币扫描 */}
      <SweepPanel logs={logs} />

      {/* 安全说明 */}
      <Card className="bg-dark-900/50">
        <h4 className="text-xs font-semibold text-gray-500 mb-2">安全说明</h4>
        <ul className="text-xs text-gray-600 space-y-1 list-disc list-inside">
          <li>助记词使用 AES-256 加密后存入本地数据库，密钥由 .env 中的 WALLET_MASTER_PASSWORD 派生</li>
          <li>私钥只在交易执行时在内存中临时解密，不写入任何日志或文件</li>
          <li>建议新建一个小额专用钱包用于交易，不要使用存有大量资产的主钱包</li>
          <li>请定期将利润转出到冷钱包</li>
        </ul>
      </Card>
    </div>
  )
}

// ── 残留代币扫描卖出面板 ──────────────────────────────────────────────────────
function SweepPanel({ logs = [] }) {
  const [state, setState] = useState('idle') // idle | started | error
  const [errMsg, setErrMsg] = useState('')
  const [startedAt, setStartedAt] = useState(null)
  const logEndRef = useRef(null)

  // 过滤扫描相关日志（含 🔍 💰 ✅ ❌ 的 log 行，且在启动之后产生）
  const sweepLogs = logs.filter(log => {
    if (log.type !== 'log' && !log.data?.message) return false
    const msg = log.data?.message || ''
    return msg.includes('🔍') || msg.includes('💰') || (startedAt && log.ts >= startedAt)
      ? (msg.includes('🔍') || msg.includes('💰') || msg.includes('✅ 卖出') || msg.includes('❌ 卖出'))
      : false
  })

  // 扫描完成检测（日志中出现"扫描完成"则标记）
  const isDone = sweepLogs.some(l => (l.data?.message || '').includes('扫描完成'))

  useEffect(() => {
    if (isDone && state === 'started') {
      setState('idle')
      setStartedAt(null)
    }
  }, [isDone])

  // 自动滚动到最新日志
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [sweepLogs.length])

  const handleSweep = async () => {
    if (!window.confirm('将扫描所有历史持仓，找出钱包中仍有余额的代币并尝试卖出。进度将在下方显示。确认开始？')) return
    setState('started')
    setStartedAt(Date.now())
    setErrMsg('')
    try {
      await sweepResidualTokens()
    } catch (e) {
      setErrMsg(e.response?.data?.detail || e.message || '请求失败')
      setState('error')
    }
  }

  const LEVEL_COLOR = { info: 'text-gray-300', warn: 'text-yellow-400', error: 'text-red-400' }

  return (
    <Card>
      <div className="flex items-start justify-between mb-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-200">残留代币扫描卖出</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            扫描历史持仓中钱包仍有余额的代币，尝试全部卖出并回收资金
          </p>
        </div>
        <button
          onClick={handleSweep}
          disabled={state === 'started'}
          className={clsx(
            'text-xs px-3 py-1.5 rounded-lg border font-medium transition-colors shrink-0',
            state === 'started'
              ? 'border-gray-600 text-gray-600 cursor-not-allowed'
              : 'border-orange-600 text-orange-400 hover:bg-orange-900/20'
          )}
        >
          {state === 'started' ? '扫描中...' : '开始扫描'}
        </button>
      </div>

      {state === 'error' && (
        <div className="text-xs text-red-400 mb-2">{errMsg}</div>
      )}

      {/* 扫描日志区域 */}
      {sweepLogs.length > 0 && (
        <div className="mt-2 bg-dark-900/60 border border-dark-600 rounded-lg p-2 max-h-48 overflow-y-auto font-mono space-y-0.5">
          {[...sweepLogs].reverse().map(log => {
            const msg = log.data?.message || ''
            const cls = LEVEL_COLOR[log.level] || 'text-gray-400'
            return (
              <div key={log.id} className={clsx('text-[11px] leading-relaxed', cls)}>{msg}</div>
            )
          })}
          <div ref={logEndRef} />
        </div>
      )}

      {state === 'started' && sweepLogs.length === 0 && (
        <div className="text-xs text-orange-400/80 animate-pulse mt-1">等待扫描开始...</div>
      )}
    </Card>
  )
}
