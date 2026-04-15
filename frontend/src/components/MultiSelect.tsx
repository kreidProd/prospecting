import { useEffect, useMemo, useRef, useState } from 'react'

export type Option = {
  value: string
  label: string
  group?: string
  meta?: string
}

type Props = {
  label: string
  placeholder?: string
  options: Option[]
  value: string[]
  onChange: (next: string[]) => void
  maxChips?: number
}

export function MultiSelect({
  label,
  placeholder = 'Select…',
  options,
  value,
  onChange,
  maxChips = 4,
}: Props) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const selectedSet = useMemo(() => new Set(value), [value])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        (o.meta || '').toLowerCase().includes(q) ||
        (o.group || '').toLowerCase().includes(q)
    )
  }, [options, query])

  const grouped = useMemo(() => {
    const map = new Map<string, Option[]>()
    filtered.forEach((o) => {
      const g = o.group || ''
      if (!map.has(g)) map.set(g, [])
      map.get(g)!.push(o)
    })
    return Array.from(map.entries())
  }, [filtered])

  function toggle(v: string) {
    if (selectedSet.has(v)) onChange(value.filter((x) => x !== v))
    else onChange([...value, v])
  }

  function selectAll() {
    const all = Array.from(new Set([...value, ...filtered.map((o) => o.value)]))
    onChange(all)
  }

  function clear() {
    onChange([])
  }

  const selectedOptions = value
    .map((v) => options.find((o) => o.value === v))
    .filter(Boolean) as Option[]

  const overflow = selectedOptions.length - maxChips

  return (
    <div ref={rootRef} className="relative">
      <label className="mb-1.5 block text-xs font-medium text-slate-600">{label}</label>
      <button
        type="button"
        onClick={() => setOpen((x) => !x)}
        className={`flex min-h-[40px] w-full items-center gap-2 rounded-lg border bg-white px-3 py-1.5 text-left text-sm transition ${
          open
            ? 'border-brand-500 ring-2 ring-brand-500/20'
            : 'border-slate-200 hover:border-slate-300'
        }`}
      >
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          {selectedOptions.length === 0 && (
            <span className="text-slate-400">{placeholder}</span>
          )}
          {selectedOptions.slice(0, maxChips).map((o) => (
            <span
              key={o.value}
              className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 text-xs font-medium text-brand-700"
            >
              {o.label}
              <span
                role="button"
                onClick={(e) => {
                  e.stopPropagation()
                  toggle(o.value)
                }}
                className="text-brand-400 hover:text-brand-700"
              >
                ×
              </span>
            </span>
          ))}
          {overflow > 0 && (
            <span className="inline-flex items-center rounded-md bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
              +{overflow} more
            </span>
          )}
        </div>
        <ChevronIcon open={open} />
      </button>

      {open && (
        <div className="absolute left-0 right-0 z-20 mt-1.5 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-pop">
          <div className="border-b border-slate-100 p-2">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search…"
              className="w-full rounded-md bg-slate-50 px-2.5 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none"
            />
          </div>

          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-1.5 text-xs text-slate-500">
            <span>
              {value.length} selected
              {filtered.length !== options.length && ` · ${filtered.length} shown`}
            </span>
            <div className="flex gap-3">
              <button
                onClick={selectAll}
                className="font-medium text-brand-600 hover:underline"
              >
                Select visible
              </button>
              <button
                onClick={clear}
                className="font-medium text-slate-500 hover:text-slate-900"
              >
                Clear
              </button>
            </div>
          </div>

          <div className="max-h-72 overflow-auto py-1">
            {filtered.length === 0 && (
              <div className="px-3 py-6 text-center text-sm text-slate-400">
                No matches for “{query}”
              </div>
            )}
            {grouped.map(([group, items]) => (
              <div key={group || 'none'}>
                {group && (
                  <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                    {group}
                  </div>
                )}
                {items.map((o) => {
                  const on = selectedSet.has(o.value)
                  return (
                    <button
                      key={o.value + (o.meta || '')}
                      type="button"
                      onClick={() => toggle(o.value)}
                      className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm transition hover:bg-slate-50 ${
                        on ? 'text-slate-900' : 'text-slate-700'
                      }`}
                    >
                      <span
                        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border transition ${
                          on
                            ? 'border-brand-600 bg-brand-600 text-white'
                            : 'border-slate-300 bg-white'
                        }`}
                      >
                        {on && <CheckIcon />}
                      </span>
                      <span className="flex-1 truncate">{o.label}</span>
                      {o.meta && (
                        <span className="text-xs text-slate-400">{o.meta}</span>
                      )}
                    </button>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      className={`shrink-0 text-slate-400 transition ${open ? 'rotate-180' : ''}`}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  )
}
