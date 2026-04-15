import { useEffect, useState } from 'react'
import { Sidebar, type View } from './components/Sidebar'
import { UploadZone } from './components/UploadZone'
import { ResultsTable } from './components/ResultsTable'
import { RunHistory } from './components/RunHistory'
import { AutoScrapePanel } from './components/AutoScrapePanel'
import { RetierPanel } from './components/RetierPanel'
import { Settings } from './components/Settings'
import { TierSelector } from './components/TierSelector'
import { TierLegend } from './components/TierLegend'
import {
  startRun,
  getRun,
  listRuns,
  downloadUrl,
  getSettings,
  type RunState,
  type UploadResult,
  type HistoryRun,
  type AppSettings,
} from './api'

const TIER_META: Record<string, { label: string; color: string; text: string }> = {
  '1A': { label: '1A', color: 'bg-emerald-500', text: 'text-emerald-700' },
  '1B': { label: '1B', color: 'bg-emerald-400', text: 'text-emerald-600' },
  '2A': { label: '2A', color: 'bg-sky-500', text: 'text-sky-700' },
  '2B': { label: '2B', color: 'bg-sky-400', text: 'text-sky-600' },
  '3A': { label: '3A', color: 'bg-violet-500', text: 'text-violet-700' },
  '3B': { label: '3B', color: 'bg-violet-400', text: 'text-violet-600' },
  SKIP: { label: 'Skip', color: 'bg-slate-400', text: 'text-slate-600' },
}
const TIER_ORDER = ['1A', '1B', '2A', '2B', '3A', '3B', 'SKIP']

const VIEW_META: Record<View, { eyebrow: string; title: string }> = {
  prospecting: { eyebrow: 'Prospecting', title: 'Enrich & tier leads' },
  autoscrape: { eyebrow: 'Prospecting', title: 'Auto-scrape by city × industry' },
  retier: { eyebrow: 'Prospecting', title: 'Re-tier your ClickUp list' },
  lists: { eyebrow: 'Lists', title: 'Saved lists' },
  analytics: { eyebrow: 'Analytics', title: 'Performance' },
  settings: { eyebrow: 'Workspace', title: 'Settings' },
}

