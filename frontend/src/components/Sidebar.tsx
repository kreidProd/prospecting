export type View = 'prospecting' | 'autoscrape' | 'retier' | 'lists' | 'analytics' | 'settings'

type Props = {
  active: View
  onNavigate: (v: View) => void
  userName?: string
  businessName?: string
}

type NavItem = {
  id: View
  label: string
  icon: React.ReactNode
  disabled?: boolean
  badge?: string
}

export function Sidebar({ active, onNavigate, userName, businessName }: Props) {
  const items: NavItem[] = [
    { id: 'prospecting', label: 'Prospecting', icon: <TargetIcon /> },
    { id: 'autoscrape', label: 'Auto-scrape', icon: <SparklesIcon />, badge: 'Beta' },
    { id: 'retier', label: 'Re-tier ClickUp', icon: <RefreshIcon /> },
    { id: 'lists', label: 'Lists', icon: <ListIcon />, disabled: true },
    { id: 'analytics', label: 'Analytics', icon: <ChartIcon />, disabled: true },
    { id: 'settings', label: 'Settings', icon: <GearIcon /> },
  ]

  const initial = (userName || businessName || 'R').trim().charAt(0).toUpperCase()

  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="flex h-14 items-center gap-2.5 border-b border-slate-200 px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-600 text-white">
          <BoltIcon />
        </div>
        <span className="text-sm font-semibold tracking-tight text-slate-900">
          {businessName || 'Reboot'}
        </span>
      </div>

      <nav className="flex-1 space-y-0.5 px-3 py-4">
        <div className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Workspace
        </div>
        {items.map((it) => {
          const isActive = it.id === active
          return (
            <button
              key={it.id}
              disabled={it.disabled}
              onClick={() => !it.disabled && onNavigate(it.id)}
              className={`group flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition ${
                isActive
                  ? 'bg-brand-50 font-medium text-brand-700'
                  : it.disabled
                    ? 'cursor-not-allowed text-slate-400'
                    : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
              }`}
            >
              <span
                className={`${
                  isActive
                    ? 'text-brand-600'
                    : it.disabled
                      ? 'text-slate-300'
                      : 'text-slate-400 group-hover:text-slate-600'
                }`}
              >
                {it.icon}
              </span>
              <span className="flex-1">{it.label}</span>
              {it.badge && (
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                    isActive ? 'bg-brand-100 text-brand-700' : 'bg-slate-100 text-slate-500'
                  }`}
                >
                  {it.badge}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      <div className="border-t border-slate-200 p-3">
        <button
          onClick={() => onNavigate('settings')}
          className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-slate-50"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-600">
            {initial}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-slate-900">
              {userName || 'Set your name'}
            </div>
            <div className="truncate text-xs text-slate-500">{businessName || 'Reboot'}</div>
          </div>
        </button>
      </div>
    </aside>
  )
}

function BoltIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z" />
    </svg>
  )
}
function TargetIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="6" />
      <circle cx="12" cy="12" r="2" />
    </svg>
  )
}
function SparklesIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
    </svg>
  )
}
function RefreshIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
    </svg>
  )
}
function ListIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <line x1="3" y1="6" x2="3.01" y2="6" />
      <line x1="3" y1="12" x2="3.01" y2="12" />
      <line x1="3" y1="18" x2="3.01" y2="18" />
    </svg>
  )
}
function ChartIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
    </svg>
  )
}
function GearIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z" />
    </svg>
  )
}
