import { useMemo, useState } from 'react'
import { MultiSelect, type Option } from './MultiSelect'
import { TierSelector } from './TierSelector'
import { CITIES } from '../data/cities'
import { INDUSTRIES } from '../data/industries'

type Props = {
  onStarted?: (runId: string) => void
}

export function AutoScrapePanel({ onStarted }: Props) {
  const [cities, setCities] = useState<string[]>([])
  const [industries, setIndustries] = useState<string[]>([])
  const [targetTiers, setTargetTiers] = useState<string[]>(['1A', '1B'])
  const [radius, setRadius] = useState(25)
  const [limit, setLimit] = useState(500)
  const [msg, setMsg] = useState<{ kind: 'info' | 'err'; text: string } | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const cityOptions: Option[] = useMemo(
    () =>
      CITIES.map((c) => ({
        value: `${c.name}|${c.state}`,
        label: `${c.name}, ${c.state}`,
        group: c.region,
      })),
    []
  )

  const industryOptions: Option[] = useMemo(
    () => INDUSTRIES.map((i) => ({ value: i.value, label: i.label, group: i.group })),
    []
  )

  const combos = cities.length * industries.length
  const totalRows = combos * limit
  const estCost = (combos * 5).toFixed(2)

  async function submit() {
    if (cities.length === 0 || industries.length === 0) {
      setMsg({ kind: 'err', text: 'Pick at least one city and one industry.' })
      return
    }
    if (targetTiers.length === 0) {
      setMsg({ kind: 'err', text: 'Pick at least one target tier.' })
      return
    }
    setSubmitting(true)
    setMsg(null)
    try {
      const r = await fetch('/api/scrape', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          cities,
          industries,
          radius_miles: radius,
          limit,
          target_tiers: targetTiers,
        }),
      })
      const data = await r.json()
      if (!r.ok) {
        setMsg({ kind: 'err', text: data.detail || 'Scrape failed' })
      } else if (data.run_id) {
        setMsg({
          kind: 'info',
          text: `Started auto-scrape. Redirecting you to the run — Apify is pulling leads now (typically 2–10 min per city/industry combo).`,
        })
        onStarted?.(data.run_id)
      } else {
        setMsg({ kind: 'err', text: 'Unexpected response from server.' })
      }
    } catch (e: any) {
      setMsg({ kind: 'err', text: e.message || 'Network error' })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <MultiSelect
            label="Cities"
            placeholder="Pick cities to scrape…"
            options={cityOptions}
            value={cities}
            onChange={setCities}
          />
          <MultiSelect
            label="Industries"
            placeholder="Pick industries…"
            options={industryOptions}
            value={industries}
            onChange={setIndustries}
          />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600">
              Radius (miles)
            </label>
            <input
              type="number"
              min={1}
              max={100}
              value={radius}
              onChange={(e) => setRadius(Number(e.target.value) || 25)}
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600">
              Limit per query
            </label>
            <input
              type="number"
              min={50}
              max={2000}
              step={50}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value) || 500)}
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
            />
          </div>
        </div>
      </div>

      <TierSelector value={targetTiers} onChange={setTargetTiers} />

      <div className="flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-card text-xs text-slate-600">
        <Stat label="Jobs" value={combos || '—'} />
        <Stat label="Max rows" value={totalRows ? totalRows.toLocaleString() : '—'} />
        <Stat label="Est. cost" value={combos ? `~$${estCost}` : '—'} />
        <Stat
          label="Exporting"
          value={targetTiers.length > 0 ? targetTiers.join(', ') : '—'}
        />
        <div className="ml-auto">
          <button
            onClick={submit}
            disabled={
              submitting ||
              cities.length === 0 ||
              industries.length === 0 ||
              targetTiers.length === 0
            }
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400 disabled:shadow-none"
          >
            {submitting ? 'Queuing…' : 'Start auto-scrape'}
          </button>
        </div>
      </div>

      {msg && (
        <div
          className={`rounded-lg px-3 py-2 text-sm ${
            msg.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-brand-50 text-brand-700'
          }`}
        >
          {msg.text}
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
        {label}
      </div>
      <div className="font-medium text-slate-800">{value}</div>
    </div>
  )
}
