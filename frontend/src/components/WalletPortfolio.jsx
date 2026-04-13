import { useState, useCallback } from 'react'
import { clsx } from 'clsx'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const CHAIN_COLORS = {
  SOL: '#9945FF',
  BSC: '#F0B90B',
  ETH: '#627EEA',
  XLAYER: '#00D4AA',
}

const CHAIN_BG = {
  SOL: 'bg-purple-900/20 border-purple-800/40',
  BSC: 'bg-yellow-900/20 border-yellow-800/40',
  ETH: 'bg-blue-900/20 border-blue-800/40',
  XLAYER: 'bg-teal-900/20 border-teal-800/40',
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).catch(() => {})
}

function AddrBadge({ address }) {
  const [copied, setCopied] = useState(false)
  if (!address) return <span className="text-gray-600 text-xs">未配置钱包</span>
  const short = address.slice(0, 6) + '…' + address.slice(-4)
  return (
    <button
      className="flex items-center gap-1 text-xs font-mono text-gray-400 hover:text-gray-200 transition-colors"
      title={address}
      onClick={() => { copyToClipboard(address); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
    >
      {short}
      <span className="text-gray-600">{copied ? '✓' : '⧉'}</span>
    </button>
  )
}

function PnlText({ pct }) {
  if (pct === undefined || pct === null) return null
  const pos = pct >= 0
  return (
    <span className={clsx('font-mono text-xs', pos ? 'text-green-400' : 'text-red-400')}>
      {pos ? '+' : ''}{pct.toFixed(2)}%
    </span>
  )
}

export default function WalletPortfolio() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [expandedChains, setExpandedChains] = useState({})

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/analytics/portfolio`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setData(d)
      setLastUpdated(new Date())
      // 默认展开有持仓的链
      const exp = {}
      for (const c of d.chains) {
        if (c.position_count > 0) exp[c.chain] = true
      }
      setExpandedChains(prev => ({ ...exp, ...prev }))
    } catch (e) {
      console.error('portfolio fetch error', e)
    } finally {
      setLoading(false)
    }
  }, [])

  // 首次渲染时自动加载（不查余额，只加载持仓）
  useState(() => { refresh() }, [])

  const toggleChain = (chain) =>
    setExpandedChains(prev => ({ ...prev, [chain]: !prev[chain] }))

  return (
    <div className="bg-dark-800 rounded-xl border border-dark-600 p-4">
      {/* 标题栏 */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-200">钱包资产总览</h3>
          {lastUpdated && (
            <div className="text-xs text-gray-500 mt-0.5">
              更新于 {lastUpdated.toLocaleTimeString()}
              {data && (
                <span className="ml-2 text-gray-400">
                  · 持仓估值
                  <span className={clsx('ml-1 font-mono', data.total_position_value_usdt > 0 ? 'text-yellow-400' : 'text-gray-400')}>
                    ${data.total_position_value_usdt.toFixed(2)}
                  </span>
                  {data.price_source === 'realtime' && <span className="ml-1 text-green-400/60 text-[10px]">实时</span>}
                  {data.price_source === 'cached' && <span className="ml-1 text-gray-500 text-[10px]">缓存</span>}
                </span>
              )}
            </div>
          )}
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className={clsx(
            'flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border transition-colors',
            loading
              ? 'border-dark-500 text-gray-600 cursor-not-allowed'
              : 'border-dark-500 text-gray-400 hover:text-white hover:border-gray-500'
          )}
        >
          <span className={loading ? 'animate-spin' : ''}>↻</span>
          {loading ? '查询中...' : '刷新余额'}
        </button>
      </div>

      {/* 各链卡片 */}
      {!data ? (
        <div className="text-gray-500 text-sm text-center py-6">加载中...</div>
      ) : (
        <div className="space-y-2">
          {data.chains.map(c => {
            const expanded = expandedChains[c.chain]
            const hasPos = c.position_count > 0
            const color = CHAIN_COLORS[c.chain] || '#6B7280'
            const bgCls = CHAIN_BG[c.chain] || 'bg-gray-900/20 border-gray-700/40'

            return (
              <div key={c.chain} className={clsx('rounded-lg border overflow-hidden', bgCls)}>
                {/* 链头部行 */}
                <div
                  className="flex items-center gap-3 px-3 py-2.5 cursor-pointer select-none"
                  onClick={() => toggleChain(c.chain)}
                >
                  {/* 链名 */}
                  <div className="flex items-center gap-2 min-w-[60px]">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: color }} />
                    <span className="text-sm font-bold text-gray-200">{c.chain}</span>
                  </div>

                  {/* 地址 */}
                  <div className="flex-1" onClick={e => e.stopPropagation()}>
                    <AddrBadge address={c.address} />
                  </div>

                  {/* 主链余额 + USDT */}
                  <div className="text-right min-w-[110px]">
                    {c.native_balance !== null && c.native_balance !== undefined ? (
                      <div>
                        <div className="text-xs font-mono text-gray-200">
                          {Number(c.native_balance).toFixed(6)}
                          <span className="ml-1 text-gray-500">{c.native_symbol}</span>
                        </div>
                        {c.usdt_balance !== null && c.usdt_balance !== undefined && (
                          <div className="text-xs font-mono text-green-400/80 mt-0.5">
                            {Number(c.usdt_balance).toFixed(2)}
                            <span className="ml-1 text-gray-500">USDT</span>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="text-xs text-gray-600">点击刷新查询</div>
                    )}
                  </div>

                  {/* 持仓价值 */}
                  <div className="text-right min-w-[80px]">
                    {hasPos ? (
                      <div>
                        <div className="text-xs font-mono text-yellow-400">${c.position_value_usdt.toFixed(2)}</div>
                        <div className="text-[10px] text-gray-500">{c.position_count} 个持仓</div>
                      </div>
                    ) : (
                      <div className="text-xs text-gray-600">无持仓</div>
                    )}
                  </div>

                  {/* 展开箭头 */}
                  {hasPos && (
                    <span className={clsx('text-gray-500 text-xs transition-transform', expanded ? 'rotate-180' : '')}>▼</span>
                  )}
                </div>

                {/* 持仓列表（展开） */}
                {hasPos && expanded && (
                  <div className="border-t border-dark-600/50">
                    <table className="w-full text-xs text-gray-400">
                      <thead>
                        <tr className="border-b border-dark-600/30 text-gray-600">
                          <th className="text-left px-3 py-1.5 font-medium">CA</th>
                          <th className="text-right px-2 py-1.5 font-medium">买入价</th>
                          <th className="text-right px-2 py-1.5 font-medium">当前价</th>
                          <th className="text-right px-2 py-1.5 font-medium">涨跌</th>
                          <th className="text-right px-2 py-1.5 font-medium">数量</th>
                          <th className="text-right px-3 py-1.5 font-medium">估值</th>
                        </tr>
                      </thead>
                      <tbody>
                        {c.positions.map(pos => (
                          <tr key={pos.id} className="border-b border-dark-600/20 hover:bg-dark-700/20">
                            <td className="px-3 py-1.5 font-mono text-gray-300">
                              <span title={pos.ca}>{pos.ca.slice(0, 8)}…</span>
                            </td>
                            <td className="text-right px-2 py-1.5 font-mono text-gray-400">
                              {pos.entry_price ? pos.entry_price.toPrecision(4) : '—'}
                            </td>
                            <td className="text-right px-2 py-1.5 font-mono text-gray-300">
                              {pos.current_price ? pos.current_price.toPrecision(4) : '—'}
                            </td>
                            <td className="text-right px-2 py-1.5">
                              <PnlText pct={pos.pnl_pct} />
                            </td>
                            <td className="text-right px-2 py-1.5 font-mono text-gray-400">
                              {pos.token_amount > 0 ? pos.token_amount.toExponential(3) : '—'}
                            </td>
                            <td className="text-right px-3 py-1.5 font-mono text-yellow-400">
                              ${pos.value_usdt.toFixed(4)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
