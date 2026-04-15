# Deploy Guide — Stage 1 (single-user, online)

Stage 1 puts your prospector on the public internet behind a password, with the same
code you run locally. Two services, ~30 minutes.

- **Backend (FastAPI + scraper + pipeline)** → Railway (Docker)
- **Frontend (React/Vite)** → Cloudflare Pages

---

## 1. Backend on Railway

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**.
2. Select `kreidProd/prospecting`.
3. Railway will detect `backend/Dockerfile` and `backend/railway.json`. If it doesn't:
   - Settings → **Root Directory**: `backend`
   - Settings → **Builder**: Dockerfile
4. **Add a Volume** (Settings → Volumes):
   - Mount path: `/data`
   - Size: 1 GB (plenty for SQLite + CSVs)
   - This keeps your settings, run history, and output CSVs across deploys.
5. **Environment variables** (Settings → Variables):
   | Variable | Value | Why |
   |---|---|---|
   | `DATA_DIR` | `/data` | Tells the app where the persistent volume is mounted |
   | `APP_USERNAME` | `admin` (or anything) | Basic auth username |
   | `APP_PASSWORD` | *pick a strong password* | Basic auth password |
   | `ALLOWED_ORIGINS` | *your Pages URL, e.g. `https://prospector.pages.dev`* | CORS allowlist (only needed if you hit Railway directly; the Pages proxy is same-origin so this can stay `*` for now) |
6. **Deploy**. Wait for green build.
7. Settings → **Networking** → **Generate Domain**. Copy the URL (e.g.
   `https://prospector-backend-production.up.railway.app`).
8. Verify: open `<railway-url>/api/health` — should return `{"ok": true}` with no auth prompt.
9. Verify auth works: open `<railway-url>/api/runs` — browser should prompt for username/password.

---

## 2. Frontend on Cloudflare Pages

1. Go to [pages.cloudflare.com](https://pages.cloudflare.com) → **Create a project** →
   **Connect to Git** → select `kreidProd/prospecting`.
2. **Build settings**:
   | Setting | Value |
   |---|---|
   | Framework preset | Vite |
   | Build command | `npm run build` |
   | Build output directory | `dist` |
   | Root directory | `frontend` |
3. **Environment variables** (important — this is how the Pages Function finds your backend):
   | Variable | Value |
   |---|---|
   | `API_BASE_URL` | *paste your Railway URL from step 1.7, **no trailing slash*** |
4. **Save and Deploy**. Wait for green build.
5. Cloudflare gives you a URL like `https://prospector.pages.dev`.
6. Open it — your browser will prompt for the username/password you set in Railway. Enter them.

---

## 3. Lock down CORS (optional hardening)

Once you've confirmed the flow works, tighten the Railway backend CORS so only your
Pages URL can hit it:

- Railway → Variables → `ALLOWED_ORIGINS` = `https://prospector.pages.dev`
- Redeploy (Railway auto-redeploys on variable change).

The Pages Function proxy makes the browser see everything as same-origin, so this
doesn't break anything — it just keeps randos from hitting your API directly.

---

## 4. Custom domain (optional)

- Cloudflare Pages → your project → **Custom domains** → add `prospector.yourdomain.com`.
- If the domain is already on Cloudflare DNS, it's instant; otherwise add the CNAME
  record Cloudflare tells you to.

---

## Day-to-day updates

Push to `main` on GitHub. Both Railway and Pages auto-deploy in ~1 minute.

Frontend tweaks → Pages rebuilds. Backend tweaks → Railway rebuilds the Docker image.
Your SQLite DB and CSVs persist on the Railway volume through deploys.

---

## Troubleshooting

- **502 from Pages when hitting `/api/...`** — `API_BASE_URL` is missing or has a
  typo. Re-deploy after fixing.
- **Browser endlessly prompts for password** — `APP_PASSWORD` env mismatch or typo.
- **Lost run history after deploy** — the Railway volume isn't mounted. Re-check step
  1.4 and confirm `DATA_DIR=/data`.
- **401 on `/api/health`** — shouldn't happen, it's whitelisted. If it does, confirm
  the path is literally `/api/health` (not `/api/health/`).
