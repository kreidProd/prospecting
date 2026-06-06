"""
Direct Close REST client for syncing re-tier results back into Close.

The MCP wrapper exposed by Close only allows reading custom fields and
updating a lead's name/url/status — not writing custom field values. This
client talks to Close's REST API directly to set:

  - lead_custom.Tier   (1A / 1B / 2 / 3A / 3B / PARK / RECHECK_ADS / REVIEW_TRACKING)
  - lead_custom.Reviews
  - lead_custom.Rating

Authentication uses HTTP Basic with an API key in the username slot, no
password — that's Close's documented scheme for personal API keys.
"""
from __future__ import annotations

import re
import threading
from typing import Optional

import requests


CLOSE_BASE = "https://api.close.com/api/v1"

# Field NAME -> attribute on the client. Looked up dynamically on first
# call so we don't hardcode IDs that vary between organizations.
CUSTOM_FIELD_NAMES = {
    "tier_field_id": "Tier",
    "reviews_field_id": "Reviews",
    "rating_field_id": "Rating",
}


def _domain_only(url_or_domain: str) -> str:
    if not url_or_domain:
        return ""
    s = url_or_domain.strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+\-.]*://", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].split(":", 1)[0]
    s = re.sub(r"^www\.", "", s).rstrip(".")
    return s


class CloseClient:
    def __init__(self, api_key: str, timeout: float = 15.0):
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (api_key, "")
        self.session.headers.update({"Accept": "application/json"})
        self._field_ids: dict[str, str] = {}
        self._fields_loaded = False
        self._fields_lock = threading.Lock()
        self._domain_cache: dict[str, Optional[str]] = {}
        self._cache_lock = threading.Lock()

    # --- Schema lookup ---------------------------------------------------

    def _ensure_fields(self) -> None:
        if self._fields_loaded:
            return
        with self._fields_lock:
            if self._fields_loaded:
                return
            try:
                r = self.session.get(
                    f"{CLOSE_BASE}/custom_field/lead/",
                    timeout=self.timeout,
                )
                r.raise_for_status()
            except requests.RequestException:
                self._fields_loaded = True
                return
            data = r.json() or {}
            by_name = {f.get("name"): f.get("id") for f in (data.get("data") or [])}
            for attr, name in CUSTOM_FIELD_NAMES.items():
                fid = by_name.get(name)
                if fid:
                    self._field_ids[attr] = fid
            self._fields_loaded = True

    # --- Lead discovery --------------------------------------------------

    def find_lead_id_by_domain(self, domain: str) -> Optional[str]:
        """Return the Close lead ID matching this domain, or None."""
        d = _domain_only(domain)
        if not d:
            return None
        with self._cache_lock:
            if d in self._domain_cache:
                return self._domain_cache[d]
        try:
            r = self.session.get(
                f"{CLOSE_BASE}/lead/",
                params={"query": f"url:{d}", "_fields": "id,url,display_name"},
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.RequestException:
            with self._cache_lock:
                self._domain_cache[d] = None
            return None
        leads = (r.json() or {}).get("data") or []
        # Choose the lead whose URL most closely matches the domain.
        best = None
        for lead in leads:
            url = (lead.get("url") or "").lower()
            if d in url:
                best = lead.get("id")
                break
        with self._cache_lock:
            self._domain_cache[d] = best
        return best

    # --- Mutations -------------------------------------------------------

    def update_lead_fields(
        self,
        lead_id: str,
        tier: Optional[str] = None,
        reviews: Optional[int] = None,
        rating: Optional[float] = None,
    ) -> bool:
        """Update Tier / Reviews / Rating custom fields on a lead."""
        self._ensure_fields()
        payload: dict = {}
        if tier and self._field_ids.get("tier_field_id"):
            payload[f"custom.{self._field_ids['tier_field_id']}"] = tier
        if reviews is not None and self._field_ids.get("reviews_field_id"):
            payload[f"custom.{self._field_ids['reviews_field_id']}"] = reviews
        if rating is not None and self._field_ids.get("rating_field_id"):
            payload[f"custom.{self._field_ids['rating_field_id']}"] = rating
        if not payload:
            return False
        try:
            r = self.session.put(
                f"{CLOSE_BASE}/lead/{lead_id}/",
                json=payload,
                timeout=self.timeout,
            )
            return r.ok
        except requests.RequestException:
            return False

    # --- Bulk sync -------------------------------------------------------

    def sync_results(self, rows: list[dict]) -> dict:
        """Push tier/reviews/rating for every row that resolves to a Close
        lead. Returns counts: {matched, updated, skipped, errors}."""
        self._ensure_fields()
        matched = updated = skipped = errors = 0

        for row in rows:
            domain = row.get("website") or ""
            tier = (row.get("tier") or "").strip()
            if not domain or not tier or tier == "SKIP":
                skipped += 1
                continue
            lead_id = self.find_lead_id_by_domain(domain)
            if not lead_id:
                skipped += 1
                continue
            matched += 1

            try:
                reviews = int(row.get("reviews") or 0) or None
            except (ValueError, TypeError):
                reviews = None
            try:
                rating = float(row.get("rating") or 0) or None
            except (ValueError, TypeError):
                rating = None

            ok = self.update_lead_fields(
                lead_id, tier=tier, reviews=reviews, rating=rating,
            )
            if ok:
                updated += 1
            else:
                errors += 1

        return {"matched": matched, "updated": updated, "skipped": skipped, "errors": errors}
