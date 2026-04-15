import os
import time
import uuid
import secrets
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

import csv

from pipeline import run_pipeline
from db import DB
from settings_store import SettingsStore
from clickup_client import ClickUpClient, ClickUpError
from apify_client import ApifyClient, ApifyError, TERMINAL_STATUSES
from ads_verifier import MetaAdsVerifier, GoogleAdsApifyVerifier


ROOT = Path(__file__).parent
# DATA_DIR env var points to Railway's mounted volume in prod; defaults to ./data locally.
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
UPLOADS = DATA / "uploads"
OUTPUTS = DATA / "outputs"
for d in (DATA, UPLOADS, OUTPUTS):
    d.mkdir(parents=True, exist_ok=True)

# Comma-separated origins for prod (e.g. https://prospector.pages.dev). Use "*" locally.
_allowed = os.environ.get("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS = [o.strip() for o in _allowed.split(",")] if _allowed != "*" else ["*"]

# --- HTTP basic auth (single-user Stage 1) ----------------------------------
# Set APP_PASSWORD env var in Railway. If unset, auth is disabled (local dev).
APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
_auth = HTTPBasic(auto_error=False)

# Paths that skip auth (for platform healthchecks).
_PUBLIC_PATHS = {"/api/health", "/health", "/"}


def require_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(_auth),
):
    if request.url.path in _PUBLIC_PATHS:
        return
    if not APP_PASSWORD:
        return  # auth disabled for local dev
    if not credentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(credentials.username, APP_USERNAME)
    pass_ok = secrets.compare_digest(credentials.password, APP_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


app = FastAPI(
    title="Reboot Prospector",
    dependencies=[Depends(require_auth)],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


db = DB(DATA / "prospector.db")
settings = SettingsStore(DATA / "settings.json")

RUNS: dict = {}
RUN_LOCK = threading.Lock()


def _get_clickup_client() -> Optional[ClickUpClient]:
    """Return a ClickUp client if key is configured, else None."""
    key = settings.get("clickup_api_key")
    return ClickUpClient(key) if key else None


def _build_ad_verifiers():
    """Build Google + Meta ad verifiers from settings. Either may be None."""
    if not settings.get("verify_live_ads"):
        return None, None

    google = None
    apify_token = settings.get("apify_api_token")
    actor_id = settings.get("apify_transparency_actor")
    if apify_token and actor_id:
        google = GoogleAdsApifyVerifier(
            ApifyClient(apify_token),
            actor_id=actor_id,
        )

    meta = None
    meta_token = settings.get("meta_ads_access_token")
    if meta_token:
        meta = MetaAdsVerifier(meta_token)

    return google, meta


def _fetch_clickup_dedup_rows() -> tuple[list, Optional[str]]:
    """
    If ClickUp is configured, return (rows, None) where rows is a list of
    {business_name, phone, website, email} dicts pulled from the list.
    If not configured or an error occurs, return ([], error_message_or_None).
    """
    client = _get_clickup_client()
    list_id = settings.get("clickup_list_id")
    if not client or not list_id:
        return [], None
    try:
        rows = client.list_tasks(list_id)
        return rows, None
    except ClickUpError as e:
        return [], str(e)


class RunRequest(BaseModel):
    scraped_id: str
    existing_id: Optional[str] = None
    name: str = "Untitled run"
    target_tiers: Optional[list[str]] = None
    skip_clickup_dedup: bool = False


# Canonical pipeline column -> list of normalized header aliases we'll accept.
# Normalization (see _normalize_header) strips emoji, "(type)" suffixes, and
# punctuation so "📞 Phone (phone)" → "phone" matches the "phone" alias.
CLICKUP_FIELD_ALIASES: dict = {
    "business_name": ["task name", "name", "business name", "business", "company", "company name", "title"],
    "phone": ["phone", "phone number", "phone_number", "mobile", "cell"],
    "website": ["website", "website url", "url", "site", "domain"],
    "email": ["email", "email address", "e-mail", "email1"],
    "address": ["address", "street", "street address"],
    "city": ["city"],
    "state": ["state", "state/province", "region"],
    "total_reviews": ["reviews", "review count", "total reviews", "num reviews", "number of reviews"],
    "rating": ["rating", "stars", "score", "google rating"],
    "owner_name": [
        "owner", "owner name", "business owner", "contact", "contact name",
        "primary contact", "point of contact", "poc", "decision maker",
        "first name last name", "full name",
    ],
}

import re
_HEADER_PAREN_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")
_HEADER_NON_WORD = re.compile(r"[^a-z0-9 ]+")


def _normalize_header(h: str) -> str:
    """Normalize a ClickUp header to match against alias list.

    Strips trailing "(short text)" / "(drop down)" / "(url)" etc. type tags,
    removes emoji and non-word chars, collapses whitespace, lowercases.
    """
    s = (h or "").strip()
    # Strip trailing "(...)" type annotation (may repeat)
    while _HEADER_PAREN_SUFFIX.search(s):
        s = _HEADER_PAREN_SUFFIX.sub("", s).strip()
    s = s.lower()
    s = _HEADER_NON_WORD.sub(" ", s)  # kill emoji + punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _map_clickup_csv(
    content: bytes,
    industry_tokens: Optional[list] = None,
) -> tuple[list, dict, int]:
    """Read a ClickUp CSV export and remap its columns to pipeline-expected names.

    Returns (rows, mapping, filtered_irrelevant):
      - rows: list of dicts in canonical format, already filtered if industry_tokens given
      - mapping: {canonical_field: original_header_or_None} for UI display
      - filtered_irrelevant: count of rows dropped because the business name
        didn't contain any industry token
    """
    import io
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    src_headers = reader.fieldnames or []

    # Build normalized-header → original-header index, but prefer the first match
    # so "Email (email)" wins over "Email2 (email)" when both normalize to "email".
    norm_to_src: dict = {}
    for h in src_headers:
        n = _normalize_header(h)
        if n and n not in norm_to_src:
            norm_to_src[n] = h

    mapping: dict = {}
    for canonical, aliases in CLICKUP_FIELD_ALIASES.items():
        found = None
        for a in aliases:
            if a in norm_to_src:
                found = norm_to_src[a]
                break
        mapping[canonical] = found

    rows: list = []
    filtered = 0
    for src_row in reader:
        out = {}
        for canonical, src_key in mapping.items():
            out[canonical] = (src_row.get(src_key, "") if src_key else "") or ""
        if not any(out.values()):
            continue
        if industry_tokens and not _matches_industry(out.get("business_name", ""), industry_tokens):
            filtered += 1
            continue
        rows.append(out)
    return rows, mapping, filtered


@app.post("/api/upload/clickup")
async def upload_clickup(
    file: UploadFile = File(...),
    industry_filter: Optional[str] = None,
):
    """Accept a ClickUp CSV export, auto-map columns, optionally filter by industry.

    `industry_filter` is the id from INDUSTRY_NAME_FILTERS (e.g. 'roofing').
    If set, rows whose business name doesn't contain any of that industry's
    tokens are dropped before the CSV is written.
    """
    fid = uuid.uuid4().hex[:12]
    path = UPLOADS / f"scraped_{fid}.csv"  # stored as 'scraped' so existing run flow picks it up
    content = await file.read()

    tokens = INDUSTRY_NAME_FILTERS.get(industry_filter) if industry_filter else None

    try:
        rows, mapping, filtered = _map_clickup_csv(content, industry_tokens=tokens)
    except Exception as e:
        raise HTTPException(400, f"Could not parse CSV: {e}")

    if not mapping.get("business_name"):
        raise HTTPException(
            400,
            "Couldn't find a business/task name column. "
            "Export from ClickUp with 'Task Name' (or 'Name') included.",
        )
    if not rows:
        if filtered > 0:
            raise HTTPException(
                400,
                f"All {filtered} rows were dropped by the '{industry_filter}' "
                f"name filter. Try a different industry or 'None'.",
            )
        raise HTTPException(400, "CSV had headers but no rows.")

    fields = list(CLICKUP_FIELD_ALIASES.keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    return {
        "file_id": fid,
        "filename": file.filename,
        "size": len(content),
        "kind": "scraped",
        "row_count": len(rows),
        "filtered_irrelevant": filtered,
        "industry_filter": industry_filter,
        "mapping": mapping,
    }


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/upload/{kind}")
async def upload(kind: str, file: UploadFile = File(...)):
    if kind not in ("scraped", "existing"):
        raise HTTPException(400, "kind must be 'scraped' or 'existing'")
    fid = uuid.uuid4().hex[:12]
    path = UPLOADS / f"{kind}_{fid}.csv"
    content = await file.read()
    path.write_bytes(content)
    return {
        "file_id": fid,
        "filename": file.filename,
        "size": len(content),
        "kind": kind,
    }


@app.post("/api/runs")
def start_run(req: RunRequest):
    run_id = uuid.uuid4().hex[:12]
    scraped_path = UPLOADS / f"scraped_{req.scraped_id}.csv"
    if not scraped_path.exists():
        raise HTTPException(404, "scraped file not found")

    existing_path = None
    if req.existing_id:
        cand = UPLOADS / f"existing_{req.existing_id}.csv"
        if cand.exists():
            existing_path = cand

    output_path = OUTPUTS / f"run_{run_id}.csv"

    target_tiers = req.target_tiers or None

    with RUN_LOCK:
        RUNS[run_id] = {
            "id": run_id,
            "name": req.name,
            "status": "queued",
            "processed": 0,
            "total": 0,
            "tier_counts": {},
            "target_tiers": target_tiers,
            "started_at": time.time(),
            "finished_at": None,
            "summary": None,
            "error": None,
            "output_path": str(output_path),
        }
        db.save_run(RUNS[run_id])

    def on_progress(p, t, tier):
        with RUN_LOCK:
            r = RUNS[run_id]
            r["processed"] = p
            r["total"] = t
            r["status"] = "running"
            # Skip placeholder tiers ("starting") from the counts
            if tier and tier != "starting":
                tc = r["tier_counts"]
                tc[tier] = tc.get(tier, 0) + 1

    def worker():
        try:
            if req.skip_clickup_dedup:
                clickup_rows, clickup_err = [], None
            else:
                clickup_rows, clickup_err = _fetch_clickup_dedup_rows()
            with RUN_LOCK:
                RUNS[run_id]["dedup_sources"] = {
                    "clickup_count": len(clickup_rows),
                    "clickup_error": clickup_err,
                    "uploaded_csv": bool(existing_path),
                }
            google_ver, meta_ver = _build_ad_verifiers()
            summary = run_pipeline(
                str(scraped_path),
                str(existing_path) if existing_path else None,
                str(output_path),
                workers=20,
                on_progress=on_progress,
                target_tiers=target_tiers,
                existing_rows=clickup_rows or None,
                google_verifier=google_ver,
                meta_verifier=meta_ver,
            )
            with RUN_LOCK:
                RUNS[run_id]["status"] = "done"
                RUNS[run_id]["summary"] = summary
                RUNS[run_id]["finished_at"] = time.time()
                db.save_run(RUNS[run_id])
        except Exception as e:
            with RUN_LOCK:
                RUNS[run_id]["status"] = "error"
                RUNS[run_id]["error"] = str(e)
                RUNS[run_id]["finished_at"] = time.time()
                db.save_run(RUNS[run_id])

    threading.Thread(target=worker, daemon=True).start()
    return {"run_id": run_id}


def _public_run(r):
    pub = {k: v for k, v in r.items() if k != "summary"}
    s = r.get("summary")
    if s:
        pub["summary"] = {
            "total_rows": s.get("total_rows"),
            "duplicates": s.get("duplicates"),
            "processed": s.get("processed"),
            "tier_distribution": s.get("tier_distribution"),
        }
    return pub


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    with RUN_LOCK:
        r = RUNS.get(run_id)
    if r:
        return _public_run(r)
    saved = db.get_run(run_id)
    if saved:
        return saved
    raise HTTPException(404, "run not found")


@app.get("/api/runs/{run_id}/results")
def get_results(run_id: str, limit: int = 500, tier: Optional[str] = None):
    with RUN_LOCK:
        r = RUNS.get(run_id)
    if not r or not r.get("summary"):
        raise HTTPException(404, "run not done or results not in memory")
    rows = r["summary"]["results"]
    if tier:
        rows = [x for x in rows if x.get("preliminary_tier_hint") == tier]
    return {"total": len(rows), "rows": rows[:limit]}


@app.get("/api/runs/{run_id}/download")
def download(run_id: str):
    with RUN_LOCK:
        r = RUNS.get(run_id)
    path = Path(r["output_path"]) if r else None
    if not path or not path.exists():
        saved = db.get_run(run_id)
        if saved and saved.get("output_path"):
            path = Path(saved["output_path"])
    if not path or not path.exists():
        raise HTTPException(404, "output not available")
    return FileResponse(path, filename=path.name, media_type="text/csv")


@app.get("/api/runs")
def list_runs():
    return db.list_runs()


# --- Settings ------------------------------------------------------------

@app.get("/api/settings")
def get_settings():
    return settings.read_public()


class SettingsPatch(BaseModel):
    outscraper_api_key: Optional[str] = None
    apify_api_token: Optional[str] = None
    hunter_api_key: Optional[str] = None
    neverbounce_api_key: Optional[str] = None
    clickup_api_key: Optional[str] = None
    clickup_list_id: Optional[str] = None
    default_radius_miles: Optional[int] = None
    default_limit: Optional[int] = None
    fetch_timeout_seconds: Optional[int] = None
    pipeline_workers: Optional[int] = None
    business_name: Optional[str] = None
    user_name: Optional[str] = None


@app.post("/api/settings")
def update_settings(patch: SettingsPatch):
    return settings.update({k: v for k, v in patch.model_dump().items() if v is not None})


# --- Auto-scrape ---------------------------------------------------------

class ScrapeRequest(BaseModel):
    cities: list[str]  # "City|ST" tokens
    industries: list[str]
    radius_miles: int = 25
    limit: int = 500
    target_tiers: Optional[list[str]] = None


# Industry-specific name filters. Key is the industry id shown in the UI;
# value is the list of substring tokens a business name (or Google category)
# must contain to count as a match. Stem-style tokens catch plurals/variants
# (e.g. "roof" matches "Roofing", "Roofer", "Roofs").
INDUSTRY_NAME_FILTERS: dict = {
    "roofing": ["roof", "exterior"],
    "hvac": ["hvac", "heating", "cooling", "air condition", " ac "],
    "plumbing": ["plumb", "drain", "sewer", "rooter"],
    "electrical": ["electric", "electrician"],
    "landscaping": ["landscap", "lawn", "yard", "tree service", "turf"],
    "pest_control": ["pest", "termite", "exterminator", "bug"],
    "solar": ["solar"],
    "painting": ["paint"],
    "flooring": ["floor", "carpet", "tile"],
    "restoration": ["restoration", "water damage", "mold", "fire damage"],
}

# Keep the old roofing constant around for the Apify flow that still uses it
ROOFING_NAME_TOKENS = tuple(INDUSTRY_NAME_FILTERS["roofing"])


def _matches_industry(name: str, tokens: list, categories: Optional[list] = None) -> bool:
    """Return True if the name (or Google category) contains any industry token.

    A real roofer almost always puts 'Roof', 'Roofing', or 'Exteriors' in the name.
    Same pattern holds for most trades — the business name is the strongest filter
    for cutting out general contractors, handymen, and unrelated listings.
    """
    if not tokens:
        return True  # no filter configured → keep everything
    hay = (name or "").lower()
    if any(tok in hay for tok in tokens):
        return True
    for c in categories or []:
        cl = (c or "").lower()
        if any(tok in cl for tok in tokens):
            return True
    return False


def _is_roofing_business(name: str, categories: Optional[list] = None) -> bool:
    """Backwards-compat wrapper used by the Apify auto-scrape flow."""
    return _matches_industry(name, list(ROOFING_NAME_TOKENS), categories)


@app.get("/api/industry-filters")
def get_industry_filters():
    """Surface available industry filters (id + tokens) to the frontend."""
    return {
        "filters": [
            {"id": k, "tokens": v} for k, v in INDUSTRY_NAME_FILTERS.items()
        ]
    }


def _apify_items_to_csv(items: list, path) -> tuple[int, int]:
    """Write Apify google-places items into a CSV that the pipeline loader understands.

    Returns (kept, dropped) where dropped is the count of non-roofing businesses
    filtered out by name.
    """
    rows = []
    dropped = 0
    for it in items:
        name = (it.get("title") or it.get("name") or "").strip()
        categories = it.get("categories") or ([it.get("categoryName")] if it.get("categoryName") else [])
        if not _is_roofing_business(name, categories):
            dropped += 1
            continue
        emails = it.get("emails") or it.get("emailsFromWebsite") or []
        email = emails[0] if emails else ""
        rows.append({
            "business_name": name,
            "phone": it.get("phone") or it.get("phoneUnformatted") or "",
            "email": email,
            "website": it.get("website") or it.get("url") or "",
            "address": it.get("address") or it.get("street") or "",
            "city": it.get("city") or "",
            "state": it.get("state") or "",
            "total_reviews": it.get("reviewsCount") or it.get("totalScore") or 0,
            "rating": it.get("totalScore") or it.get("rating") or "",
        })
    with open(path, "w", newline="", encoding="utf-8") as f:
        fields = [
            "business_name", "phone", "email", "website", "address",
            "city", "state", "total_reviews", "rating",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return len(rows), dropped


def _run_autoscrape(run_id, cities, industries, radius, limit, target_tiers):
    """Background worker: Apify scrape → merge → enrich pipeline."""
    token = settings.get("apify_api_token")
    apify = ApifyClient(token)

    with RUN_LOCK:
        RUNS[run_id]["phase"] = "scraping"
        RUNS[run_id]["status"] = "running"

    # --- Phase 1: kick off Apify runs -------------------------------------
    jobs = []
    for city_token in cities:
        parts = (city_token.split("|") + [""])[:2]
        city, state = parts[0].strip(), parts[1].strip()
        location = f"{city}, {state}".strip(", ")
        for industry in industries:
            search = f"{industry} in {location}".strip()
            try:
                data = apify.start_google_places_run(search, location, limit)
                jobs.append({
                    "apify_run_id": data.get("id"),
                    "dataset_id": data.get("defaultDatasetId"),
                    "status": data.get("status") or "RUNNING",
                    "search": search,
                    "error": None,
                })
            except ApifyError as e:
                jobs.append({
                    "apify_run_id": None,
                    "dataset_id": None,
                    "status": "FAILED",
                    "search": search,
                    "error": str(e),
                })

    with RUN_LOCK:
        RUNS[run_id]["scrape_progress"] = {
            "done": sum(1 for j in jobs if j["status"] == "SUCCEEDED"),
            "failed": sum(1 for j in jobs if j["status"] in TERMINAL_STATUSES and j["status"] != "SUCCEEDED"),
            "total": len(jobs),
        }

    # --- Phase 2: poll until all runs reach a terminal state -------------
    deadline = time.time() + 3600  # 1-hour cap
    while time.time() < deadline:
        all_terminal = True
        for j in jobs:
            if j["status"] in TERMINAL_STATUSES or not j["apify_run_id"]:
                continue
            all_terminal = False
            try:
                r = apify.get_run(j["apify_run_id"])
                j["status"] = r.get("status") or j["status"]
                if not j.get("dataset_id"):
                    j["dataset_id"] = r.get("defaultDatasetId")
            except ApifyError as e:
                j["error"] = str(e)

        done = sum(1 for j in jobs if j["status"] == "SUCCEEDED")
        failed = sum(1 for j in jobs if j["status"] in TERMINAL_STATUSES and j["status"] != "SUCCEEDED")
        with RUN_LOCK:
            RUNS[run_id]["scrape_progress"] = {"done": done, "failed": failed, "total": len(jobs)}

        if all_terminal or (done + failed) >= len(jobs):
            break
        time.sleep(15)

    # --- Phase 3: download + merge ----------------------------------------
    with RUN_LOCK:
        RUNS[run_id]["phase"] = "downloading"

    all_items = []
    for j in jobs:
        if j["status"] != "SUCCEEDED" or not j.get("dataset_id"):
            continue
        try:
            items = apify.get_dataset_items(j["dataset_id"])
            all_items.extend(items)
        except ApifyError as e:
            j["error"] = str(e)

    scraped_path = UPLOADS / f"autoscrape_{run_id}.csv"
    scraped_count, filtered_non_roofing = _apify_items_to_csv(all_items, scraped_path)
    with RUN_LOCK:
        RUNS[run_id]["scraped_count"] = scraped_count
        RUNS[run_id]["filtered_non_roofing"] = filtered_non_roofing

    if scraped_count == 0:
        total_returned = len(all_items)
        if filtered_non_roofing and total_returned > 0:
            msg = (
                f"No roofing matches — Apify returned {total_returned} businesses "
                f"but none had 'roof' or 'exterior' in the name. Try different search terms."
            )
        else:
            msg = "No results — all Apify jobs returned empty. Check your search terms or Apify account balance."
        with RUN_LOCK:
            RUNS[run_id]["status"] = "error"
            RUNS[run_id]["phase"] = "error"
            RUNS[run_id]["error"] = msg
            RUNS[run_id]["finished_at"] = time.time()
            db.save_run(RUNS[run_id])
        return

    # --- Phase 4: enrichment pipeline (same as upload flow) ---------------
    with RUN_LOCK:
        RUNS[run_id]["phase"] = "enriching"

    def on_progress(p, t, tier):
        with RUN_LOCK:
            r = RUNS[run_id]
            r["processed"] = p
            r["total"] = t
            if tier and tier != "starting":
                tc = r["tier_counts"]
                tc[tier] = tc.get(tier, 0) + 1

    clickup_rows, clickup_err = _fetch_clickup_dedup_rows()
    with RUN_LOCK:
        RUNS[run_id]["dedup_sources"] = {
            "clickup_count": len(clickup_rows),
            "clickup_error": clickup_err,
            "uploaded_csv": False,
        }

    output_path = Path(RUNS[run_id]["output_path"])
    try:
        google_ver, meta_ver = _build_ad_verifiers()
        summary = run_pipeline(
            str(scraped_path),
            None,
            str(output_path),
            workers=settings.get("pipeline_workers") or 20,
            on_progress=on_progress,
            target_tiers=target_tiers,
            existing_rows=clickup_rows or None,
            google_verifier=google_ver,
            meta_verifier=meta_ver,
        )
        with RUN_LOCK:
            RUNS[run_id]["status"] = "done"
            RUNS[run_id]["phase"] = "done"
            RUNS[run_id]["summary"] = summary
            RUNS[run_id]["finished_at"] = time.time()
            db.save_run(RUNS[run_id])
    except Exception as e:
        with RUN_LOCK:
            RUNS[run_id]["status"] = "error"
            RUNS[run_id]["phase"] = "error"
            RUNS[run_id]["error"] = f"Pipeline: {e}"
            RUNS[run_id]["finished_at"] = time.time()
            db.save_run(RUNS[run_id])


@app.post("/api/scrape")
def queue_scrape(req: ScrapeRequest):
    token = settings.get("apify_api_token")
    if not token:
        raise HTTPException(
            400,
            "Apify API token is required for auto-scrape. Add it in Settings → Scraping.",
        )
    if not req.cities or not req.industries:
        raise HTTPException(400, "Pick at least one city and one industry.")
    if not req.target_tiers:
        raise HTTPException(400, "Pick at least one target tier.")

    run_id = uuid.uuid4().hex[:12]
    output_path = OUTPUTS / f"run_{run_id}.csv"
    name = f"Auto-scrape · {len(req.cities)} cities × {len(req.industries)} industries"

    with RUN_LOCK:
        RUNS[run_id] = {
            "id": run_id,
            "name": name,
            "status": "queued",
            "phase": "queued",
            "source": "auto_scrape",
            "processed": 0,
            "total": 0,
            "tier_counts": {},
            "target_tiers": req.target_tiers,
            "scrape_progress": {"done": 0, "failed": 0, "total": len(req.cities) * len(req.industries)},
            "started_at": time.time(),
            "finished_at": None,
            "summary": None,
            "error": None,
            "output_path": str(output_path),
        }
        db.save_run(RUNS[run_id])

    threading.Thread(
        target=_run_autoscrape,
        args=(run_id, req.cities, req.industries, req.radius_miles, req.limit, req.target_tiers),
        daemon=True,
    ).start()

    return {"run_id": run_id, "status": "queued"}


@app.post("/api/apify/test")
def test_apify():
    token = settings.get("apify_api_token")
    if not token:
        raise HTTPException(400, "Apify token is not set.")
    result = ApifyClient(token).test()
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Connection failed"))
    return result


class GoogleTestRequest(BaseModel):
    domain: str
    actor: Optional[str] = None


@app.post("/api/google/test")
def test_google_ads(body: GoogleTestRequest):
    """Run the configured Apify Transparency actor against a single domain.

    Returns enough raw detail to diagnose both actor-shape and data issues.
    """
    token = settings.get("apify_api_token")
    actor = (body.actor or "").strip() or settings.get("apify_transparency_actor") or "automation-lab~google-ads-scraper"
    if not token:
        raise HTTPException(400, "Apify token is not set — the Transparency actor runs through Apify.")
    dom = (body.domain or "").strip()
    if not dom:
        raise HTTPException(400, "Pass a domain like 'angi.com'.")

    from ads_verifier import _domain_only, _merge_apify_items
    clean = _domain_only(dom)
    client = ApifyClient(token)
    try:
        run = client.start_actor(
            actor,
            run_input={
                "domains": [clean],
                "region": "US",
                "startUrls": [{"url": f"https://adstransparency.google.com/?region=US&domain={clean}"}],
            },
        )
    except Exception as e:
        raise HTTPException(400, f"Could not start actor '{actor}': {e}")

    import time as _t
    run_id = run.get("id")
    dataset_id = run.get("defaultDatasetId")
    deadline = _t.time() + 120
    status_val = run.get("status")
    while status_val not in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"):
        if _t.time() > deadline:
            return {
                "ok": False,
                "actor": actor,
                "domain": clean,
                "error": "Actor run timed out (120s). Actor may exist but be slow — try again or swap actors.",
                "run_id": run_id,
            }
        _t.sleep(3)
        try:
            run = client.get_run(run_id)
        except Exception as e:
            raise HTTPException(400, f"Failed polling run {run_id}: {e}")
        status_val = run.get("status")
        dataset_id = dataset_id or run.get("defaultDatasetId")

    if status_val != "SUCCEEDED":
        return {
            "ok": False,
            "actor": actor,
            "domain": clean,
            "error": f"Actor finished with status {status_val}.",
            "run_id": run_id,
        }

    try:
        items = client.get_dataset_items(dataset_id) if dataset_id else []
    except Exception as e:
        raise HTTPException(400, f"Fetching dataset {dataset_id}: {e}")

    parsed = _merge_apify_items(items)
    return {
        "ok": True,
        "actor": actor,
        "domain": clean,
        "item_count": len(items),
        "parsed_ad_count": parsed.get(clean, {}).get("ad_count", 0),
        "sample_item_keys": sorted(list(items[0].keys()))[:25] if items else [],
        "sample_item": items[0] if items else None,
    }


@app.post("/api/meta/test")
def test_meta_ads():
    """Hit Meta's Ads Archive with a benign query to validate the token."""
    token = settings.get("meta_ads_access_token")
    if not token:
        raise HTTPException(400, "Meta Ads access token is not set.")
    import requests as _r
    try:
        r = _r.get(
            "https://graph.facebook.com/v19.0/ads_archive",
            params={
                "access_token": token,
                "search_terms": "acme",
                "ad_reached_countries": "['US']",
                "ad_active_status": "ACTIVE",
                "fields": "id",
                "limit": 1,
            },
            timeout=10,
        )
    except _r.RequestException as e:
        raise HTTPException(400, f"Network: {e}")
    if r.status_code != 200:
        try:
            err = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
        except Exception:
            err = f"HTTP {r.status_code}"
        raise HTTPException(400, err)
    return {"ok": True, "message": "Meta Ads Library API reachable."}


# --- ClickUp diagnostics -------------------------------------------------

@app.post("/api/clickup/test")
def test_clickup():
    key = settings.get("clickup_api_key")
    list_id = settings.get("clickup_list_id")
    if not key:
        raise HTTPException(400, "ClickUp API key is not set.")
    client = ClickUpClient(key)
    result = client.test_connection(list_id or None)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "ClickUp connection failed"))
    return result


@app.post("/api/clickup/refresh")
def refresh_clickup_cache():
    """Invalidate the ClickUp cache so the next run re-fetches fresh data."""
    client = _get_clickup_client()
    if client:
        client.invalidate_cache()
    return {"ok": True}
