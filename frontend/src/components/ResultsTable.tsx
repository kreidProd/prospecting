import { useEffect, useMemo, useState } from 'react'
import { getResults, type ResultRow } from '../api'

const TIERS = ['1A', '1B', '2A', '2B', '3A', '3B', 'SKIP']

type Col = { key: string; label: string; width?: string }
const COLS: Col[] = [
  { key: 'fit_score', label: 'Score', width: '80px' },
  { key: 'business_name', label: 'Business' },
  { key: 'owner_name', label: 'Owner' },
  { key: 'tier', label: 'Tier' },
  { key: 'phone', label: 'Phone' },
  { key: 'phone_verified', label: 'Verified' },
  { key: 'email', label: 'Email' },
  { key: 'website', label: 'Website' },
  { key: 'city', label: 'City' },
  { key: 'reviews', label: 'Reviews' },
  { key: 'rating', label: 'Rating' },
  { key: 'live_ads', label: 'Live', width: '72px' },
  { key: 'signal_google_ads_pixel', label: 'Pixel' },
  { key: 'signal_conversion_event', label: 'Conv' },
  { key: 'signal_gtm', label: 'GTM' },
  { key: 'skip_reason', label: 'Reason' },
]

export function ResultsTable({
  runId,
  initialTier = '1A',
}: {
  runId: string
  initialTier?: string | null
}) {
  const [rows, setRows] = useState<ResultRow[]>([])
  const [total, setTotal] = useState(0)
  const [tier, setTier] = useState<string | null>(initialTier)
  const [search, setSearch] = useState('')
  const [minScore, setMinScore] = useState(0)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getResults(runId, tier ?? undefined)
      .then((r) => {
        if (cancelled) return
        setRows(r.rows)
        setTotal(r.total)
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [runId, tier])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return rows.filter((r) => {
      if (minScore > 0 && Number(r.fit_score || 0) < minScore) return false
      if (!q) return true
      return [r.business_name, r.city, r.phone, r.website, r.email].some((v) =>
        (v || '').toLowerCase().includes(q)
      )
    })
  }, [rows, search, minScore])

  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-4 py-3">
        <div className="flex items-center gap-1.5">
          <FilterPill active={!tier} onClick={() => setTier(null)}>
            All
          </FilterPill>
          {TIERS.map((t) => (
            <FilterPill key={t} active={tier === t} onClick={() => setTier(t)}>
              {t}
            </FilterPill>
          ))}
        </div>

        <div className="ml-2 flex items-center gap-2 border-l border-slate-200 pl-3">
          <label className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
            Score ≥
          </label>
          <input
            type="number"
            min={0}
            max={100}
            step={5}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value) || 0)}
            className="w-16 rounded-md border border-slate-200 bg-white px-2 py-1 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
          />
        </div>

        <div className="relative ml-auto">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            className="w-56 rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
          />
          <SearchIcon />
        </div>

        <span className="text-xs text-slate-500">
          {loading ? 'loading…' : `${filtered.length} of ${total}`}
        </span>
      </div>

      <div className="max-h-[600px] overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-50">
            <tr>
              {COLS.map((c) => (
                <th
                  key={c.key}
                  style={c.width ? { width: c.width } : undefined}
                  className="whitespace-nowrap border-b border-slate-200 px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500"
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((r, i) => (
              <>
                <tr
                  key={i}
                  onClick={() => setExpanded(expanded === i ? null : i)}
                  className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50"
                >
                  {COLS.map((c) => (
                    <td key={c.key} className="whitespace-nowrap px-3 py-2.5 text-slate-700">
                      {renderCell(c.key, r[c.key], r)}
                    </td>
                  ))}
                </tr>
                {expanded === i && (
                  <tr className="bg-slate-50">
                    <td colSpan={COLS.length} className="px-4 py-3">
                      <ScoreBreakdown row={r} />
                    </td>
                  </tr>
                )}
              </>
            ))}
            {filtered.length === 0 && !loading && (
              <tr>
                <td colSpan={COLS.length} className="px-4 py-10 text-center text-sm text-slate-400">
                  No rows match this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ScoreBreakdown({ row }: { row: ResultRow }) {
  const breakdown = (row.score_breakdown || '').split(';').map((s) => s.trim()).filter(Boolean)
  const adSource = row.ad_status_source
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold uppercase tracking-wider text-slate-500">
          Score breakdown:
        </span>
        {breakdown.length === 0 ? (
          <span className="text-slate-400">no signals fired</span>
        ) : (
          breakdown.map((b, j) => (
            <span
              key={j}
              className="rounded-md bg-white px-2 py-0.5 text-slate-700 ring-1 ring-inset ring-slate-200"
            >
              {b}
            </span>
          ))
        )}
      </div>
      <div className="flex flex-wrap items-center gap-4 text-slate-500">
        <span>
          <span className="font-semibold uppercase tracking-wider">Ad source:</span>{' '}
          <code className="rounded bg-white px-1.5 py-0.5 ring-1 ring-inset ring-slate-200">
            {adSource || 'n/a'}
          </code>
          {adSource && !adSource.includes('google_transparency') && !adSource.includes('meta_ads_library') && (
            <span className="ml-1 text-amber-600">
              — tag presence only. Add Meta token + Apify key to verify live ads.
            </span>
          )}
        </span>
        {(row.google_ads_error || row.meta_ads_error) && (
          <span className="text-amber-600">
            {row.google_ads_error && <span>Google: {row.google_ads_error}. </span>}
            {row.meta_ads_error && <span>Meta: {row.meta_ads_error}.</span>}
          </span>
        )}
        {row.location_count && (
          <span>
            <span className="font-semibold uppercase tracking-wider">Locations:</span>{' '}
            {row.location_count}
          </span>
        )}
      </div>
    </div>
  )
}

function FilterPill({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
        active
          ? 'bg-brand-600 text-white shadow-sm'
          : 'bg-white text-slate-600 ring-1 ring-inset ring-slate-200 hover:bg-slate-50'
      }`}
    >
      {children}
    </button>
  )
}

function LiveAdsCell({ row }: { row: ResultRow }) {
  const g = row.google_ads_live === 'yes'
  const m = row.meta_ads_live === 'yes'
  const gCount = Number(row.google_ads_count || 0)
  const mCount = Number(row.meta_ads_count || 0)
  if (!g && !m) {
    // Distinguish "checked and nothing" from "never checked"
    const checked = row.google_ads_live === 'no' || row.meta_ads_live === 'no'
    return <span className="text-slate-300" title={checked ? 'No live ads found' : 'Not verified'}>—</span>
  }
  return (
    <div className="flex items-center gap-1">
      {g && (
        <span
          title={`Google Ads Transparency: ${gCount} live ad${gCount === 1 ? '' : 's'}`}
          className="inline-flex items-center gap-0.5 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-600/20"
        >
          G{gCount > 0 && <span className="tabular-nums">·{gCount}</span>}
        </span>
      )}
      {m && (
        <span
          title={`Meta Ads Library: ${mCount} live ad${mCount === 1 ? '' : 's'}`}
          className="inline-flex items-center gap-0.5 rounded bg-sky-50 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700 ring-1 ring-inset ring-sky-600/20"
        >
          M{mCount > 0 && <span className="tabular-nums">·{mCount}</span>}
        </span>
      )}
    </div>
  )
}

function ScoreCell({ score }: { score: number }) {
  const color =
    score >= 75
      ? 'bg-emerald-500'
      : score >= 50
        ? 'bg-sky-500'
        : score >= 25
          ? 'bg-amber-500'
          : 'bg-slate-300'
  const text =
    score >= 75
      ? 'text-emerald-700'
      : score >= 50
        ? 'text-sky-700'
        : score >= 25
          ? 'text-amber-700'
          : 'text-slate-500'
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-10 overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${score}%` }} />
      </div>
      <span className={`text-xs font-semibold tabular-nums ${text}`}>{score}</span>
    </div>
  )
}

