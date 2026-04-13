import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const getConfig = () => api.get('/config').then(r => r.data)
export const updateConfig = (configs) => api.put('/config', { configs }).then(r => r.data)
export const getPositions = () => api.get('/positions').then(r => r.data)
export const closePosition = (id) => api.delete(`/positions/${id}`).then(r => r.data)
export const getTradeHistory = (limit = 50, offset = 0) =>
  api.get('/trades/history', { params: { limit, offset } }).then(r => r.data)
export const getTradeStats = () => api.get('/trades/stats').then(r => r.data)

// 钱包管理
export const getWalletStatus = () => api.get('/wallet/status').then(r => r.data)
export const createWallet = () => api.post('/wallet/create').then(r => r.data)
export const importWallet = (mnemonic, force = false) =>
  api.post('/wallet/import', { mnemonic, force }).then(r => r.data)
export const deleteWallet = () => api.delete('/wallet/delete').then(r => r.data)
// 兼容旧调用
export const getWalletAddresses = () => api.get('/wallet/addresses').then(r => r.data)

// 残留代币扫描卖出
export const sweepResidualTokens = () => api.post('/sweep').then(r => r.data)

// 链上余额查询 & 批量卖出
export const getWalletBalances = () => api.get('/positions/balances').then(r => r.data)
export const sellBatch = (items) => api.post('/positions/sell_batch', { items }).then(r => r.data)
