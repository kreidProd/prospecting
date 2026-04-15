"""
Thin ClickUp API wrapper.

Used by the pipeline to pull the current prospect list for dedup without
requiring a manual CSV export. Cached for 10 minutes so back-to-back runs
don't hammer the API.
"""
import re
import time
from typing import Optional

import requests


CLICKUP_API = "https://api.clickup.com/api/v2"
CACHE_TTL_SECONDS = 600


class ClickUpError(Exception):
    pass


class ClickUpClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }
        self._cache: dict = {}  # list_id -> (timestamp, normalized_rows)

    # --- Auth / diagnostics ----------------------------------------------

    def test_connection(self, list_id: Optional[str] = None) -> dict:
        """Returns diagnostic info about auth + optional list access."""
        try:
            r = requests.get(f"{CLICKUP_API}/user", headers=self.headers, timeout=10)
        except requests.RequestException as e:
            return {"ok": False, "error": f"Network: {e}"}
        if r.status_code != 200:
            return {
                "ok": False,
                "error": f"Auth failed ({r.status_code}). Check your API key.",
            }
        user = (r.json() or {}).get("user", {}) or {}
        out = {
            "ok": True,
            "user": user.get("username") or user.get("email") or "unknown",
        }
        if list_id:
            try:
                r2 = requests.get(
                    f"{CLICKUP_API}/list/{list_id}",
                    headers=self.headers,
                    timeout=10,
                )
            except requests.RequestException as e:
                out["list_error"] = f"Network while fetching list: {e}"
                return out
            if r2.status_code == 200:
                li = r2.json() or {}
                out["list_name"] = li.get("name", f"list {list_id}")
                out["task_count"] = li.get("task_count", 0)
            else:
                out["list_error"] = (
                    f"List {list_id} not accessible ({r2.status_code})."
                )
        return out

    # --- Task listing with pagination ------------------------------------

    def list_tasks(self, list_id: str, use_cache: bool = True) -> list:
        now = time.time()
        if use_cache and list_id in self._cache:
            ts, data = self._cache[list_id]
            if now - ts < CACHE_TTL_SECONDS:
                return data

        all_tasks = []
        page = 0
        while True:
            try:
                r = requests.get(
                    f"{CLICKUP_API}/list/{list_id}/task",
                    headers=self.headers,
                    params={
                        "page": page,
                        "include_closed": "true",
                        "subtasks": "false",
                    },
                    timeout=20,
                )
            except requests.RequestException as e:
                raise ClickUpError(f"Network fetching tasks: {e}") from e
            if r.status_code != 200:
                raise ClickUpError(
                    f"ClickUp API {r.status_code}: {r.text[:200]}"
                )
            body = r.json() or {}
            batch = body.get("tasks", []) or []
            if not batch:
                break
            all_tasks.extend(batch)
            # ClickUp returns 100 tasks per page; stop when we get less
            if len(batch) < 100 or body.get("last_page") is True:
                break
            page += 1
            if page > 50:  # 5000-task safety cap
                break

        rows = [self._normalize(t) for t in all_tasks]
        self._cache[list_id] = (now, rows)
        return rows

    def invalidate_cache(self, list_id: Optional[str] = None):
        if list_id:
            self._cache.pop(list_id, None)
        else:
            self._cache.clear()

    # --- Normalization ----------------------------------------------------

    _PHONE_RE = re.compile(
        r"(\+?1?[\s.\-]?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})"
    )
    _URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
    _EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

    def _normalize(self, task: dict) -> dict:
        """Extract phone, website, email from custom fields + description fallback."""
        name = (task.get("name") or "").strip()
        desc = (task.get("description") or task.get("text_content") or "") or ""

        phone = ""
        website = ""
        email = ""

        for cf in task.get("custom_fields") or []:
            cname = (cf.get("name") or "").lower()
            value = cf.get("value")
            if value in (None, ""):
                continue
            sval = str(value) if not isinstance(value, dict) else (value.get("value") or "")
            if not sval:
                continue
            if not phone and "phone" in cname:
                phone = sval
            elif not website and any(k in cname for k in ("website", "url", "domain", "site")):
                website = sval
            elif not email and "email" in cname:
                email = sval

        if not phone:
            m = self._PHONE_RE.search(desc + " " + name)
            if m:
                phone = m.group(1)
        if not website:
            m = self._URL_RE.search(desc)
            if m:
                website = m.group(0)
        if not email:
            m = self._EMAIL_RE.search(desc)
            if m:
                email = m.group(0)

        return {
            "business_name": name,
            "phone": phone,
            "website": website,
            "email": email,
        }
