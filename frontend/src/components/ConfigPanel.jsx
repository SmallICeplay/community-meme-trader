import { useState, useEffect } from 'react'
import { getConfig, updateConfig, getTradeHistory, getTradeStats } from '../api'
import { Card, Button, Toggle } from './UI'
import { clsx } from 'clsx'

const CHAIN_OPTIONS = ['SOL', 'BSC', 'ETH', 'XLAYER']

const FILTER_DEFAULTS = {
  // 支出限额
  spend_limit_enabled: 'false',
  spend_limit_usdt: '50',
  spend_limit_hours: '24',
  // 重复买入
  ca_repeat_buy_enabled: 'false',
  ca_repeat_qwfc_delta: '20',
  // 发币人质量
  filter_sender_win_rate_enabled: 'false',
  filter_sender_win_rate_min: '60',
  filter_sender_group_win_rate_enabled: 'false',
  filter_sender_group_win_rate_min: '60',
  filter_sender_total_tokens_enabled: 'false',
  filter_sender_total_tokens_min: '5',
  filter_sender_best_multiple_enabled: 'false',
  filter_sender_best_multiple_min: '10',
  filter_new_sender_action: 'skip',
  // 防追高
  filter_current_multiple_enabled: 'false',
  filter_current_multiple_max: '3',
  // 传播热度
  filter_qwfc_enabled: 'false',
  filter_qwfc_min: '3',
  filter_bqfc_enabled: 'false',
  filter_bqfc_min: '2',
  filter_fgq_enabled: 'false',
  filter_fgq_min: '2',
  filter_grcxcs_enabled: 'false',
  filter_grcxcs_min: '1',
  // 市场数据
  filter_market_cap_enabled: 'false',
  filter_market_cap_min: '10000',
  filter_market_cap_max: '5000000',
  filter_price_change_5m_enabled: 'false',
  filter_price_change_5m_min: '0',
  filter_buy_volume_1h_enabled: 'false',
  filter_buy_volume_1h_min: '1000',
  filter_holders_enabled: 'false',
  filter_holders_min: '50',
  // 安全
  filter_honeypot_enabled: 'true',
  filter_honeypot_unknown_action: 'skip',
  filter_mintable_enabled: 'true',
  filter_risk_score_enabled: 'false',
  filter_risk_score_max: '70',
  filter_max_holder_pct_enabled: 'false',
  filter_max_holder_pct_max: '90',
}

