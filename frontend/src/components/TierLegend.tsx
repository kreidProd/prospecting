import { useState } from 'react'

type TierDef = {
  id: string
  dot: string
  title: string
  signals: string
  sell: string
}

const TIER_DEFS: TierDef[] = [
  {
    id: '1A',
    dot: 'bg-emerald-500',
    title: 'Running ads · no conversion tracking',
    signals:
      'Google Ads pixel detected on the homepage AND evidence of recent paid traffic (gclid token). No conversion events firing.',
    sell:
      'Hottest target. Actively spending but can\'t measure ROI — every click is guesswork. Easiest "we can help" conversation.',
  },
  {
    id: '1B',
    dot: 'bg-emerald-400',
    title: 'Recently ran ads · no conversion tracking',
    signals:
      'Google Ads pixel installed but no gclid evidence on the fetch. Either paused, or running but the fetch missed it (verification via Transparency Center will sharpen this).',
    sell:
      'Warm target. Budget history + installed infrastructure = they\'ve been a buyer before.',
  },
  {
    id: '2A',
    dot: 'bg-sky-500',
    title: 'Never ran ads · no analytics',
    signals:
      'No ad pixels, no GA4, no GTM, no Meta Pixel. Clean slate website.',
    sell:
      'Cold but greenfield. Full funnel build. Longer sales cycle — they\'re not used to measuring.',
  },
  {
    id: '2B',
    dot: 'bg-sky-400',
    title: 'Never ran ads · has analytics',
    signals:
      'GA4 or GTM installed, but no advertising pixels. Tracking-curious but not paying for traffic yet.',
    sell:
      'Educated buyer. They get tracking, just haven\'t pulled the trigger on paid acquisition. Easier "scale what you have" pitch.',
  },
  {
    id: '3A',
    dot: 'bg-violet-500',
    title: 'Multi-location · running or recent ads',
    signals:
      'Business has 2+ locations (detected via /locations links or multi-address parsing) AND ad pixel signals. 100+ total reviews qualifying floor.',
    sell:
      'Bigger deals, more stakeholders. Usually already have an agency — displacement sell. Multi-location = budget.',
  },
  {
    id: '3B',
    dot: 'bg-violet-400',
    title: 'Multi-location · analytics only',
    signals:
      'Business has 2+ locations and analytics installed, but no ad pixels.',
    sell:
      'Long sales cycle, enterprise-ish motion. Worth the pursuit for the ARR.',
  },
  {
    id: 'SKIP',
    dot: 'bg-slate-400',
    title: 'Skipped',
    signals:
      'Either: (a) conversion tracking already firing correctly — they don\'t need us; or (b) failed qualifying floor (<40 reviews or <4.0 rating for single-loc; <100 reviews for multi-loc).',
    sell:
      'Not a fit. Shown in the results table only when you clear the tier filter — useful for auditing what got cut.',
  },
]

export function TierLegend() {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-5 py-3 text-left"
      >
        <div>
          <div className="text-sm font-semibold text-slate-900">What do the tiers mean?</div>
          <div className="text-xs text-slate-500">
            How the pipeline classifies every lead · qualifying floors · why each tier is a sales target
          </div>
        </div>
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`text-slate-400 transition ${open ? 'rotate-180' : ''}`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="space-y-3 border-t border-slate-100 px-5 py-4">
          <div className="rounded-md bg-amber-50 p-3 text-xs text-amber-900">
            <span className="font-semibold">Accuracy note:</span> ad detection is currently a proxy
            — we look for Google Ads / GTM / GA4 / Meta pixels in the HTML, plus gclid tokens.
            Having a pixel means they\'ve run ads at some point, not that they\'re spending today.
            Transparency Center verification (coming) will promote 1B → 1A only when a live ad is
            confirmed.
          </div>

          <ul className="divide-y divide-slate-100">
            {TIER_DEFS.map((t) => (
              <li key={t.id} className="py-3 first:pt-0 last:pb-0">
                <div className="flex items-start gap-3">
                  <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${t.dot}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="text-sm font-semibold text-slate-900">
                        Tier {t.id}
                      </span>
                      <span className="text-sm text-slate-700">— {t.title}</span>
                    </div>
                    <div className="mt-1.5 grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
                      <div>
                        <div className="font-medium text-slate-500">Signals</div>
                        <div className="text-slate-700">{t.signals}</div>
                      </div>
                      <div>
                        <div className="font-medium text-slate-500">Why you call them</div>
                        <div className="text-slate-700">{t.sell}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </li>
            ))}
          </ul>

          <div className="rounded-md bg-slate-50 p-3 text-xs text-slate-600">
            <div className="font-semibold text-slate-800">Qualifying floors (applied before tiering)</div>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              <li>Single-location: 40+ Google reviews AND 4.0+ rating, or they\'re skipped.</li>
              <li>Multi-location: 100+ total reviews across locations, or they\'re skipped.</li>
              <li>Phone number must appear on the actual website (not just Google Maps) — otherwise flagged as unverified.</li>
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}
