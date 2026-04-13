import { clsx } from 'clsx'

export function Card({ children, className }) {
  return (
    <div className={clsx('bg-dark-800 rounded-xl border border-dark-600 p-4', className)}>
      {children}
    </div>
  )
}

export function Badge({ children, color = 'blue' }) {
  const colors = {
    blue: 'bg-blue-900/40 text-blue-300 border-blue-700',
    green: 'bg-green-900/40 text-green-300 border-green-700',
    red: 'bg-red-900/40 text-red-300 border-red-700',
    yellow: 'bg-yellow-900/40 text-yellow-300 border-yellow-700',
    purple: 'bg-purple-900/40 text-purple-300 border-purple-700',
    gray: 'bg-gray-800 text-gray-400 border-gray-600',
  }
  return (
    <span className={clsx('text-xs px-2 py-0.5 rounded border font-mono', colors[color])}>
      {children}
    </span>
  )
}

export function PnlValue({ value, suffix = 'U' }) {
  const isPos = value >= 0
  return (
    <span className={isPos ? 'text-accent-green font-mono' : 'text-accent-red font-mono'}>
      {isPos ? '+' : ''}{typeof value === 'number' ? value.toFixed(4) : value}{suffix}
    </span>
  )
}

export function Button({ children, onClick, disabled, variant = 'primary', className, size = 'md' }) {
  const variants = {
    primary: 'bg-accent-blue hover:bg-blue-500 text-white',
    danger: 'bg-accent-red hover:bg-red-500 text-white',
    ghost: 'bg-dark-600 hover:bg-dark-500 text-gray-300',
    success: 'bg-green-700 hover:bg-green-600 text-white',
  }
  const sizes = { sm: 'px-3 py-1 text-xs', md: 'px-4 py-2 text-sm', lg: 'px-6 py-3 text-base' }
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        'rounded-lg font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed',
        variants[variant], sizes[size], className
      )}
    >
      {children}
    </button>
  )
}

export function Toggle({ checked, onChange, label }) {
  return (
    <label className="flex items-center gap-3 cursor-pointer select-none">
      <div
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative w-12 h-6 rounded-full transition-colors',
          checked ? 'bg-accent-green' : 'bg-dark-500'
        )}
      >
        <div className={clsx(
          'absolute top-1 w-4 h-4 bg-white rounded-full transition-transform shadow',
          checked ? 'left-7' : 'left-1'
        )} />
      </div>
      {label && <span className="text-sm text-gray-300">{label}</span>}
    </label>
  )
}

export function StatCard({ label, value, sub, color = 'white' }) {
  const colors = { white: 'text-white', green: 'text-accent-green', red: 'text-accent-red', yellow: 'text-accent-yellow' }
  return (
    <Card>
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={clsx('text-2xl font-bold font-mono', colors[color])}>{value}</div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </Card>
  )
}
