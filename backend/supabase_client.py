"""Thin Supabase REST client for the prospect pipeline.

Used by records_loaders (bulk upsert into public_records) and the Enrich
cascade (per-lead match queries). Auth = service-role key (bypasses RLS),
read from env so no secret lives in code:

    SUPABASE_URL=https://<ref>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=<service-role JWT>

On the VPS these come from lead_pipe.env; n8n holds its own copy in its
credential store. Locally, export them to run --upsert.
"""
from __future__ import annotations

import os
from typing import Any

import requests

_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
_TIMEOUT = 30


def _headers(extra: dict | None = None) -> dict:
    if not _URL or not _KEY:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — export them or "
            "populate lead_pipe.env before calling Supabase."
        )
    h = {
        "apikey": _KEY,
        "Authorization": f"Bearer {_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def upsert_public_records(rows: list[dict], chunk: int = 500) -> int:
    """Insert public_records rows in chunks. Returns count attempted.

    public_records has no natural unique key (it's an append/refresh log), so
    callers typically truncate-by-source before a full reload; we plain-insert.
    """
    if not rows:
        return 0
    url = f"{_URL}/rest/v1/public_records"
    total = 0
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        r = requests.post(url, headers=_headers({"Prefer": "return=minimal"}),
                          json=batch, timeout=_TIMEOUT)
        r.raise_for_status()
        total += len(batch)
    return total


def delete_by_source(source: str) -> None:
    """Clear a source before a full reload (idempotent weekly refresh)."""
    url = f"{_URL}/rest/v1/public_records?source=eq.{source}"
    r = requests.delete(url, headers=_headers({"Prefer": "return=minimal"}), timeout=_TIMEOUT)
    r.raise_for_status()


def fetch_exact(state: str, biz_name_norm: str) -> list[dict]:
    """Exact match on state + biz_name_norm (cascade 7b step 1)."""
    url = (f"{_URL}/rest/v1/public_records"
           f"?state=eq.{state}&biz_name_norm=eq.{requests.utils.quote(biz_name_norm)}"
           f"&select=*")
    r = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_state(state: str, _ignored: str = "") -> list[dict]:
    """All records in a state for the fuzzy pass (cascade 7b step 2).

    For large states this should be narrowed (e.g. by city or a name prefix);
    kept simple here — the Enrich flow batches per-lead and can pass city.
    """
    url = f"{_URL}/rest/v1/public_records?state=eq.{state}&select=*"
    r = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()
