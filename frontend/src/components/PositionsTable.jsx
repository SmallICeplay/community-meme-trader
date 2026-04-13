import { useState, useEffect, useCallback } from 'react'
import { getPositions, closePosition, getWalletBalances, sellBatch } from '../api'
import { Card, Badge, PnlValue, Button } from './UI'
import { clsx } from 'clsx'

const CHAIN_COLOR = { SOL: 'purple', BSC: 'yellow', ETH: 'blue', XLAYER: 'gray' }

function TokenCell({ logo_url, token_name, symbol, ca }) {
  const display = symbol || token_name || ''
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      <TokenLogo url={logo_url} name={display || ca} size={20} />
      <div className="min-w-0">
        {display
          ? <div className="text-gray-200 font-medium truncate max-w-[90px]" title={display}>{display}</div>
          : null}
        <div className="font-mono text-gray-500 text-[10px]">{ca.slice(0, 6)}...{ca.slice(-4)}</div>
      </div>
    </div>
  )
}

export function TokenLogo({ url, name, size = 24 }) {
  const [err, setErr] = useState(false)
  const letter = (name || '?')[0].toUpperCase()
  if (!url || err) {
    return (
      <div
        className="rounded-full bg-dark-600 flex items-center justify-center shrink-0 text-gray-400 font-bold"
        style={{ width: size, height: size, fontSize: size * 0.45 }}
      >{letter}</div>
    )
  }
  return (
    <img
      src={url}
      alt={name}
      onError={() => setErr(true)}
      className="rounded-full object-cover shrink-0"
      style={{ width: size, height: size }}
    />
  )
}

export default function PositionsTable({ onRefresh }) {
  const [tab, setTab] = useState('bot')

  return (
    <Card>
      {/* Tab 切换 */}
      <div className="flex gap-1 mb-4 border-b border-dark-600 pb-3">
        <button
          onClick={() => setTab('bot')}
          className={clsx(
            'text-xs px-3 py-1.5 rounded-lg font-medium transition-colors',
            tab === 'bot'
              ? 'bg-accent-blue/20 text-accent-blue'
              : 'text-gray-500 hover:text-gray-300'
          )}
        >Bot持仓</button>
        <button
          onClick={() => setTab('wallet')}
          className={clsx(
            'text-xs px-3 py-1.5 rounded-lg font-medium transition-colors',
            tab === 'wallet'
              ? 'bg-accent-blue/20 text-accent-blue'
              : 'text-gray-500 hover:text-gray-300'
          )}
        >链上余额</button>
      </div>

      {tab === 'bot'
        ? <BotPositions onRefresh={onRefresh} />
        : <WalletBalances />
      }
    </Card>
  )
}

