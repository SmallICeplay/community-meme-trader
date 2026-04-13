import { useEffect, useRef, useState, useCallback } from 'react'

export function useWebSocket() {
  const [logs, setLogs] = useState([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)

  const connect = useCallback(() => {
    const ws = new WebSocket(`ws://${window.location.host}/ws/logs`)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => {
      setConnected(false)
      reconnectTimer.current = setTimeout(connect, 3000)
    }
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'ping') return
        // buy_gas 事件：找到对应 buy 条目并更新 gas_fee_usd
        if (msg.type === 'buy_gas' && msg.data?.position_id) {
          setLogs(prev => prev.map(log =>
            log.type === 'buy' && log.data?.position_id === msg.data.position_id
              ? { ...log, data: { ...log.data, gas_fee_usd: msg.data.gas_fee_usd } }
              : log
          ))
          return
        }
        setLogs(prev => [{
          ...msg,
          id: Date.now() + Math.random(),
        }, ...prev].slice(0, 200))
      } catch { }
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { logs, connected }
}
