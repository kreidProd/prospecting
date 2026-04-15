import { downloadUrl, type HistoryRun } from '../api'

type Props = {
  runs: HistoryRun[]
  activeId?: string | null
  onSelect?: (id: string) => void
}

export function RunHistory({ runs, activeId, onSelect }: Props) {
  return (
    <aside className="h-fit rounded-xl border border-slate-200 bg-white shadow-card">
      <div className="border-b border-slate-200 px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-900">Recent runs</h3>
      </div>
      <ul className="divide-y divide-slate-100">
        {runs.length === 0 && (
          <li className="px-4 py-6 text-xs text-slate-400">
            No runs yet. Upload a CSV to start.
          </li>
        )}
        {runs.map((h) => {
          const isActive = h.id === activeId
          return (
            <li
              key={h.id}
              className={`group px-4 py-3 transition ${
                isActive ? 'bg-brand-50/60' : 'hover:bg-slate-50'
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect?.(h.id)}
                className="w-full text-left"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 truncate text-sm font-medium text-slate-900">
                    {h.name}
                  </div>
                  <StatusBadge status={h.status} />
                </div>
                <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                  <span>
                    {h.processed}/{h.total} rows
                  </span>
                  {h.started_at && (
                    <>
                      <span className="text-slate-300">·</span>
                      <span>{formatRelative(h.started_at)}</span>
                    </>
                  )}
                </div>
              </button>
              {h.status === 'done' && (
                <a
                  href={downloadUrl(h.id)}
                  download
                  onClick={(e) => e.stopPropagation()}
                  className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-brand-700 hover:text-brand-800"
                >
                  ⬇ Download CSV
                </a>
              )}
            </li>
          )
        })}
      </ul>
    </aside>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    done: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
    running: 'bg-sky-50 text-sky-700 ring-sky-600/20',
    queued: 'bg-slate-100 text-slate-600 ring-slate-500/10',
    error: 'bg-red-50 text-red-700 ring-red-600/20',
  }
  return (
    <span
      className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset ${
        map[status] || map.queued
      }`}
    >
      {status}
    </span>
  )
}

function formatRelative(ts: number) {
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return new Date(ts * 1000).toLocaleDateString()
}
