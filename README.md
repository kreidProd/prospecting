# Reboot Prospector

AI-assisted lead prospecting tool. Upload scraped CSVs, dedupe against your existing prospect list, detect ad/analytics pixels on each lead's homepage, and tier them for outreach.

## Phase 1 (what's built now)

- Upload scraped CSV (Outscraper, Apollo, any Google Maps export)
- Optional dedup against an existing ClickUp export
- Parallel homepage fetch + pixel detection (Google Ads, Meta, GA4, GTM, LinkedIn, TikTok, Hotjar)
- Preliminary tier classification: `1A_or_1B` / `2A` / `2B` / `SKIP`
- Live progress, filterable results table, enriched CSV download, run history

## Stack

- **Backend:** FastAPI + SQLite (run history) + `requests` + stdlib `concurrent.futures`
- **Frontend:** React + Vite + Tailwind (TypeScript)

## One-time setup

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Frontend

```bash
cd frontend
npm install
```

## Running (two terminals)

**Terminal 1 — backend** (port 8000):

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --reload --port 8000
```

**Terminal 2 — frontend** (port 5173):

```bash
cd frontend
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api/*` to the backend, so uploads and run control just work.

## Using it

1. Export your ClickUp prospects to CSV (ClickUp list → "…" menu → Export → CSV). Drop it in the **Existing CSV** zone.
2. Scrape a new city on Outscraper. Drop the CSV in the **Scraped CSV** zone.
3. Name the run (e.g. `OKC roofers — April`) and hit **Run pipeline**.
4. Watch tier distribution populate live. When it's done, filter to `1A_or_1B` and download the enriched CSV.

## Headless CLI

The pipeline is also callable standalone, no frontend needed:

```bash
cd backend
source .venv/bin/activate
python pipeline.py \
  --scraped /path/to/scraped.csv \
  --existing /path/to/clickup_export.csv \
  --out /path/to/enriched.csv \
  --workers 20
```

## Tier heuristic (preliminary — you confirm the final tier)

| Signal                                 | Tier        |
| -------------------------------------- | ----------- |
| Google Ads pixel detected (`AW-…`)     | `1A_or_1B`  |
| Meta Pixel + ≥10 reviews               | `2A`        |
| ≥20 reviews, no paid pixel             | `2A`        |
| 5–19 reviews                           | `2B`        |
| No website / dead site / low signal    | `SKIP`      |

The `1A_or_1B` rows are the hot list — they're currently or recently running paid Google search. The final 1A vs 1B split (currently running vs. recently stopped) requires a Google Ads Transparency Center check, which is Phase 3.

## Data

- Uploaded CSVs: `backend/data/uploads/`
- Enriched outputs: `backend/data/outputs/`
- Run history (SQLite): `backend/data/prospector.db`

## Coming next

- **Phase 2:** Auto-scrape — multi-select city × industry, kick off Outscraper jobs, skip the manual export step
- **Phase 3:** Google Ads Transparency Center check (split 1A vs 1B), ClickUp push, scheduled re-scans