export default function App() {
  const [view, setView] = useState<View>('prospecting')
  const [settings, setSettings] = useState<AppSettings | null>(null)

  const [scraped, setScraped] = useState<UploadResult | null>(null)
  const [existing, setExisting] = useState<UploadResult | null>(null)
  const [runName, setRunName] = useState('')
  const [targetTiers, setTargetTiers] = useState<string[]>(['1A', '1B'])
  const [run, setRun] = useState<RunState | null>(null)
  const [history, setHistory] = useState<HistoryRun[]>([])
  const [err, setErr] = useState<string | null>(null)

  async function refreshHistory() {
    try {
      setHistory(await listRuns())
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    refreshHistory()
    getSettings().then(setSettings).catch(() => {})
  }, [])

  // Refresh settings when returning to a view that uses them
  useEffect(() => {
    if (view === 'prospecting' || view === 'autoscrape') {
      getSettings().then(setSettings).catch(() => {})
    }
  }, [view])

  useEffect(() => {
    if (!run) return
    if (run.status === 'done' || run.status === 'error') return
    const t = setInterval(async () => {
      try {
        const s = await getRun(run.id)
        setRun(s)
        if (s.status === 'done' || s.status === 'error') refreshHistory()
      } catch {
        /* ignore */
      }
    }, 700)
    return () => clearInterval(t)
  }, [run?.id, run?.status])

  async function handleStart() {
    if (!scraped) {
      setErr('Upload a scraped CSV first.')
      return
    }
    if (targetTiers.length === 0) {
      setErr('Pick at least one tier to target.')
      return
    }
    setErr(null)
    try {
      const { run_id } = await startRun(
        scraped.file_id,
        existing?.file_id ?? null,
        runName || scraped.filename,
        targetTiers
      )
      const s = await getRun(run_id)
      setRun(s)
      refreshHistory()
    } catch (e: any) {
      setErr(e.message || 'Failed to start run')
    }
  }

  const isRunning = run?.status === 'running' || run?.status === 'queued'
  const pct = run && run.total > 0 ? Math.round((run.processed / run.total) * 100) : 0
  const tierDist =
    run?.summary?.tier_distribution ?? (run?.status === 'running' ? run.tier_counts : null)

  // Keep an elapsed-time ticker running while the job is in flight
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!isRunning) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [isRunning])
  const elapsedSec =
    run?.started_at ? Math.max(0, Math.floor(now / 1000 - run.started_at)) : 0

  // Update browser tab title with progress + completion state
  useEffect(() => {
    const base = 'Reboot Prospector'
    if (!run) {
      document.title = base
      return
    }
    if (run.status === 'running' || run.status === 'queued') {
      document.title = `(${pct}%) ${run.processed}/${run.total || '…'} · ${base}`
    } else if (run.status === 'done') {
      document.title = `✓ Done · ${base}`
    } else if (run.status === 'error') {
      document.title = `✗ Error · ${base}`
    }
    return () => {
      document.title = base
    }
  }, [run?.status, run?.processed, run?.total, pct])

  const phaseLabel = (() => {
    if (!run) return ''
    const sp = run.scrape_progress
    if (run.phase === 'queued' || run.status === 'queued')
      return 'Queued — preparing dedup set…'
    if (run.phase === 'scraping' && sp)
      return `Scraping · ${sp.done}/${sp.total} Apify jobs complete${sp.failed ? ` (${sp.failed} failed)` : ''}`
    if (run.phase === 'downloading')
      return 'Downloading scraped results…'
    if (run.phase === 'enriching' && run.total === 0)
      return 'Enriching — loading merged CSV…'
    if (run.phase === 'enriching' && run.processed === 0)
      return `Enriching — fetching first responses (${run.total} total)…`
    if (run.phase === 'enriching')
      return `Enriching · ${run.processed}/${run.total}`
    if (run.status === 'running' && run.total === 0) return 'Starting — loading your CSV…'
    if (run.status === 'running' && run.processed === 0)
      return `Fetching first responses (${run.total} total)…`
    if (run.status === 'running') return `Enriching · ${run.processed}/${run.total}`
    if (run.status === 'done' || run.phase === 'done') return 'Complete'
    if (run.status === 'error' || run.phase === 'error') return 'Error'
    return run.status
  })()

  const phasePct = (() => {
    if (!run) return 0
    if (run.phase === 'scraping' && run.scrape_progress && run.scrape_progress.total > 0) {
      return Math.round(
        ((run.scrape_progress.done + run.scrape_progress.failed) / run.scrape_progress.total) * 100
      )
    }
    return pct
  })()

  const meta = VIEW_META[view]

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        active={view}
        onNavigate={setView}
        userName={settings?.user_name}
        businessName={settings?.business_name}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-slate-200 bg-white px-8">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">
              {meta.eyebrow}
            </div>
            <h1 className="text-base font-semibold text-slate-900">{meta.title}</h1>
          </div>
          {view === 'prospecting' && run && (
            <div className="flex items-center gap-3 text-xs text-slate-500">
              <span className="capitalize">{run.status}</span>
              <span className="text-slate-300">·</span>
              <span>
                {run.processed}/{run.total || '?'}
              </span>
            </div>
          )}
        </header>

        <main className="flex-1 overflow-auto">
          <div className="mx-auto max-w-[1400px] p-8">
            {view === 'prospecting' && (
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
                <div className="space-y-6">
                  <section>
                    <StepHeader
                      num={1}
                      title="Upload your lead data"
                      description="Drop a fresh scrape and your existing prospect list. We'll dedupe against what you already have."
                    />
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <UploadZone
                        label="Scraped CSV"
                        hint="From Outscraper, Apollo, or any Maps scraper"
                        kind="scraped"
                        value={scraped}
                        onChange={setScraped}
                      />
                      <UploadZone
                        label="Existing CSV (optional)"
                        hint="Your current ClickUp export — used for dedup"
                        kind="existing"
                        value={existing}
                        onChange={setExisting}
                      />
                    </div>
                  </section>

                  <section>
                    <StepHeader
                      num={2}
                      title="Pick target tiers"
                      description="Choose which tiers land in your exported CSV. Full classification still runs — this just filters the output."
                    />
                    <div className="space-y-3">
                      <TierLegend />
                      <TierSelector value={targetTiers} onChange={setTargetTiers} />
                    </div>
                  </section>

                  <section>
                    <StepHeader
                      num={3}
                      title="Run the pipeline"
                      description="Fetch each homepage, detect ad pixels, score every row, filter to your target tiers."
                    />
                    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
                      <div className="flex flex-wrap items-center gap-3">
                        <input
                          className="min-w-[240px] flex-1 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
                          placeholder="Run name (e.g. 'OKC roofers — April')"
                          value={runName}
                          onChange={(e) => setRunName(e.target.value)}
                        />
                        <button
                          onClick={handleStart}
                          disabled={!scraped || isRunning}
                          className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400 disabled:shadow-none"
                        >
                          {isRunning ? (
                            <>
                              <Spinner />
                              Running
                            </>
                          ) : (
                            <>
                              <PlayIcon />
                              Run pipeline
                            </>
                          )}
                        </button>
                      </div>

                      {err && (
                        <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
                          {err}
                        </div>
                      )}

                      {run && (
                        <div className="mt-5 space-y-4">
                          {run.source === 'auto_scrape' && typeof run.filtered_non_roofing === 'number' && run.filtered_non_roofing > 0 && (
                            <div className="flex flex-wrap items-center gap-2 text-xs">
                              <span className="text-slate-500">Name filter:</span>
                              <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-slate-700 ring-1 ring-inset ring-slate-500/10">
                                Dropped {run.filtered_non_roofing.toLocaleString()} non-roofing
                                {typeof run.scraped_count === 'number' && (
                                  <> · kept {run.scraped_count.toLocaleString()}</>
                                )}
                              </span>
                            </div>
                          )}
                          {run.dedup_sources && (
                            <div className="flex flex-wrap items-center gap-2 text-xs">
                              <span className="text-slate-500">Deduping against:</span>
                              {run.dedup_sources.clickup_count > 0 && (
                                <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
                                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                                  ClickUp · {run.dedup_sources.clickup_count.toLocaleString()} prospects
                                </span>
                              )}
                              {run.dedup_sources.clickup_error && (
                                <span className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-0.5 text-amber-700 ring-1 ring-inset ring-amber-600/20">
                                  ⚠ ClickUp: {run.dedup_sources.clickup_error}
                                </span>
                              )}
                              {run.dedup_sources.uploaded_csv && (
                                <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-slate-600 ring-1 ring-inset ring-slate-500/10">
                                  Uploaded CSV
                                </span>
                              )}
                              {!run.dedup_sources.clickup_count &&
                                !run.dedup_sources.clickup_error &&
                                !run.dedup_sources.uploaded_csv && (
                                  <span className="text-slate-400">
                                    None — duplicates may sneak through. Add ClickUp in Settings.
                                  </span>
                                )}
                            </div>
                          )}

                          <div>
                            <div className="mb-1.5 flex items-center justify-between gap-2 text-xs">
                              <div className="flex items-center gap-2 text-slate-600">
                                {isRunning && (
                                  <span className="relative flex h-2 w-2">
                                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-500 opacity-60" />
                                    <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-600" />
                                  </span>
                                )}
                                <span className="font-medium">{phaseLabel}</span>
                                {isRunning && (
                                  <span className="text-slate-400">
                                    · {formatElapsed(elapsedSec)} elapsed
                                  </span>
                                )}
                              </div>
                              <span className="tabular-nums text-slate-500">{pct}%</span>
                            </div>
                            <div
                              className={`h-2 overflow-hidden rounded-full bg-slate-100 ${
                                isRunning ? 'ring-1 ring-brand-500/20' : ''
                              }`}
                            >
                              <div
                                className={`h-full rounded-full bg-brand-600 transition-all ${
                                  isRunning && pct < 100 ? 'animate-pulse' : ''
                                }`}
                                style={{ width: `${phasePct}%`, minWidth: isRunning ? '2px' : 0 }}
                              />
                            </div>
                          </div>

                          {tierDist && Object.keys(tierDist).length > 0 && (
                            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
                              {TIER_ORDER.map((t) => {
                                const tm = TIER_META[t]
                                const n = tierDist[t] ?? 0
                                return (
                                  <div
                                    key={t}
                                    className="rounded-lg border border-slate-200 bg-white px-4 py-3"
                                  >
                                    <div className="flex items-center gap-2">
                                      <span className={`h-2 w-2 rounded-full ${tm.color}`} />
                                      <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                        {tm.label}
                                      </span>
                                    </div>
                                    <div className={`mt-1 text-2xl font-semibold ${tm.text}`}>
                                      {n}
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          )}

                          {run.status === 'done' && run.summary && (
                            <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
                              <div className="text-xs text-slate-500">
                                <span className="font-medium text-slate-700">
                                  {run.summary.total_rows}
                                </span>{' '}
                                input ·{' '}
                                <span className="font-medium text-slate-700">
                                  {run.summary.duplicates}
                                </span>{' '}
                                duplicates ·{' '}
                                <span className="font-medium text-slate-700">
                                  {run.summary.processed}
                                </span>{' '}
                                enriched
                              </div>
                              <a
                                href={downloadUrl(run.id)}
                                download
                                className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
                              >
                                <DownloadIcon />
                                Download CSV
                              </a>
                            </div>
                          )}

                          {run.status === 'error' && (
                            <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
                              {run.error}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </section>

                  {run?.status === 'done' && (
                    <section>
                      <StepHeader
                        num={4}
                        title="Review & export"
                        description="Sorted by fit score. Click any row to see the score breakdown. Unfilter to audit what was skipped."
                      />
                      <ResultsTable
                        runId={run.id}
                        initialTier={targetTiers.length === 1 ? targetTiers[0] : null}
                      />
                    </section>
                  )}
                </div>

                <RunHistory
                  runs={history}
                  activeId={run?.id ?? null}
                  onSelect={async (id) => {
                    try {
                      const s = await getRun(id)
                      setRun(s)
                    } catch {
                      /* ignore */
                    }
                  }}
                />
              </div>
            )}

            {view === 'autoscrape' && (
              <div className="mx-auto max-w-3xl space-y-6">
                <section>
                  <StepHeader
                    num={1}
                    title="Pick your geography & vertical"
                    description="Multi-select cities and industries. We'll kick off one scrape job per combination."
                  />
                  {!settings?.apify_api_token_set && (
                    <div className="mb-4 flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                      <span className="mt-0.5">⚠</span>
                      <div>
                        <div className="font-medium">Apify API token not configured.</div>
                        <div className="mt-0.5 text-xs">
                          Add it in{' '}
                          <button
                            onClick={() => setView('settings')}
                            className="font-medium underline underline-offset-2 hover:text-amber-900"
                          >
                            Settings
                          </button>{' '}
                          before running auto-scrape.
                        </div>
                      </div>
                    </div>
                  )}
                  <AutoScrapePanel
                    onStarted={async (runId) => {
                      try {
                        const s = await getRun(runId)
                        setRun(s)
                        setView('prospecting')
                        refreshHistory()
                      } catch {
                        /* ignore */
                      }
                    }}
                  />
                </section>
              </div>
            )}

            {view === 'retier' && (
              <div className="mx-auto max-w-3xl space-y-6">
                <StepHeader
                  num={1}
                  title="Upload & re-tier"
                  description="Drop your ClickUp CSV export. We'll re-visit every website, re-detect ad pixels, and re-score every prospect with the latest tier logic."
                />
                <RetierPanel
                  onStarted={async (runId) => {
                    try {
                      const s = await getRun(runId)
                      setRun(s)
                      setView('prospecting')
                      refreshHistory()
                    } catch {
                      /* ignore */
                    }
                  }}
                />
              </div>
            )}

            {view === 'settings' && (
              <div className="mx-auto max-w-3xl">
                <Settings />
              </div>
            )}

            {(view === 'lists' || view === 'analytics') && (
              <div className="mx-auto max-w-xl py-20 text-center">
                <div className="text-6xl">🚧</div>
                <h2 className="mt-4 text-lg font-semibold text-slate-900">Coming soon</h2>
                <p className="mt-1 text-sm text-slate-500">
                  This view isn't built yet. It'll light up in a future phase.
                </p>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}

function StepHeader({
  num,
  title,
  description,
}: {
  num: number
  title: string
  description: string
}) {
  return (
    <div className="mb-3 flex items-start gap-3">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-900 text-[11px] font-semibold text-white">
        {num}
      </div>
      <div>
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        <p className="text-xs text-slate-500">{description}</p>
      </div>
    </div>
  )
}

function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <path d="M8 5v14l11-7z" />
    </svg>
  )
}
function DownloadIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  )
}
function Spinner() {
  return (
    <svg className="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
      <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    </svg>
  )
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m < 60) return `${m}m ${s.toString().padStart(2, '0')}s`
  const h = Math.floor(m / 60)
  return `${h}h ${(m % 60).toString().padStart(2, '0')}m`
}
