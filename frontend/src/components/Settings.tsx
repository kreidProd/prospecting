import { useEffect, useState } from 'react'
import {
  getSettings,
  saveSettings,
  testClickUp,
  testApify,
  testMetaAds,
  type AppSettings,
  type ClickUpTestResult,
  type ApifyTestResult,
  type MetaTestResult,
} from '../api'

export function Settings() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [draft, setDraft] = useState<Partial<AppSettings>>({})
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<ClickUpTestResult | null>(null)
  const [testingApify, setTestingApify] = useState(false)
  const [apifyResult, setApifyResult] = useState<ApifyTestResult | null>(null)
  const [testingMeta, setTestingMeta] = useState(false)
  const [metaResult, setMetaResult] = useState<MetaTestResult | null>(null)

  useEffect(() => {
    getSettings()
      .then((s) => {
        setSettings(s)
        setDraft(s)
      })
      .catch(() => setMsg({ kind: 'err', text: 'Could not load settings' }))
  }, [])

  function set<K extends keyof AppSettings>(key: K, value: AppSettings[K]) {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  async function save() {
    setSaving(true)
    setMsg(null)
    try {
      const patch: Partial<AppSettings> = { ...draft }
      // Don't echo masked values back to the server
      if (patch.outscraper_api_key?.startsWith('••••')) delete patch.outscraper_api_key
      if (patch.clickup_api_key?.startsWith('••••')) delete patch.clickup_api_key
      const updated = await saveSettings(patch)
      setSettings(updated)
      setDraft(updated)
      setMsg({ kind: 'ok', text: 'Settings saved.' })
    } catch (e: any) {
      setMsg({ kind: 'err', text: e.message || 'Save failed' })
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(null), 3500)
    }
  }

  if (!settings)
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-8 text-sm text-slate-500 shadow-card">
        Loading settings…
      </div>
    )

  return (
    <div className="space-y-6">
      <Section
        title="Scraping"
        description="API keys for pulling Google Maps results. Keys are saved locally and shown masked after entry."
      >
        <KeyField
          label="Apify API token"
          hint="Powers auto-scrape. Get it from apify.com → Settings → Integrations → Personal API tokens."
          value={(draft.apify_api_token as string) ?? ''}
          onChange={(v) => set('apify_api_token', v)}
          isSet={settings.apify_api_token_set}
        />

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={async () => {
              setTestingApify(true)
              setApifyResult(null)
              const patch: Partial<AppSettings> = {
                apify_api_token: draft.apify_api_token?.startsWith('••••')
                  ? undefined
                  : draft.apify_api_token,
              }
              if (patch.apify_api_token !== undefined) {
                try {
                  await saveSettings(patch)
                } catch { /* noop */ }
              }
              const r = await testApify()
              setApifyResult(r)
              setTestingApify(false)
            }}
            disabled={testingApify || (!settings.apify_api_token_set && !draft.apify_api_token)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {testingApify ? 'Testing…' : 'Test connection'}
          </button>
          {apifyResult && <ApifyResultBadge result={apifyResult} />}
        </div>

        <div className="border-t border-slate-100 pt-4">
          <KeyField
            label="Outscraper API key (optional fallback)"
            hint="Alternative to Apify. outscraper.com → Profile → API keys."
            value={(draft.outscraper_api_key as string) ?? ''}
            onChange={(v) => set('outscraper_api_key', v)}
            isSet={settings.outscraper_api_key_set}
          />
        </div>
      </Section>

      <Section
        title="Email enrichment"
        description="Verifies and enriches the emails your scraper returns. Pick one."
      >
        <KeyField
          label="Hunter.io API key"
          hint="Best DX. Verifies deliverability + finds emails for domains that scraped blank. ~$49/mo starter."
          value={(draft.hunter_api_key as string) ?? ''}
          onChange={(v) => set('hunter_api_key', v)}
          isSet={settings.hunter_api_key_set}
        />
        <KeyField
          label="NeverBounce API key"
          hint="Cheapest — validation only, ~$0.008 per email. Use if you already have emails and just need deliverability checks."
          value={(draft.neverbounce_api_key as string) ?? ''}
          onChange={(v) => set('neverbounce_api_key', v)}
          isSet={settings.neverbounce_api_key_set}
        />
      </Section>

      <Section
        title="CRM"
        description="Connect ClickUp so every run dedupes against your live prospect list — no more manual CSV exports."
      >
        <KeyField
          label="ClickUp API key"
          hint="Personal token from ClickUp → Apps → API."
          value={(draft.clickup_api_key as string) ?? ''}
          onChange={(v) => set('clickup_api_key', v)}
          isSet={settings.clickup_api_key_set}
        />
        <Field label="ClickUp list ID" hint="Grab it from the list URL — the number after /li/.">
          <input
            type="text"
            value={(draft.clickup_list_id as string) ?? ''}
            onChange={(e) => set('clickup_list_id', e.target.value)}
            placeholder="e.g. 901234567890"
            className={inputCls}
          />
        </Field>

        <div className="flex items-center gap-3 border-t border-slate-100 pt-4">
          <button
            type="button"
            onClick={async () => {
              setTesting(true)
              setTestResult(null)
              // Save draft first so the server has current values
              const patch: Partial<AppSettings> = {
                clickup_api_key: draft.clickup_api_key?.startsWith('••••') ? undefined : draft.clickup_api_key,
                clickup_list_id: draft.clickup_list_id,
              }
              if (patch.clickup_api_key !== undefined || patch.clickup_list_id !== undefined) {
                try {
                  await saveSettings(patch)
                } catch { /* noop */ }
              }
              const r = await testClickUp()
              setTestResult(r)
              setTesting(false)
            }}
            disabled={testing || (!settings.clickup_api_key_set && !draft.clickup_api_key)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          {testResult && <ClickUpResult result={testResult} />}
        </div>
      </Section>

      <Section
        title="Scrape defaults"
        description="Applied to new auto-scrape jobs. You can still override per run."
      >
        <div className="grid grid-cols-2 gap-4">
          <Field label="Default radius (miles)">
            <input
              type="number"
              min={1}
              max={100}
              value={draft.default_radius_miles ?? 25}
              onChange={(e) => set('default_radius_miles', Number(e.target.value) || 25)}
              className={inputCls}
            />
          </Field>
          <Field label="Default limit per query">
            <input
              type="number"
              min={50}
              max={2000}
              step={50}
              value={draft.default_limit ?? 500}
              onChange={(e) => set('default_limit', Number(e.target.value) || 500)}
              className={inputCls}
            />
          </Field>
        </div>
      </Section>

      <Section
        title="Pipeline"
        description="Performance tuning for the enrichment step."
      >
        <div className="grid grid-cols-2 gap-4">
          <Field label="Fetch timeout (seconds)">
            <input
              type="number"
              min={3}
              max={30}
              value={draft.fetch_timeout_seconds ?? 10}
              onChange={(e) => set('fetch_timeout_seconds', Number(e.target.value) || 10)}
              className={inputCls}
            />
          </Field>
          <Field label="Parallel workers">
            <input
              type="number"
              min={1}
              max={50}
              value={draft.pipeline_workers ?? 20}
              onChange={(e) => set('pipeline_workers', Number(e.target.value) || 20)}
              className={inputCls}
            />
          </Field>
        </div>
      </Section>

      <Section title="Profile" description="Shown in the sidebar.">
        <div className="grid grid-cols-2 gap-4">
          <Field label="Business name">
            <input
              type="text"
              value={draft.business_name ?? ''}
              onChange={(e) => set('business_name', e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Your name">
            <input
              type="text"
              value={draft.user_name ?? ''}
              onChange={(e) => set('user_name', e.target.value)}
              className={inputCls}
            />
          </Field>
        </div>
      </Section>

      <div className="sticky bottom-0 flex items-center justify-between rounded-xl border border-slate-200 bg-white/90 p-4 shadow-card backdrop-blur">
        <div className="text-xs text-slate-500">
          {msg ? (
            <span className={msg.kind === 'ok' ? 'text-emerald-600' : 'text-red-600'}>
              {msg.text}
            </span>
          ) : (
            'Settings are stored on this machine only.'
          )}
        </div>
        <button
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-brand-700 disabled:bg-slate-200 disabled:text-slate-400"
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  )
}

const inputCls =
  'w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20'

function Section({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-card">
      <div className="border-b border-slate-100 px-5 py-4">
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
      </div>
      <div className="space-y-4 px-5 py-4">{children}</div>
    </div>
  )
}

function KeyField({
  label,
  hint,
  value,
  onChange,
  isSet,
}: {
  label: string
  hint: string
  value: string
  onChange: (v: string) => void
  isSet: boolean
}) {
  return (
    <Field label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={isSet ? 'Key saved — enter to replace' : 'Paste your key'}
          className={inputCls}
        />
        {isSet && <Badge color="emerald">Connected</Badge>}
      </div>
    </Field>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <label className="mb-1.5 block text-xs font-medium text-slate-700">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

function Badge({ color, children }: { color: 'emerald' | 'amber'; children: React.ReactNode }) {
  const cls =
    color === 'emerald'
      ? 'bg-emerald-50 text-emerald-700 ring-emerald-600/20'
      : 'bg-amber-50 text-amber-700 ring-amber-600/20'
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-md px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${cls}`}
    >
      {children}
    </span>
  )
}

function ApifyResultBadge({ result }: { result: ApifyTestResult }) {
  if (!result.ok) {
    return (
      <div className="flex-1 rounded-md bg-red-50 px-3 py-1.5 text-xs text-red-700">
        {result.error || 'Connection failed.'}
      </div>
    )
  }
  return (
    <div className="flex-1 rounded-md bg-emerald-50 px-3 py-1.5 text-xs text-emerald-800">
      <span className="font-medium">✓ Connected</span> as{' '}
      <span className="font-semibold">{result.username}</span>
      {result.plan && <> · plan <span className="font-semibold">{result.plan}</span></>}
    </div>
  )
}

function ClickUpResult({ result }: { result: ClickUpTestResult }) {
  if (!result.ok) {
    return (
      <div className="flex-1 rounded-md bg-red-50 px-3 py-1.5 text-xs text-red-700">
        {result.error || 'Connection failed.'}
      </div>
    )
  }
  return (
    <div className="flex-1 rounded-md bg-emerald-50 px-3 py-1.5 text-xs text-emerald-800">
      <span className="font-medium">✓ Connected</span> as{' '}
      <span className="font-semibold">{result.user}</span>
      {result.list_name && (
        <>
          {' · list '}
          <span className="font-semibold">{result.list_name}</span>
          {typeof result.task_count === 'number' && (
            <>
              {' ('}
              <span className="font-semibold">{result.task_count}</span> tasks{')'}
            </>
          )}
        </>
      )}
      {result.list_error && (
        <span className="ml-2 text-amber-700">· {result.list_error}</span>
      )}
    </div>
  )
}