export default function ConfigPanel({ onConfigSaved }) {
  const [cfg, setCfg] = useState({
    bot_enabled: 'false',
    buy_amount_usdt: '2',
    take_profit_pct: '50',
    stop_loss_pct: '30',
    max_hold_minutes: '60',
    max_concurrent_positions: '5',
    enabled_chains: 'SOL,BSC,ETH,XLAYER',
    price_poll_interval: '10',
    position_price_source: 'cached',
    gas_price_multiplier: '1.0',
    approve_gas_price_gwei: '1.0',
    buy_amount_fallback_enabled: 'true',
    buy_amount_fallback_usdt: '1',
    buy_precheck_enabled: 'true',
    buy_fail_cooldown_seconds: '300',
    ave_trade_api_key: '',
    ave_trade_api_url: 'https://bot-api.ave.ai',
    ave_data_api_key: 'SW59NmZFRG2yfRSSWvKlzTuAuZBFl5SUUCV2DUX5rg5eK8n6sipMlLkwXCX5qHGw',
    ave_data_api_url: 'https://ave-api.cloud',
    ...FILTER_DEFAULTS,
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    getConfig().then(data => setCfg(prev => ({ ...prev, ...data }))).catch(() => { })
  }, [])

  const set = (key, val) => setCfg(prev => ({ ...prev, [key]: val }))
  const setEnabled = (key, v) => set(key, v ? 'true' : 'false')
  const isEnabled = (key) => cfg[key] === 'true'

  const toggleChain = (chain) => {
    const current = cfg.enabled_chains.split(',').filter(Boolean)
    const next = current.includes(chain)
      ? current.filter(c => c !== chain)
      : [...current, chain]
    set('enabled_chains', next.join(','))
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateConfig(cfg)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      onConfigSaved?.()
    } catch (e) {
      alert('保存失败: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  const loadWallets = async () => {
    setLoadingWallets(true)
    try {
      const data = await getWalletAddresses()
      setWallets(data.addresses || {})
    } catch (e) {
      alert('获取钱包地址失败（请先配置助记词）')
    } finally {
      setLoadingWallets(false)
    }
  }

  const enabledChainsArr = cfg.enabled_chains.split(',').filter(Boolean)
  const botEnabled = cfg.bot_enabled === 'true'

  return (
    <div className="space-y-4">
      {/* Bot 开关 */}
      <Card>
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-200">Bot 状态</h3>
            <p className="text-xs text-gray-500 mt-0.5">开启后自动监听CA并执行交易</p>
          </div>
          <Toggle
            checked={botEnabled}
            onChange={(v) => set('bot_enabled', v ? 'true' : 'false')}
          />
        </div>
        {botEnabled && (
          <div className="mt-3 text-xs text-accent-green flex items-center gap-1">
            <div className="w-1.5 h-1.5 bg-accent-green rounded-full animate-pulse" />
            运行中
          </div>
        )}
      </Card>

      {/* ── API Keys ──────────────────────────────────────────── */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-1">API 配置</h3>
        <p className="text-xs text-gray-500 mb-3">修改后点击保存立即生效，无需重启</p>
        <div className="space-y-4">
          {/* Trade API */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-gray-300">AVE Trade API</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-900/30 text-yellow-400">用于买卖交易</span>
            </div>
            <div className="space-y-1.5">
              <div>
                <label className="text-[10px] text-gray-500 mb-0.5 block">API Key</label>
                <input
                  type="password"
                  value={cfg.ave_trade_api_key}
                  onChange={e => set('ave_trade_api_key', e.target.value)}
                  placeholder="Bearer token..."
                  className="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-xs text-gray-200 font-mono placeholder-gray-600 focus:outline-none focus:border-accent-blue"
                />
              </div>
              <div>
                <label className="text-[10px] text-gray-500 mb-0.5 block">Base URL</label>
                <input
                  type="text"
                  value={cfg.ave_trade_api_url}
                  onChange={e => set('ave_trade_api_url', e.target.value)}
                  className="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-xs text-gray-400 font-mono focus:outline-none focus:border-accent-blue"
                />
              </div>
            </div>
          </div>

          <div className="border-t border-dark-600" />

          {/* Data API */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-gray-300">AVE Data API</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-400">用于行情数据</span>
            </div>
            <div className="space-y-1.5">
              <div>
                <label className="text-[10px] text-gray-500 mb-0.5 block">API Key</label>
                <input
                  type="password"
                  value={cfg.ave_data_api_key}
                  onChange={e => set('ave_data_api_key', e.target.value)}
                  placeholder="Ave-Auth token..."
                  className="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-xs text-gray-200 font-mono placeholder-gray-600 focus:outline-none focus:border-accent-blue"
                />
              </div>
              <div>
                <label className="text-[10px] text-gray-500 mb-0.5 block">Base URL</label>
                <input
                  type="text"
                  value={cfg.ave_data_api_url}
                  onChange={e => set('ave_data_api_url', e.target.value)}
                  className="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-xs text-gray-400 font-mono focus:outline-none focus:border-accent-blue"
                />
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* 交易参数 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">交易参数</h3>
        <div className="grid grid-cols-2 gap-4">
          <NumberInput
            label="买入金额 (USDT)"
            value={cfg.buy_amount_usdt}
            onChange={v => set('buy_amount_usdt', v)}
            min={0.1} max={100} step={0.1}
            hint="推荐 ≥ 1U，过小部分代币会模拟失败"
          />
          <NumberInput
            label="最大并发持仓数"
            value={cfg.max_concurrent_positions}
            onChange={v => set('max_concurrent_positions', v)}
            min={1} max={20}
            hint="同时持有的最多仓位数"
          />
        </div>
        {/* 金额自动升级 */}
        <div className="mt-3 p-3 rounded-lg bg-dark-700 border border-dark-500 space-y-2">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs font-medium text-gray-300">买入金额自动升级</div>
              <div className="text-[10px] text-gray-500 mt-0.5">原始金额失败时，自动用更大金额重试一次</div>
            </div>
            <Toggle
              checked={cfg.buy_amount_fallback_enabled === 'true'}
              onChange={v => set('buy_amount_fallback_enabled', v ? 'true' : 'false')}
            />
          </div>
          {cfg.buy_amount_fallback_enabled === 'true' && (
            <div className="pt-1">
              <NumberInput
                label="升级后金额 (USDT)"
                value={cfg.buy_amount_fallback_usdt}
                onChange={v => set('buy_amount_fallback_usdt', v)}
                min={1} max={100} step={0.5}
                hint={`原始 ${cfg.buy_amount_usdt}U 失败 → 自动改用此金额重试`}
              />
            </div>
          )}
        </div>
        {/* 买入预检 */}
        <div className="mt-3 p-3 rounded-lg bg-dark-700 border border-dark-500 space-y-1">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs font-medium text-gray-300">买入前预检（getAmountOut）</div>
              <div className="text-[10px] text-gray-500 mt-0.5">买入前先询价验证代币合约有效性，可过滤无效/无流动性代币，避免浪费 gas</div>
            </div>
            <Toggle
              checked={cfg.buy_precheck_enabled === 'true'}
              onChange={v => set('buy_precheck_enabled', v ? 'true' : 'false')}
            />
          </div>
          <p className="text-[10px] text-gray-600">
            {cfg.buy_precheck_enabled === 'true'
              ? '✅ 开启：预检失败（无法询价）的代币直接跳过，不发送交易'
              : '⚠ 关闭：跳过预检，直接尝试买入，合约无效时才报错（消耗一次 createEvmTx）'}
          </p>
        </div>
        {/* 失败冷却时间 */}
        <div className="mt-3">
          <NumberInput
            label="买入失败冷却时间 (秒)"
            value={cfg.buy_fail_cooldown_seconds}
            onChange={v => set('buy_fail_cooldown_seconds', v)}
            min={0} max={3600} step={30}
            hint="同一 CA 买入失败后，冷却此时长内不再重试（0=不冷却；推荐 300 秒）"
          />
        </div>
      </Card>
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">卖出策略</h3>
        <div className="grid grid-cols-3 gap-4">
          <NumberInput
            label="止盈 (%)"
            value={cfg.take_profit_pct}
            onChange={v => set('take_profit_pct', v)}
            min={1} max={10000}
            hint="涨幅达到此值自动卖出"
          />
          <NumberInput
            label="止损 (%)"
            value={cfg.stop_loss_pct}
            onChange={v => set('stop_loss_pct', v)}
            min={1} max={100}
            hint="跌幅达到此值自动卖出"
          />
          <NumberInput
            label="最大持仓时间 (分钟)"
            value={cfg.max_hold_minutes}
            onChange={v => set('max_hold_minutes', v)}
            min={1} max={1440}
            hint="超过此时间强制卖出"
          />
        </div>
      </Card>

      {/* ── 仪表盘显示 ────────────────────────────────────────── */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">仪表盘显示</h3>
        <div className="space-y-1">
          <label className="text-xs text-gray-400">持仓估值价格来源</label>
          <div className="flex gap-2 mt-1">
            {[
              { value: 'cached', label: '缓存价格', hint: '用监控器最近一次轮询价（延迟≤10秒），不消耗 API' },
              { value: 'realtime', label: '实时价格', hint: '点击刷新时调用 AVE API 获取最新价，每个持仓消耗一次请求' },
            ].map(opt => (
              <button
                key={opt.value}
                title={opt.hint}
                onClick={() => set('position_price_source', opt.value)}
                className={clsx(
                  'flex-1 text-xs py-2 px-3 rounded-lg border transition-colors text-left',
                  cfg.position_price_source === opt.value
                    ? 'border-accent-blue bg-accent-blue/10 text-accent-blue'
                    : 'border-dark-500 text-gray-500 hover:text-gray-300'
                )}
              >
                <div className="font-medium">{opt.label}</div>
                <div className="text-[10px] mt-0.5 opacity-70 leading-tight">{opt.hint}</div>
              </button>
            ))}
          </div>
        </div>
      </Card>

      {/* ── 购买过滤条件 ───────────────────────────────────────── */}

      {/* 发币人质量 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-1">过滤条件 · 发币人质量</h3>
        <p className="text-xs text-gray-600 mb-3">全部勾选条件须同时满足才会买入</p>
        <div className="space-y-3">
          <FilterRow
            label="全局胜率 ≥"
            enabled={isEnabled('filter_sender_win_rate_enabled')}
            onToggle={v => setEnabled('filter_sender_win_rate_enabled', v)}
            value={cfg.filter_sender_win_rate_min}
            onChange={v => set('filter_sender_win_rate_min', v)}
            unit="%" min={1} max={100}
            hint="发币人历史中涨20%以上的比例"
          />
          <FilterRow
            label="群胜率 ≥"
            enabled={isEnabled('filter_sender_group_win_rate_enabled')}
            onToggle={v => setEnabled('filter_sender_group_win_rate_enabled', v)}
            value={cfg.filter_sender_group_win_rate_min}
            onChange={v => set('filter_sender_group_win_rate_min', v)}
            unit="%" min={1} max={100}
            hint="发币人在本群的胜率（字段上线后生效）"
          />
          <FilterRow
            label="历史发币数 ≥"
            enabled={isEnabled('filter_sender_total_tokens_enabled')}
            onToggle={v => setEnabled('filter_sender_total_tokens_enabled', v)}
            value={cfg.filter_sender_total_tokens_min}
            onChange={v => set('filter_sender_total_tokens_min', v)}
            unit="个" min={1} max={999}
            hint="样本量不足时胜率参考价值低"
          />
          <FilterRow
            label="历史最高倍数 ≥"
            enabled={isEnabled('filter_sender_best_multiple_enabled')}
            onToggle={v => setEnabled('filter_sender_best_multiple_enabled', v)}
            value={cfg.filter_sender_best_multiple_min}
            onChange={v => set('filter_sender_best_multiple_min', v)}
            unit="x" min={1} max={10000}
            hint="历史至少出过一次大涨"
          />

          {/* 新人处理策略 */}
          <div className="pt-2 border-t border-dark-600">
            <label className="block text-xs text-gray-500 mb-2">
              无历史记录的新发币人处理方式
            </label>
            <div className="flex gap-2">
              {[
                { v: 'skip', label: '跳过', desc: '直接不买' },
                { v: 'allow', label: '正常买', desc: '忽略胜率条件' },
                { v: 'half', label: '半仓买', desc: '金额减半' },
              ].map(opt => (
                <button
                  key={opt.v}
                  onClick={() => set('filter_new_sender_action', opt.v)}
                  title={opt.desc}
                  className={clsx(
                    'flex-1 py-1.5 rounded-lg text-xs border transition-colors',
                    cfg.filter_new_sender_action === opt.v
                      ? 'bg-accent-blue/20 border-accent-blue text-accent-blue'
                      : 'bg-dark-700 border-dark-500 text-gray-500 hover:text-gray-300'
                  )}
                >
                  {opt.label}
                  <span className="block text-gray-600 text-[10px]">{opt.desc}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {/* 防追高 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">过滤条件 · 防追高</h3>
        <FilterRow
          label="当前涨幅 ≤"
          enabled={isEnabled('filter_current_multiple_enabled')}
          onToggle={v => setEnabled('filter_current_multiple_enabled', v)}
          value={cfg.filter_current_multiple_max}
          onChange={v => set('filter_current_multiple_max', v)}
          unit="x" min={0.1} max={100} step={0.1}
          hint="CA推送时已涨超此倍数则跳过（来源：cxrzf字段）"
        />
      </Card>

      {/* 传播热度 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">过滤条件 · 传播热度</h3>
        <div className="space-y-3">
          <FilterRow
            label="全网发送次数 ≥"
            enabled={isEnabled('filter_qwfc_enabled')}
            onToggle={v => setEnabled('filter_qwfc_enabled', v)}
            value={cfg.filter_qwfc_min}
            onChange={v => set('filter_qwfc_min', v)}
            unit="次" min={1} max={999}
            hint="全网各群发送该CA的次数"
          />
          <FilterRow
            label="本群发送次数 ≥"
            enabled={isEnabled('filter_bqfc_enabled')}
            onToggle={v => setEnabled('filter_bqfc_enabled', v)}
            value={cfg.filter_bqfc_min}
            onChange={v => set('filter_bqfc_min', v)}
            unit="次" min={1} max={999}
            hint="当前群内发送次数"
          />
          <FilterRow
            label="覆盖群数量 ≥"
            enabled={isEnabled('filter_fgq_enabled')}
            onToggle={v => setEnabled('filter_fgq_enabled', v)}
            value={cfg.filter_fgq_min}
            onChange={v => set('filter_fgq_min', v)}
            unit="个群" min={1} max={999}
            hint="在多少个群里被提到"
          />
          <FilterRow
            label="个人查询次数 ≥"
            enabled={isEnabled('filter_grcxcs_enabled')}
            onToggle={v => setEnabled('filter_grcxcs_enabled', v)}
            value={cfg.filter_grcxcs_min}
            onChange={v => set('filter_grcxcs_min', v)}
            unit="次" min={1} max={999}
            hint="被个人主动查询的次数"
          />
        </div>
      </Card>

      {/* 市场数据 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">过滤条件 · 市场数据</h3>
        <div className="space-y-3">
          {/* 市值范围 */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              <input
                type="checkbox"
                id="mc-enabled"
                checked={isEnabled('filter_market_cap_enabled')}
                onChange={e => setEnabled('filter_market_cap_enabled', e.target.checked)}
                className="accent-accent-blue"
              />
              <label htmlFor="mc-enabled" className="text-xs text-gray-300 cursor-pointer">
                市值范围 (U)
              </label>
            </div>
            <div className={clsx('grid grid-cols-2 gap-2', !isEnabled('filter_market_cap_enabled') && 'opacity-40')}>
              <div>
                <label className="block text-xs text-gray-600 mb-1">最小</label>
                <input type="number" value={cfg.filter_market_cap_min}
                  onChange={e => set('filter_market_cap_min', e.target.value)}
                  disabled={!isEnabled('filter_market_cap_enabled')}
                  className="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue disabled:cursor-not-allowed" />
              </div>
              <div>
                <label className="block text-xs text-gray-600 mb-1">最大（0=不限）</label>
                <input type="number" value={cfg.filter_market_cap_max}
                  onChange={e => set('filter_market_cap_max', e.target.value)}
                  disabled={!isEnabled('filter_market_cap_enabled')}
                  className="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue disabled:cursor-not-allowed" />
              </div>
            </div>
          </div>
          <FilterRow
            label="5分钟涨幅 ≥"
            enabled={isEnabled('filter_price_change_5m_enabled')}
            onToggle={v => setEnabled('filter_price_change_5m_enabled', v)}
            value={cfg.filter_price_change_5m_min}
            onChange={v => set('filter_price_change_5m_min', v)}
            unit="%" min={-100} max={10000} step={1}
            hint="正数=已在涨，负数=允许微跌"
          />
          <FilterRow
            label="1小时买入量 ≥"
            enabled={isEnabled('filter_buy_volume_1h_enabled')}
            onToggle={v => setEnabled('filter_buy_volume_1h_enabled', v)}
            value={cfg.filter_buy_volume_1h_min}
            onChange={v => set('filter_buy_volume_1h_min', v)}
            unit="U" min={0} max={9999999}
            hint="有足够买盘才入"
          />
          <FilterRow
            label="持有人数 ≥"
            enabled={isEnabled('filter_holders_enabled')}
            onToggle={v => setEnabled('filter_holders_enabled', v)}
            value={cfg.filter_holders_min}
            onChange={v => set('filter_holders_min', v)}
            unit="人" min={1} max={99999}
            hint="持有人太少流动性差"
          />
        </div>
      </Card>

      {/* 安全过滤 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">过滤条件 · 安全（建议常开）</h3>
        <div className="space-y-3">
          {/* 蜜罐：两个独立开关，互不依赖 */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-gray-300">排除确认蜜罐 <span className="text-red-400 font-mono text-[10px]">is_honeypot=1</span></p>
              <p className="text-xs text-gray-600">已被检测为蜜罐的代币直接跳过（强烈推荐开启）</p>
            </div>
            <Toggle
              checked={isEnabled('filter_honeypot_enabled')}
              onChange={v => setEnabled('filter_honeypot_enabled', v)}
            />
          </div>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-gray-300">排除未检测代币 <span className="text-orange-400 font-mono text-[10px]">is_honeypot=-1</span></p>
              <p className="text-xs text-gray-600">新币上线初期尚未被检测，此类代币貔貅风险高</p>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-gray-600">
                {cfg.filter_honeypot_unknown_action === 'skip' ? '跳过' : '放行'}
              </span>
              <Toggle
                checked={cfg.filter_honeypot_unknown_action === 'skip'}
                onChange={v => set('filter_honeypot_unknown_action', v ? 'skip' : 'allow')}
              />
            </div>
          </div>
          <p className="text-[10px] text-gray-600 -mt-1 pl-0.5">
            {cfg.filter_honeypot_unknown_action === 'skip'
              ? '⚠ 未检测代币一律跳过，更安全但会错过部分新上线代币'
              : '⚠ 放行未检测代币——貔貅损失风险高，建议仅短期测试使用'}
          </p>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-gray-300">排除可增发代币</p>
              <p className="text-xs text-gray-600">is_mintable = 1 则跳过</p>
            </div>
            <Toggle
              checked={isEnabled('filter_mintable_enabled')}
              onChange={v => setEnabled('filter_mintable_enabled', v)}
            />
          </div>
          <FilterRow
            label="风险评分 ≤"
            enabled={isEnabled('filter_risk_score_enabled')}
            onToggle={v => setEnabled('filter_risk_score_enabled', v)}
            value={cfg.filter_risk_score_max}
            onChange={v => set('filter_risk_score_max', v)}
            unit="分" min={1} max={100}
            hint="AVE风险评分，越低越安全"
          />
          <FilterRow
            label="最大持仓比 ≤"
            enabled={isEnabled('filter_max_holder_pct_enabled')}
            onToggle={v => setEnabled('filter_max_holder_pct_enabled', v)}
            value={cfg.filter_max_holder_pct_max}
            onChange={v => set('filter_max_holder_pct_max', v)}
            unit="%" min={1} max={100}
            hint="zzb字段，防庄家集中控盘"
          />
        </div>
      </Card>

      {/* 支出限额 */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-200">支出限额</h3>
            <p className="text-xs text-gray-500 mt-0.5">时间窗口内累计买入不超过设定金额</p>
          </div>
          <Toggle
            checked={isEnabled('spend_limit_enabled')}
            onChange={v => setEnabled('spend_limit_enabled', v)}
          />
        </div>
        <div className={clsx('grid grid-cols-2 gap-4', !isEnabled('spend_limit_enabled') && 'opacity-40')}>
          <NumberInput
            label="限额 (USDT)"
            value={cfg.spend_limit_usdt}
            onChange={v => set('spend_limit_usdt', v)}
            min={1} max={99999}
            hint="窗口内最多买入总金额"
          />
          <NumberInput
            label="时间窗口 (小时)"
            value={cfg.spend_limit_hours}
            onChange={v => set('spend_limit_hours', v)}
            min={1} max={168}
            hint="例：24=每24小时重置"
          />
        </div>
      </Card>

      {/* 重复买入 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">重复买入策略</h3>
        <div className="flex items-center justify-between mb-3">
          <div>
            <p className="text-xs text-gray-300">允许重复买入已平仓 CA</p>
            <p className="text-xs text-gray-600 mt-0.5">默认关闭：已买过并平仓的 CA 不再重复买入</p>
          </div>
          <Toggle
            checked={cfg.ca_repeat_buy_enabled === 'true'}
            onChange={v => set('ca_repeat_buy_enabled', v ? 'true' : 'false')}
          />
        </div>
        {cfg.ca_repeat_buy_enabled === 'true' && (
          <div className="pl-3 border-l border-dark-500 space-y-2">
            <NumberInput
              label="热度暴涨阈值（全网发送增量）"
              value={cfg.ca_repeat_qwfc_delta}
              onChange={v => set('ca_repeat_qwfc_delta', v)}
              min={5} max={200} step={5}
              hint="比上次推送新增多少条全网发送才触发重买（推荐 20）"
            />
            <p className="text-[10px] text-gray-600">
              例：设 20 表示同一 CA 再次出现时，全网发送数比上次 +20 才重买，否则跳过
            </p>
          </div>
        )}
      </Card>

      {/* 链配置 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">启用链（优先级从左到右）</h3>
        <div className="flex gap-3">
          {CHAIN_OPTIONS.map(chain => (
            <button
              key={chain}
              onClick={() => toggleChain(chain)}
              className={`px-4 py-2 rounded-lg text-sm font-mono border transition-colors ${
                enabledChainsArr.includes(chain)
                  ? 'bg-accent-blue/20 border-accent-blue text-accent-blue'
                  : 'bg-dark-700 border-dark-500 text-gray-500'
              }`}
            >
              {chain}
            </button>
          ))}
        </div>
        <div className="mt-3">
          <NumberInput
            label="价格轮询间隔 (秒)"
            value={cfg.price_poll_interval}
            onChange={v => set('price_poll_interval', v)}
            min={5} max={60}
            hint="检查持仓盈亏的频率"
          />
        </div>

        {/* GAS 费优化配置 */}
        <div className="mt-4 pt-4 border-t border-dark-600">
          <p className="text-xs font-semibold text-gray-400 mb-3">GAS 费优化</p>
          <div className="grid grid-cols-2 gap-3">
            <NumberInput
              label="Swap GasPrice 倍数"
              value={cfg.gas_price_multiplier}
              onChange={v => set('gas_price_multiplier', v)}
              min={0.1} max={5.0} step={0.1}
              hint="1.0=跟随网络实时价格（BSC约$0.6~0.8/笔）；>1.0加速但更贵"
            />
            <NumberInput
              label="Approve GasPrice (Gwei)"
              value={cfg.approve_gas_price_gwei}
              onChange={v => set('approve_gas_price_gwei', v)}
              min={0.05} max={10.0} step={0.05}
              hint="approve 一次性授权，推荐 1.0 Gwei（命中缓存后不再消耗）"
            />
          </div>
          <p className="text-xs text-gray-600 mt-2">
            ⚠️ BSC 每笔 swap 实际约 $0.6~0.9U（合约 gas 消耗约 200k~400k，不可避免）
          </p>
        </div>
      </Card>
      <Card>
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-200">钱包管理</h3>
            <p className="text-xs text-gray-500 mt-0.5">新建、导入、查看各链地址</p>
          </div>
          <button
            onClick={() => window.__switchTab?.('wallet')}
            className="text-xs text-accent-blue hover:underline"
          >
            前往钱包管理 →
          </button>
        </div>
      </Card>

      {/* 保存按钮 */}
      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving}>
          {saving ? '保存中...' : saved ? '已保存 ✓' : '保存配置'}
        </Button>
      </div>
    </div>
  )
}

// ── 子组件 ────────────────────────────────────────────────

function FilterRow({ label, enabled, onToggle, value, onChange, unit, min, max, step = 1, hint }) {
  return (
    <div className={clsx('flex items-center gap-3', !enabled && 'opacity-50')}>
      <input
        type="checkbox"
        checked={enabled}
        onChange={e => onToggle(e.target.checked)}
        className="accent-accent-blue shrink-0"
      />
      <label className="text-xs text-gray-300 w-36 shrink-0 cursor-pointer" onClick={() => onToggle(!enabled)}>
        {label}
      </label>
      <input
        type="number"
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={!enabled}
        min={min} max={max} step={step}
        className="w-24 bg-dark-700 border border-dark-500 rounded-lg px-2 py-1 text-sm text-gray-200 focus:outline-none focus:border-accent-blue disabled:cursor-not-allowed"
      />
      <span className="text-xs text-gray-500 w-8 shrink-0">{unit}</span>
      {hint && <span className="text-xs text-gray-600 hidden xl:block">{hint}</span>}
    </div>
  )
}

function NumberInput({ label, value, onChange, min, max, step = 1, hint }) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <input
        type="number"
        value={value}
        onChange={e => onChange(e.target.value)}
        min={min}
        max={max}
        step={step}
        className="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
      />
      {hint && <p className="text-xs text-gray-600 mt-0.5">{hint}</p>}
    </div>
  )
}

// ── Gas 分析面板 ──────────────────────────────────────────────────────────────
const REASON_ICON_G = {
  take_profit: '🎯', stop_loss: '🛡', time_limit: '⏰',
  manual: '👆', zero_balance: '💀', sell_failed: '⚠',
}
const REASON_ZH_G = {
  take_profit: '止盈', stop_loss: '止损', time_limit: '超时',
  manual: '手动', zero_balance: '归零', sell_failed: '放弃',
}

export function GasAnalysisPanel() {
  const [trades, setTrades] = useState([])
  const [stats, setStats] = useState(null)
  const [liveGas, setLiveGas] = useState(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    Promise.all([getTradeHistory(100, 0), getTradeStats()])
      .then(([t, s]) => { setTrades(t); setStats(s) })
      .catch(() => {})
    // 查询当前 BSC 网络 gas price
    fetch('https://bsc-dataseed1.binance.org', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', method: 'eth_gasPrice', params: [], id: 1 }),
    })
      .then(r => r.json())
      .then(d => {
        const gwei = parseInt(d.result, 16) / 1e9
        setLiveGas(gwei)
      })
      .catch(() => {})
  }, [])

  const withGas = trades.filter(t => t.gas_fee_usd > 0)
  const totalGas = withGas.reduce((s, t) => s + t.gas_fee_usd, 0)
  const avgGas = withGas.length ? totalGas / withGas.length : 0
  const totalNet = withGas.reduce((s, t) => s + (t.pnl_usdt || 0) - t.gas_fee_usd, 0)

  // 按原因分组统计
  const byReason = withGas.reduce((acc, t) => {
    const k = t.reason
    if (!acc[k]) acc[k] = { count: 0, totalGas: 0, totalPnl: 0 }
    acc[k].count++
    acc[k].totalGas += t.gas_fee_usd
    acc[k].totalPnl += t.pnl_usdt || 0
    return acc
  }, {})

  const sorted = [...withGas].sort((a, b) => b.gas_fee_usd - a.gas_fee_usd)
  const shown = expanded ? sorted : sorted.slice(0, 10)

  return (
    <div className="space-y-4">
      {/* 实时 Gas Price */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">实时网络 Gas Price</h3>
        <div className="flex items-center gap-4">
          <div className="text-center">
            <div className="text-2xl font-bold font-mono text-orange-400">
              {liveGas != null ? liveGas.toFixed(4) : '…'}
            </div>
            <div className="text-xs text-gray-500 mt-0.5">Gwei (BSC)</div>
          </div>
          <div className="flex-1 text-xs text-gray-500 space-y-1">
            <div>当前设置 multiplier <span className="text-gray-300 font-mono">×1.2</span> → 实际约 <span className="font-mono text-orange-300">{liveGas != null ? (liveGas * 1.2).toFixed(4) : '…'} Gwei</span></div>
            <div>每笔 swap (300k gas) 预估 ≈ <span className="font-mono text-orange-300">{liveGas != null ? (300000 * liveGas * 1.2 / 1e9 * 600).toFixed(5) : '…'}U</span>（BNB@600U）</div>
            <div className="text-gray-600">↑ 历史最高 0.3 Gwei时：每笔约 0.054U；修复前1 Gwei时：每笔约 0.18U</div>
          </div>
        </div>
      </Card>

      {/* 汇总统计 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Gas 总计', value: `${totalGas.toFixed(3)}U`, color: 'text-orange-400' },
          { label: '均 Gas/笔', value: `${avgGas.toFixed(4)}U`, color: 'text-orange-300' },
          { label: '交易 P&L', value: `${((stats?.total_pnl_usdt || 0) >= 0 ? '+' : '') + (stats?.total_pnl_usdt || 0).toFixed(3)}U`, color: (stats?.total_pnl_usdt || 0) >= 0 ? 'text-accent-green' : 'text-red-400' },
          { label: '净盈亏(含Gas)', value: `${totalNet >= 0 ? '+' : ''}${totalNet.toFixed(3)}U`, color: totalNet >= 0 ? 'text-accent-green' : 'text-red-400' },
        ].map(s => (
          <div key={s.label} className="bg-dark-700 rounded-lg px-3 py-2 border border-dark-600">
            <div className="text-[10px] text-gray-500 mb-0.5">{s.label}</div>
            <div className={clsx('text-sm font-semibold font-mono', s.color)}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* 按原因分组 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">按卖出原因统计</h3>
        <div className="space-y-2">
          {Object.entries(byReason).sort((a, b) => b[1].totalGas - a[1].totalGas).map(([reason, d]) => (
            <div key={reason} className="flex items-center gap-3 text-xs">
              <span className="w-4 shrink-0">{REASON_ICON_G[reason] || '•'}</span>
              <span className="text-gray-300 w-10 shrink-0">{REASON_ZH_G[reason] || reason}</span>
              <span className="text-gray-500 w-8 shrink-0">{d.count}笔</span>
              <div className="flex-1 h-1.5 bg-dark-600 rounded-full overflow-hidden">
                <div className="h-full bg-orange-500/50 rounded-full" style={{ width: `${totalGas > 0 ? d.totalGas / totalGas * 100 : 0}%` }} />
              </div>
              <span className="font-mono text-orange-400 w-16 text-right">{d.totalGas.toFixed(3)}U</span>
              <span className={clsx('font-mono w-16 text-right', (d.totalPnl - d.totalGas) >= 0 ? 'text-accent-green' : 'text-red-400')}>
                净{(d.totalPnl - d.totalGas) >= 0 ? '+' : ''}{(d.totalPnl - d.totalGas).toFixed(3)}U
              </span>
            </div>
          ))}
        </div>
      </Card>

      {/* 每笔明细 */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-200 mb-3">每笔 Gas 明细（按消耗降序）</h3>
        <div className="space-y-1.5">
          {shown.map(t => {
            const display = t.symbol || t.token_name || (t.ca.slice(0, 6) + '…' + t.ca.slice(-4))
            const netPnl = (t.pnl_usdt || 0) - t.gas_fee_usd
            const barPct = totalGas > 0 ? Math.min(t.gas_fee_usd / totalGas * 100, 100) : 0
            return (
              <div key={t.id} className="flex items-center gap-2 text-xs group">
                <span className="shrink-0">{REASON_ICON_G[t.reason] || '•'}</span>
                <span className="text-gray-300 w-20 shrink-0 truncate" title={display}>{display}</span>
                <div className="flex-1 h-1.5 bg-dark-600 rounded-full overflow-hidden">
                  <div className="h-full bg-orange-500/60 rounded-full" style={{ width: `${barPct}%` }} />
                </div>
                <span className="font-mono text-orange-400 w-16 text-right shrink-0">{t.gas_fee_usd.toFixed(4)}U</span>
                <span className={clsx('font-mono w-16 text-right shrink-0', netPnl >= 0 ? 'text-accent-green' : 'text-red-400')}>
                  净{netPnl >= 0 ? '+' : ''}{netPnl.toFixed(3)}U
                </span>
                <span className="text-gray-600 w-8 text-right shrink-0 text-[10px]">
                  {REASON_ZH_G[t.reason] || t.reason}
                </span>
                <span className="text-gray-700 w-24 text-right shrink-0 text-[10px] hidden group-hover:block">
                  {t.close_time?.slice(5, 16).replace('T', ' ')}
                </span>
              </div>
            )
          })}
        </div>
        {sorted.length > 10 && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="text-xs text-gray-500 hover:text-gray-300 w-full text-center py-2 border-t border-dark-600 mt-2"
          >
            {expanded ? '收起' : `显示全部 ${sorted.length} 笔`}
          </button>
        )}
      </Card>
    </div>
  )
}

