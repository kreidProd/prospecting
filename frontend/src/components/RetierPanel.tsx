import { useEffect, useRef, useState } from 'react'
import { TierSelector } from './TierSelector'
import {
  uploadClickUpCsv,
  startRun,
  getIndustryFilters,
  type ClickUpUploadResult,
  type IndustryFilter,
} from '../api'

type Props = {
  onStarted?: (runId: string) => void
}

const INDUSTRY_LABELS: Record<string, string> = {
  roofing: 'Roofing / Exteriors',
  hvac: 'HVAC',
  plumbing: 'Plumbing',
  electrical: 'Electrical',
  landscaping: 'Landscaping',
  pest_control: 'Pest control',
  solar: 'Solar',
  painting: 'Painting',
  flooring: 'Flooring',
  restoration: 'Restoration',
}

const FIELD_LABELS: Record<string, string> = {
  business_name: 'Business name',
  phone: 'Phone',
  website: 'Website',
  email: 'Email',
  address: 'Address',
  city: 'City',
  state: 'State',
  total_reviews: 'Total reviews',
  rating: 'Rating',
}

export function RetierPanel({ onStarted }: Props) {
  const [uploaded, setUploaded] = useState<ClickUpUploadResult | null>(null)
  const [runName, setRunName] = useState('')
  const [targetTiers, setTargetTiers] = useState<string[]>(['1A', '1B', '2A', '2B', '3A', '3B'])
  const [industry, setIndustry] = useState<string>('roofing')
  const [availableFilters, setAvailableFilters] = useState<IndustryFilter[]>([])
  const [uploading, setUploading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [lastFile, setLastFile] = useState<File | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    getIndustryFilters().then(setAvailableFilters).catch(() => {})
  }, [])

  async function handleFile(file: File) {
    setErr(null)
    setUploading(true)
    setLastFile(file)
    try {
      const r = await uploadClickUpCsv(file, industry || null)
      setUploaded(r)
      if (!runName) setRunName(`Re-tier · ${file.name.replace(/\.csv$/i, '')}`)
    } catch (e: any) {
      setErr(e.message || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  async function handleIndustryChange(next: string) {
    setIndustry(next)
    // If a file was already uploaded, re-upload with the new filter so the
    // preview + row count stay consistent with what the run will actually use.
    if (lastFile) {
      setUploading(true)
      setErr(null)
      try {
        const r = await uploadClickUpCsv(lastFile, next || null)
        setUploaded(r)
      } catch (e: any) {
        setErr(e.message || 'Re-filter failed')
      } finally {
        setUploading(false)
      }
    }
  }

  async function handleRun() {
    if (!uploaded) return
    if (targetTiers.length === 0) {
      setErr('Pick at least one tier.')
      return
    }
    setErr(null)
    setSubmitting(true)
    try {
      const { run_id } = await startRun(
        uploaded.file_id,
        null,
        runName || `Re-tier · ${uploaded.filename}`,
        targetTiers,
        true, // skip_clickup_dedup — we're re-tiering the list itself
      )
      onStarted?.(run_id)
    } catch (e: any) {
      setErr(e.message || 'Failed to start run')
    } finally {
      setSubmitting(false)
    }
  }

  const missingRequired = uploaded && !uploaded.mapping.business_name
  const mappedCount = uploaded
    ? Object.values(uploaded.mapping).filter(Boolean).length
    : 0

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
        <h3 className="text-sm font-semibold text-slate-900">Upload a ClickUp CSV export</h3>
        <p className="mt-0.5 text-xs text-slate-500">
          In ClickUp, open your prospect list → <span className="font-medium">⋯ menu</span> →{' '}
          <span className="font-medium">Export</span> → CSV. We'll auto-map common columns
          (Task Name, Phone, Website, Email, Address, City, State).
        </p>

        <div className="mt-4">
          <label className="mb-1.5 block text-xs font-medium text-slate-700">
            Industry filter
          </label>
          <select
            value={industry}
            onChange={(e) => handleIndustryChange(e.target.value)}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
          >
            <option value="">None — keep every row</option>
            {availableFilters.map((f) => (
              <option key={f.id} value={f.id}>
                {INDUSTRY_LABELS[f.id] || f.id} · matches {f.tokens.join(', ')}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-slate-500">
            Drops any business whose name doesn't contain at least one of these words.
            Cuts general contractors, handymen, and off-vertical leads before scoring.
          </p>
        </div>

        <div className="mt-4">
          <div
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              const f = e.dataTransfer.files[0]
              if (f) handleFile(f)
            }}
            className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-200 bg-slate-50 px-6 py-8 text-center transition hover:border-brand-400 hover:bg-brand-50/40"
          >
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              className="text-slate-400"
            >
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <div className="mt-2 text-sm font-medium text-slate-700">
              {uploading ? 'Uploading…' : 'Drop ClickUp CSV or click to browse'}
            </div>
            <div className="mt-0.5 text-xs text-slate-500">Accepts .csv only</div>
            <input
              ref={inputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) handleFile(f)
              }}
            />
          </div>
        </div>

        {uploaded && (
          <div className="mt-5 space-y-3 border-t border-slate-100 pt-4">
            <div className="flex items-center justify-between text-sm">
              <div className="font-medium text-slate-900">{uploaded.filename}</div>
              <div className="text-xs text-slate-500">
                {uploaded.row_count.toLocaleString()} rows · {mappedCount}/9 columns mapped
              </div>
            </div>
            {uploaded.filtered_irrelevant > 0 && (
              <div className="flex items-center gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <span>🧹</span>
                <span>
                  Dropped <span className="font-semibold">{uploaded.filtered_irrelevant.toLocaleString()}</span>{' '}
                  rows whose name didn't match{' '}
                  <span className="font-semibold">{INDUSTRY_LABELS[uploaded.industry_filter || ''] || uploaded.industry_filter}</span>
                  . Change or disable the filter above if that's too aggressive.
                </span>
              </div>
            )}
            {missingRequired && (
              <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
                Couldn't find a Task Name / Business column. Re-export from ClickUp with the
                name column included.
              </div>
            )}
            <details className="text-xs">
              <summary className="cursor-pointer text-slate-600 hover:text-slate-900">
                Column mapping
              </summary>
              <div className="mt-2 grid grid-cols-1 gap-1 rounded-md bg-slate-50 p-3 sm:grid-cols-2">
                {Object.entries(uploaded.mapping).map(([k, src]) => (
                  <div key={k} className="flex items-center justify-between gap-2">
                    <span className="text-slate-500">{FIELD_LABELS[k] || k}</span>
                    <span
                      className={`truncate font-mono text-[11px] ${
                        src ? 'text-slate-800' : 'text-slate-400'
                      }`}
                    >
                      {src || '— not found'}
                    </span>
                  </div>
                ))}
              </div>
            </details>
          </div>
        )}
      </section>

      <section>
        <h3 className="mb-3 text-sm font-semibold text-slate-900">Pick tiers to keep</h3>
        <TierSelector value={targetTiers} onChange={setTargetTiers} />
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
        <div className="flex flex-wrap items-center gap-3">
          <input
            className="min-w-[240px] flex-1 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
            placeholder="Run name (e.g. 'ClickUp re-tier — April')"
            value={runName}
            onChange={(e) => setRunName(e.target.value)}
          />
          <button
            onClick={handleRun}
            disabled={!uploaded || !!missingRequired || submitting || targetTiers.length === 0}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
          >
            {submitting ? 'Starting…' : 'Re-tier list'}
          </button>
        </div>

        {err && (
          <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>
        )}

        <p className="mt-3 text-xs text-slate-500">
          ClickUp dedup is disabled on this flow — we want to re-score every existing prospect,
          not skip them as duplicates.
        </p>
      </section>
    </div>
  )
}
