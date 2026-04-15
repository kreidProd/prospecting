"""
Apify REST API wrapper.

Async run lifecycle:
1. `start_google_places_run(search, location, max)` → returns {id, defaultDatasetId, status}
2. `get_run(run_id)` polled every N seconds until status in terminal set
3. `get_dataset_items(dataset_id)` returns list of place dicts

The `compass/crawler-google-places` actor is the canonical Google Maps scraper on Apify.
Pricing is ~$5 per 1,000 results (as of 2026-Q1).
"""
import time
from typing import Optional

import requests


APIFY_API = "https://api.apify.com/v2"
GOOGLE_PLACES_ACTOR = "compass~crawler-google-places"

TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"}


class ApifyError(Exception):
    pass


class ApifyClient:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    # --- Diagnostics -----------------------------------------------------

    def test(self) -> dict:
        try:
            r = requests.get(f"{APIFY_API}/users/me", headers=self.headers, timeout=10)
        except requests.RequestException as e:
            return {"ok": False, "error": f"Network: {e}"}
        if r.status_code != 200:
            return {
                "ok": False,
                "error": f"Auth failed ({r.status_code}). Check your Apify token.",
            }
        d = (r.json() or {}).get("data", {}) or {}
        plan = d.get("plan") or {}
        return {
            "ok": True,
            "username": d.get("username") or d.get("email") or "unknown",
            "plan": plan.get("id") or "",
        }

    # --- Runs ------------------------------------------------------------

    def start_google_places_run(
        self,
        search: str,
        location: str,
        max_results: int = 500,
    ) -> dict:
        input_data = {
            "searchStringsArray": [search],
            "locationQuery": location,
            "maxCrawledPlacesPerSearch": max_results,
            "language": "en",
            "includeWebResults": False,
            "maxReviews": 0,
            "maxImages": 0,
        }
        url = f"{APIFY_API}/acts/{GOOGLE_PLACES_ACTOR}/runs"
        try:
            r = requests.post(url, headers=self.headers, json=input_data, timeout=30)
        except requests.RequestException as e:
            raise ApifyError(f"Network starting run: {e}") from e
        if r.status_code not in (200, 201):
            raise ApifyError(f"Apify start HTTP {r.status_code}: {r.text[:300]}")
        return (r.json() or {}).get("data", {}) or {}

    def get_run(self, run_id: str) -> dict:
        try:
            r = requests.get(
                f"{APIFY_API}/actor-runs/{run_id}",
                headers=self.headers,
                timeout=15,
            )
        except requests.RequestException as e:
            raise ApifyError(f"Network polling run: {e}") from e
        if r.status_code != 200:
            raise ApifyError(f"Apify get_run HTTP {r.status_code}")
        return (r.json() or {}).get("data", {}) or {}

    def get_dataset_items(self, dataset_id: str, limit: int = 10000) -> list:
        try:
            r = requests.get(
                f"{APIFY_API}/datasets/{dataset_id}/items",
                headers=self.headers,
                params={"clean": "true", "limit": limit, "format": "json"},
                timeout=120,
            )
        except requests.RequestException as e:
            raise ApifyError(f"Network fetching dataset: {e}") from e
        if r.status_code != 200:
            raise ApifyError(f"Apify dataset HTTP {r.status_code}")
        return r.json() or []

    def abort_run(self, run_id: str):
        try:
            requests.post(
                f"{APIFY_API}/actor-runs/{run_id}/abort",
                headers=self.headers,
                timeout=10,
            )
        except requests.RequestException:
            pass
