export type UploadResult = {
  file_id: string
  filename: string
  size: number
  kind: 'scraped' | 'existing'
}

export type RunSummary = {
  total_rows: number
  duplicates: number
  processed: number
  tier_distribution: Record<string, number>
}

export type RunPhase =
  | 'queued'
  | 'scraping'
  | 'downloading'
  | 'enriching'
  | 'done'
  | 'error'

export type RunState = {
  id: string
  name: string
  status: 'queued' | 'running' | 'done' | 'error'
  phase?: RunPhase
  source?: 'upload' | 'auto_scrape'
  processed: number
  total: number
  tier_counts: Record<string, number>
  started_at: number | null
  finished_at: number | null
  summary: RunSummary | null
  error: string | null
  output_path: string
  scraped_count?: number
  filtered_non_roofing?: number
  scrape_progress?: { done: number; failed: number; total: number }
  dedup_sources?: {
    clickup_count: number
    clickup_error: string | null
    uploaded_csv: boolean
  }
}

export type HistoryRun = {
  id: string
  name: string
  status: string
  started_at: number | null
  finished_at: number | null
  processed: number
  total: number
  duplicates: number
  tier_counts: Record<string, number>
}

export type ResultRow = Record<string, string>

export async function uploadFile(
  kind: 'scraped' | 'existing',
  file: File
): Promise<UploadResult> {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`/api/upload/${kind}`, { method: 'POST', body: fd })
  if (!r.ok) throw new Error(`Upload failed: ${r.status}`)
  return r.json()
}

export type ClickUpUploadResult = UploadResult & {
  row_count: number
  filtered_irrelevant: number
  industry_filter: string | null
  mapping: Record<string, string | null>
}

export async function uploadClickUpCsv(
  file: File,
  industryFilter?: string | null
): Promise<ClickUpUploadResult> {
  const fd = new FormData()
  fd.append('file', file)
  const q = industryFilter ? `?industry_filter=${encodeURIComponent(industryFilter)}` : ''
  const r = await fetch(`/api/upload/clickup${q}`, { method: 'POST', body: fd })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || `Upload failed: ${r.status}`)
  return data
}

export type IndustryFilter = { id: string; tokens: string[] }
export async function getIndustryFilters(): Promise<IndustryFilter[]> {
  const r = await fetch('/api/industry-filters')
  if (!r.ok) return []
  const data = await r.json().catch(() => ({ filters: [] }))
  return data.filters || []
}

export async function startRun(
  scraped_id: string,
  existing_id: string | null,
  name: string,
  target_tiers?: string[] | null,
  skip_clickup_dedup?: boolean
): Promise<{ run_id: string }> {
  const r = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      scraped_id,
      existing_id,
      name,
      target_tiers,
      skip_clickup_dedup: skip_clickup_dedup ?? false,
    }),
  })
  if (!r.ok) throw new Error(`Start run failed: ${r.status}`)
  return r.json()
}

export async function getRun(run_id: string): Promise<RunState> {
  const r = await fetch(`/api/runs/${run_id}`)
  if (!r.ok) throw new Error(`Get run failed: ${r.status}`)
  return r.json()
}

export async function getResults(
  run_id: string,
  tier?: string
): Promise<{ total: number; rows: ResultRow[] }> {
  const q = tier ? `?tier=${encodeURIComponent(tier)}` : ''
  const r = await fetch(`/api/runs/${run_id}/results${q}`)
  if (!r.ok) throw new Error(`Get results failed: ${r.status}`)
  return r.json()
}

export async function listRuns(): Promise<HistoryRun[]> {
  const r = await fetch('/api/runs')
  if (!r.ok) throw new Error('List runs failed')
  return r.json()
}

export function downloadUrl(run_id: string): string {
  return `/api/runs/${run_id}/download`
}

export type AppSettings = {
  outscraper_api_key: string
  outscraper_api_key_set: boolean
  apify_api_token: string
  apify_api_token_set: boolean
  hunter_api_key: string
  hunter_api_key_set: boolean
  neverbounce_api_key: string
  neverbounce_api_key_set: boolean
  clickup_api_key: string
  clickup_api_key_set: boolean
  clickup_list_id: string
  default_radius_miles: number
  default_limit: number
  fetch_timeout_seconds: number
  pipeline_workers: number
  business_name: string
  user_name: string
}

export async function getSettings(): Promise<AppSettings> {
  const r = await fetch('/api/settings')
  if (!r.ok) throw new Error('Get settings failed')
  return r.json()
}

export async function saveSettings(patch: Partial<AppSettings>): Promise<AppSettings> {
  const r = await fetch('/api/settings', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) throw new Error('Save settings failed')
  return r.json()
}

export type ClickUpTestResult = {
  ok: boolean
  user?: string
  list_name?: string
  task_count?: number
  list_error?: string
  error?: string
}

export async function testClickUp(): Promise<ClickUpTestResult> {
  const r = await fetch('/api/clickup/test', { method: 'POST' })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) {
    return { ok: false, error: data.detail || `HTTP ${r.status}` }
  }
  return data
}

export type ApifyTestResult = {
  ok: boolean
  username?: string
  plan?: string
  error?: string
}

export async function testApify(): Promise<ApifyTestResult> {
  const r = await fetch('/api/apify/test', { method: 'POST' })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) {
    return { ok: false, error: data.detail || `HTTP ${r.status}` }
  }
  return data
}
