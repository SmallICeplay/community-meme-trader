import { useEffect, useState, useCallback } from 'react'
import { getTradeHistory } from '../api'
import { Card, Badge, PnlValue } from './UI'
import { TokenLogo } from './PositionsTable'
import { clsx } from 'clsx'

const CHAIN_COLOR = { SOL: 'purple', BSC: 'yellow', ETH: 'blue', XLAYER: 'gray' }
const REASON_LABEL = {
  take_profit: { label: '止盈', color: 'green', icon: '🎯' },
  stop_loss:   { label: '止损', color: 'red',   icon: '🛡' },
  time_limit:  { label: '超时', color: 'yellow', icon: '⏰' },
  manual:      { label: '手动', color: 'gray',   icon: '👆' },
  zero_balance:{ label: '归零', color: 'gray',   icon: '💀' },
  sell_failed: { label: '放弃', color: 'gray',   icon: '⚠' },
}

export default function TradeHistory() {
  const [trades, setTrades] = useState([])
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 20

  const load = useCallback(async () => {
    try {
      const data = await getTradeHistory(PAGE_SIZE, page * PAGE_SIZE)
      setTrades(data)
    } catch { }
  }, [page])

  useEffect(() => { load() }, [load])

  // 当页汇总
  const summary = trades.reduce((acc, t) => ({
    totalGas: acc.totalGas + (t.gas_fee_usd || 0),
    totalPnl: acc.totalPnl + (t.pnl_usdt || 0),
    totalNet: acc.totalNet + ((t.pnl_usdt || 0) - (t.gas_fee_usd || 0)),
  }), { totalGas: 0, totalPnl: 0, totalNet: 0 })

  return (
    <Card>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-300">交易历史</h2>
        <div className="flex items-center gap-3">
          {/* 当页汇总 */}
          <div className="flex items-center gap-2 text-[11px]">
            <span className="text-gray-500">本页 P&L:</span>
            <span className={clsx('font-mono font-semibold', summary.totalPnl >= 0 ? 'text-accent-green' : 'text-accent-red')}>
              {summary.totalPnl >= 0 ? '+' : ''}{summary.totalPnl.toFixed(3)}U
            </span>
            <span className="text-gray-600">|</span>
            <span className="text-gray-500">Gas:</span>
            <span className="font-mono text-orange-400">{summary.totalGas.toFixed(3)}U</span>
            <span className="text-gray-600">|</span>
            <span className="text-gray-500">净:</span>
            <span className={clsx('font-mono font-semibold', summary.totalNet >= 0 ? 'text-accent-green' : 'text-accent-red')}>
              {summary.totalNet >= 0 ? '+' : ''}{summary.totalNet.toFixed(3)}U
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="text-xs text-gray-500 hover:text-gray-300 disabled:opacity-30"
              disabled={page === 0}
              onClick={() => setPage(p => Math.max(0, p - 1))}
            >上一页</button>
            <span className="text-xs text-gray-600">{page + 1}</span>
            <button
              className="text-xs text-gray-500 hover:text-gray-300 disabled:opacity-30"
              disabled={trades.length < PAGE_SIZE}
              onClick={() => setPage(p => p + 1)}
            >下一页</button>
          </div>
        </div>
      </div>
      {trades.length === 0 ? (
        <div className="text-center text-gray-600 py-8 text-sm">暂无历史记录</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-dark-600">
                <th className="text-left py-2 pr-3">链</th>
                <th className="text-left py-2 pr-3">代币</th>
                <th className="text-right py-2 pr-3">买入价</th>
                <th className="text-right py-2 pr-3">卖出价</th>
                <th className="text-right py-2 pr-3">本金</th>
                <th className="text-right py-2 pr-3">P&amp;L</th>
                <th className="text-right py-2 pr-3 text-orange-400/70">Gas</th>
                <th className="text-right py-2 pr-3">净盈亏</th>
                <th className="text-right py-2 pr-3">原因</th>
                <th className="text-right py-2">时间</th>
              </tr>
            </thead>
            <tbody>
              {trades.map(t => {
                const r = REASON_LABEL[t.reason] || { label: t.reason, color: 'gray', icon: '•' }
                const display = t.symbol || t.token_name || ''
                const gas = t.gas_fee_usd || 0
                const netPnl = (t.pnl_usdt || 0) - gas
                return (
                  <tr key={t.id} className="border-b border-dark-700 hover:bg-dark-700/30">
                    <td className="py-2 pr-3">
                      <Badge color={CHAIN_COLOR[t.chain] || 'gray'}>{t.chain}</Badge>
                    </td>
                    <td className="py-2 pr-3">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <TokenLogo url={t.logo_url} name={display || t.ca} size={18} />
                        <div className="min-w-0">
                          {display
                            ? <div className="text-gray-200 truncate max-w-[80px]" title={display}>{display}</div>
                            : null}
                          <div className="font-mono text-gray-500 text-[10px]">{t.ca.slice(0, 6)}...{t.ca.slice(-4)}</div>
                        </div>
                      </div>
                    </td>
                    <td className="text-right py-2 pr-3 font-mono text-gray-400">{fmtPrice(t.entry_price)}</td>
                    <td className="text-right py-2 pr-3 font-mono text-gray-300">{fmtPrice(t.exit_price)}</td>
                    <td className="text-right py-2 pr-3 font-mono text-gray-400">{t.amount_usdt}U</td>
                    <td className="text-right py-2 pr-3">
                      <div><PnlValue value={t.pnl_usdt} /></div>
                      <div className={t.pnl_pct >= 0 ? 'text-accent-green' : 'text-accent-red'}>
                        {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct?.toFixed(1)}%
                      </div>
                    </td>
                    <td className="text-right py-2 pr-3">
                      {gas > 0
                        ? <span className="font-mono text-orange-400 text-[11px]">{gas.toFixed(3)}U</span>
                        : <span className="text-gray-600 text-[10px]">—</span>
                      }
                    </td>
                    <td className="text-right py-2 pr-3">
                      <span className={clsx('font-mono font-semibold text-[11px]', netPnl >= 0 ? 'text-accent-green' : 'text-red-400')}>
                        {netPnl >= 0 ? '+' : ''}{netPnl.toFixed(3)}U
                      </span>
                    </td>
                    <td className="text-right py-2 pr-3">
                      <span className="text-[10px]">{r.icon}</span>
                      <Badge color={r.color}>{r.label}</Badge>
                    </td>
                    <td className="text-right py-2 text-gray-500">{t.close_time?.slice(0, 16).replace('T', ' ')}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function fmtPrice(p) {
  if (!p || p === 0) return '—'
  if (p < 0.000001) return p.toExponential(3)
  if (p < 0.01) return p.toFixed(8)
  return p.toFixed(6)
}