// ── Bot 持仓（原有逻辑） ─────────────────────────────────────────
function BotPositions({ onRefresh }) {
  const [positions, setPositions] = useState([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await getPositions()
      setPositions(data)
    } catch { }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [load])

  const handleClose = async (id) => {
    if (!confirm('确认手动卖出此持仓？')) return
    setLoading(true)
    try {
      await closePosition(id)
      await load()
      onRefresh?.()
    } catch (e) {
      alert('操作失败: ' + e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-gray-500">
          Bot监控中 <span className="text-accent-blue ml-1">{positions.length}</span> 个持仓
        </span>
        <button onClick={load} className="text-xs text-gray-500 hover:text-gray-300">刷新</button>
      </div>
      {positions.length === 0 ? (
        <div className="text-center text-gray-600 py-8 text-sm">暂无Bot持仓</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-dark-600">
                <th className="text-left py-2 pr-3">链</th>
                <th className="text-left py-2 pr-3">代币</th>
                <th className="text-right py-2 pr-3">买入价</th>
                <th className="text-right py-2 pr-3">Gas(U)</th>
                <th className="text-right py-2 pr-3">当前价</th>
                <th className="text-right py-2 pr-3">持仓(U)</th>
                <th className="text-right py-2 pr-3">P&amp;L</th>
                <th className="text-right py-2 pr-3">持仓时间</th>
                <th className="text-right py-2"></th>
              </tr>
            </thead>
            <tbody>
              {positions.map(p => (
                <tr key={p.id} className="border-b border-dark-700 hover:bg-dark-700/30">
                  <td className="py-2 pr-3">
                    <Badge color={CHAIN_COLOR[p.chain] || 'gray'}>{p.chain}</Badge>
                  </td>
                  <td className="py-2 pr-3">
                    <TokenCell logo_url={p.logo_url} token_name={p.token_name} symbol={p.symbol} ca={p.ca} />
                  </td>
                  <td className="text-right py-2 pr-3 font-mono text-gray-400">{fmtPrice(p.entry_price)}</td>
                  <td className="text-right py-2 pr-3 font-mono text-orange-400/80">
                    {p.gas_fee_usd > 0 ? p.gas_fee_usd.toFixed(4) : '—'}
                  </td>
                  <td className="text-right py-2 pr-3 font-mono text-gray-300">{fmtPrice(p.current_price)}</td>
                  <td className="text-right py-2 pr-3 font-mono text-gray-300">{p.amount_usdt}</td>
                  <td className="text-right py-2 pr-3">
                    <div><PnlValue value={p.pnl_usdt} /></div>
                    <div className={p.pnl_pct >= 0 ? 'text-accent-green' : 'text-accent-red'}>
                      {p.pnl_pct >= 0 ? '+' : ''}{p.pnl_pct?.toFixed(1)}%
                    </div>
                  </td>
                  <td className="text-right py-2 pr-3 text-gray-500">{fmtDuration(p.hold_minutes)}</td>
                  <td className="text-right py-2">
                    <Button
                      size="sm"
                      variant="danger"
                      disabled={loading}
                      onClick={() => handleClose(p.id)}
                    >卖出</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── 链上余额（新增） ─────────────────────────────────────────────
function WalletBalances() {
  const [balances, setBalances] = useState([])
  const [scanning, setScanning] = useState(false)
  const [selling, setSelling] = useState(false)
  const [selected, setSelected] = useState(new Set())
  const [results, setResults] = useState([]) // 卖出结果
  const [scanError, setScanError] = useState('')

  const load = async () => {
    setScanning(true)
    setSelected(new Set())
    setResults([])
    setScanError('')
    try {
      const data = await getWalletBalances()
      setBalances(Array.isArray(data) ? data : [])
    } catch (e) {
      setScanError(e.response?.data?.detail || e.message || '扫描失败')
      setBalances([])
    } finally {
      setScanning(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const allKeys = balances.map(b => `${b.ca}:${b.chain}`)
  const allSelected = allKeys.length > 0 && allKeys.every(k => selected.has(k))

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set())
    } else {
      setSelected(new Set(allKeys))
    }
  }

  const toggleOne = (key) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleSellSelected = async () => {
    const items = balances
      .filter(b => selected.has(`${b.ca}:${b.chain}`))
      .map(b => ({ ca: b.ca, chain: b.chain, token_amount: b.token_amount }))
    if (items.length === 0) return
    if (!confirm(`确认卖出选中的 ${items.length} 个代币？`)) return

    setSelling(true)
    setResults([])
    try {
      const res = await sellBatch(items)
      setResults(res.results || [])
      // 延迟3秒让用户看到结果，再刷新列表
      setTimeout(() => load(), 3000)
    } catch (e) {
      setScanError(e.response?.data?.detail || e.message || '批量卖出失败')
    } finally {
      setSelling(false)
    }
  }

  const handleSellOne = async (b) => {
    if (!confirm(`确认卖出 ${b.symbol || b.ca.slice(0, 10)}... ${b.token_amount.toFixed(4)} 个代币？`)) return
    setSelling(true)
    try {
      const res = await sellBatch([{ ca: b.ca, chain: b.chain, token_amount: b.token_amount }])
      setResults(res.results || [])
      setTimeout(() => load(), 3000)
    } catch (e) {
      setScanError(e.response?.data?.detail || e.message || '卖出失败')
    } finally {
      setSelling(false)
    }
  }

  const selectedCount = selected.size

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-gray-500">
          {scanning ? '扫描中...' : `发现 ${balances.length} 个有余额代币`}
        </span>
        <button
          onClick={load}
          disabled={scanning}
          className="text-xs text-gray-500 hover:text-gray-300 disabled:opacity-40"
        >
          {scanning ? '扫描中...' : '重新扫描'}
        </button>
      </div>

      {/* 错误提示 */}
      {scanError && (
        <div className="mb-3 text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2">{scanError}</div>
      )}

      {/* 卖出结果提示 */}
      {results.length > 0 && (
        <div className="mb-3 space-y-1">
          {results.map((r, i) => (
            <div key={i} className={clsx(
              'text-[11px] font-mono px-2 py-1 rounded',
              r.success ? 'bg-green-900/20 text-green-400' : 'bg-red-900/20 text-red-400'
            )}>
              {r.success
                ? `✅ ${r.ca.slice(0, 10)}... 回收 ${r.usdt_received.toFixed(4)}U`
                : `❌ ${r.ca.slice(0, 10)}... ${r.error}`
              }
            </div>
          ))}
        </div>
      )}

      {scanning ? (
        <div className="text-center text-gray-500 py-8 text-sm animate-pulse">正在查询链上余额，请稍候...</div>
      ) : balances.length === 0 ? (
        <div className="text-center text-gray-600 py-8 text-sm">钱包中无残留代币</div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-dark-600">
                  <th className="py-2 pr-2 w-6">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      className="accent-accent-blue"
                    />
                  </th>
                  <th className="text-left py-2 pr-3">链</th>
                  <th className="text-left py-2 pr-3">代币</th>
                  <th className="text-right py-2 pr-3">余额</th>
                  <th className="text-right py-2 pr-3">买入价</th>
                  <th className="text-right py-2 pr-3">当前价</th>
                  <th className="text-right py-2 pr-3">P&amp;L</th>
                  <th className="text-right py-2"></th>
                </tr>
              </thead>
              <tbody>
                {balances.map(b => {
                  const key = `${b.ca}:${b.chain}`
                  const isSelected = selected.has(key)
                  return (
                    <tr
                      key={key}
                      className={clsx(
                        'border-b border-dark-700 hover:bg-dark-700/30 cursor-pointer',
                        isSelected && 'bg-accent-blue/5'
                      )}
                      onClick={() => toggleOne(key)}
                    >
                      <td className="py-2 pr-2" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleOne(key)}
                          className="accent-accent-blue"
                        />
                      </td>
                      <td className="py-2 pr-3">
                        <Badge color={CHAIN_COLOR[b.chain] || 'gray'}>{b.chain}</Badge>
                      </td>
                      <td className="py-2 pr-3">
                        <TokenCell logo_url={b.logo_url} token_name={b.token_name} symbol={b.symbol} ca={b.ca} />
                      </td>
                      <td className="text-right py-2 pr-3 font-mono text-gray-300">
                        {b.token_amount >= 1 ? b.token_amount.toFixed(2) : b.token_amount.toFixed(6)}
                      </td>
                      <td className="text-right py-2 pr-3 font-mono text-gray-500">{fmtPrice(b.entry_price)}</td>
                      <td className="text-right py-2 pr-3 font-mono text-gray-300">{fmtPrice(b.current_price)}</td>
                      <td className="text-right py-2 pr-3">
                        {b.entry_price > 0 ? (
                          <>
                            <div><PnlValue value={b.pnl_usdt} /></div>
                            <div className={b.pnl_pct >= 0 ? 'text-accent-green' : 'text-accent-red'}>
                              {b.pnl_pct >= 0 ? '+' : ''}{b.pnl_pct.toFixed(1)}%
                            </div>
                          </>
                        ) : <span className="text-gray-600">—</span>}
                      </td>
                      <td className="text-right py-2" onClick={e => e.stopPropagation()}>
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={selling}
                          onClick={() => handleSellOne(b)}
                        >卖出</Button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* 批量卖出按钮 */}
          <div className="mt-3 flex items-center gap-3">
            <Button
              variant="danger"
              disabled={selectedCount === 0 || selling}
              onClick={handleSellSelected}
            >
              {selling ? '卖出中...' : `卖出选中 (${selectedCount})`}
            </Button>
            {selectedCount > 0 && (
              <button
                onClick={() => setSelected(new Set())}
                className="text-xs text-gray-500 hover:text-gray-300"
              >清除选择</button>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function fmtPrice(p) {
  if (!p || p === 0) return '—'
  if (p < 0.000001) return p.toExponential(3)
  if (p < 0.01) return p.toFixed(8)
  return p.toFixed(6)
}

function fmtDuration(minutes) {
  if (!minutes) return '—'
  if (minutes < 60) return `${Math.floor(minutes)}m`
  return `${Math.floor(minutes / 60)}h${Math.floor(minutes % 60)}m`
}
