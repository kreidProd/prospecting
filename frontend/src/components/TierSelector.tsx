const TIER_OPTIONS: { id: string; label: string; description: string }[] = [
  { id: '1A', label: '1A', description: 'Running ads · no conversion tracking' },
  { id: '1B', label: '1B', description: 'Recently ran ads · no conversion tracking' },
  { id: '2A', label: '2A', description: 'Never ran ads · no analytics' },
  { id: '2B', label: '2B', description: 'Never ran ads · has GA/GTM' },
  { id: '3A', label: '3A', description: 'Multi-location · running or recent ads' },
  { id: '3B', label: '3B', description: 'Multi-location · GA/GTM only' },
]

type Props = {
  value: string[]
  onChange: (next: string[]) => void
}

export function TierSelector({ value, onChange }: Props) {
  function toggle(id: string) {
    if (value.includes(id)) onChange(value.filter((v) => v !== id))
    else onChange([...value, id])
  }

  function preset(ids: string[]) {
    onChange(ids)
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <div className="text-sm font-medium text-slate-900">Target tiers</div>
          <div className="text-xs text-slate-500">
            Only selected tiers land in your downloaded CSV. Full classification still happens
            under the hood.
          </div>
        </div>
        <div className="flex gap-1.5 text-xs">
          <PresetBtn onClick={() => preset(['1A', '1B'])}>Hot (1A/1B)</PresetBtn>
          <PresetBtn onClick={() => preset(['1A', '1B', '2A', '2B'])}>All single-loc</PresetBtn>
          <PresetBtn onClick={() => preset(['3A', '3B'])}>Multi-loc</PresetBtn>
          <PresetBtn onClick={() => preset(TIER_OPTIONS.map((t) => t.id))}>All</PresetBtn>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {TIER_OPTIONS.map((t) => {
          const on = value.includes(t.id)
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => toggle(t.id)}
              className={`flex items-start gap-2.5 rounded-lg border p-3 text-left transition ${
                on
                  ? 'border-brand-500 bg-brand-50/60 ring-1 ring-brand-500/20'
                  : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
              }`}
            >
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border transition ${
                  on ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-300 bg-white'
                }`}
              >
                {on && (
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                )}
              </span>
              <div className="min-w-0">
                <div
                  className={`text-sm font-semibold ${
                    on ? 'text-brand-700' : 'text-slate-900'
                  }`}
                >
                  Tier {t.label}
                </div>
                <div className="text-xs text-slate-500">{t.description}</div>
              </div>
            </button>
          )
        })}
      </div>

      <div className="mt-3 text-xs text-slate-500">
        {value.length === 0 ? (
          <span className="text-amber-600">⚠ Pick at least one tier to export.</span>
        ) : (
          <>
            Exporting <span className="font-medium text-slate-700">{value.join(', ')}</span>.
          </>
        )}
      </div>
    </div>
  )
}

function PresetBtn({
  onClick,
  children,
}: {
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-md bg-slate-100 px-2 py-1 font-medium text-slate-600 hover:bg-slate-200 hover:text-slate-900"
    >
      {children}
    </button>
  )
}