function renderCell(col: string, val: string | undefined, row: ResultRow) {
  if (col === 'fit_score') return <ScoreCell score={Number(val || 0)} />

  if (col === 'live_ads') return <LiveAdsCell row={row} />

  if (col === 'owner_name') {
    if (!val) return <span className="text-slate-300">—</span>
    const src = row.owner_source
    const srcLabel =
      src === 'website' ? 'site'
      : src === 'bbb' ? 'BBB'
      : src === 'csv' ? 'CSV'
      : ''
    return (
      <span className="inline-flex items-center gap-1.5" title={`Source: ${src || 'unknown'}`}>
        <span className="text-slate-900">{val}</span>
        {srcLabel && (
          <span className="rounded bg-slate-100 px-1 py-0.5 text-[10px] font-medium text-slate-500">
            {srcLabel}
          </span>
        )}
      </span>
    )
  }



  if (col === 'tier') {
    if (!val) return <span className="text-slate-300">—</span>
    const styles: Record<string, string> = {
      '1A': 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
      '1B': 'bg-emerald-50 text-emerald-600 ring-emerald-500/15',
      '2A': 'bg-sky-50 text-sky-700 ring-sky-600/20',
      '2B': 'bg-sky-50 text-sky-600 ring-sky-500/15',
      '3A': 'bg-violet-50 text-violet-700 ring-violet-600/20',
      '3B': 'bg-violet-50 text-violet-600 ring-violet-500/15',
      SKIP: 'bg-slate-100 text-slate-500 ring-slate-500/10',
    }
    return (
      <span
        className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${
          styles[val] || styles.SKIP
        }`}
      >
        {val}
      </span>
    )
  }

  if (!val) return <span className="text-slate-300">—</span>

  if (col === 'phone_verified') {
    if (val === 'yes')
      return (
        <span className="inline-flex items-center gap-1 text-emerald-700">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
            <polyline points="20 6 9 17 4 12" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="text-xs">match</span>
        </span>
      )
    return (
      <span className="inline-flex items-center gap-1 text-amber-600" title="Phone not found on site — verify manually">
        <span className="text-xs">unverified</span>
      </span>
    )
  }

  if (col.startsWith('signal_')) {
    if (val === 'yes')
      return (
        <span className="inline-flex h-5 w-5 items-center justify-center rounded bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
            <polyline points="20 6 9 17 4 12" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      )
    if (val === 'no') return <span className="text-slate-300">—</span>
    return <span className="text-slate-300">—</span>
  }

  if (col === 'skip_reason') {
    return <span className="font-mono text-xs text-slate-500">{val}</span>
  }

  if (col === 'website') {
    return (
      <a
        href={val.startsWith('http') ? val : `https://${val}`}
        target="_blank"
        rel="noreferrer"
        onClick={(e) => e.stopPropagation()}
        className="text-brand-600 hover:underline"
      >
        {val.replace(/^https?:\/\/(www\.)?/, '').split('/')[0]}
      </a>
    )
  }

  if (col === 'business_name') {
    return <span className="font-medium text-slate-900">{val}</span>
  }

  if (col === 'rating') {
    const n = Number(val)
    if (!n) return <span className="text-slate-300">—</span>
    return <span className="tabular-nums">{n.toFixed(1)} ★</span>
  }

  return val
}

function SearchIcon() {
  return (
    <svg
      className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  )
}
