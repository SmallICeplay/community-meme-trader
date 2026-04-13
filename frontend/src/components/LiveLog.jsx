import { useEffect, useRef } from 'react'
import { clsx } from 'clsx'
import { Card } from './UI'

const LEVEL_STYLE = {
  info: 'text-gray-300',
  warn: 'text-yellow-400',
  error: 'text-red-400',
}

const TYPE_BADGE = {
  buy: { label: 'BUY', cls: 'text-green-400' },
  sell: { label: 'SELL', cls: 'text-red-400' },
  ca_received: { label: 'CA', cls: 'text-blue-400' },
  log: { label: 'LOG', cls: 'text-gray-500' },
  ping: null,
}

export default function LiveLog({ logs, connected }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  return (
    <Card className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-300">实时日志</h2>
        <div className="flex items-center gap-2">
          <div className={clsx('w-2 h-2 rounded-full', connected ? 'bg-accent-green animate-pulse' : 'bg-red-500')} />
          <span className="text-xs text-gray-500">{connected ? '已连接' : '断开'}</span>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto font-mono space-y-1 min-h-0" style={{maxHeight: 'calc(100vh - 280px)', fontSize: '15px', lineHeight: '1.6'}}>
        {logs.length === 0 && (
          <div className="text-gray-600 text-center py-8">等待事件...</div>
        )}
        {[...logs].reverse().map(log => {
          const badge = TYPE_BADGE[log.type]
          if (!badge) return null
          return (
            <div key={log.id} className="flex gap-2 items-start hover:bg-dark-700 px-1 rounded">
              <span className="text-gray-600 shrink-0">{log.ts?.slice(11, 19)}</span>
              {badge && (
                <span className={clsx('shrink-0 w-10 text-right', badge.cls)}>{badge.label}</span>
              )}
              <span className={LEVEL_STYLE[log.level] || 'text-gray-300'}>
                {log.data?.message || JSON.stringify(log.data)}
              </span>
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>
    </Card>
  )
}
