"""RB-PROSPECT-RecordsRefresh — populate Supabase `public_records`.

Orchestrates: fetch (download) → parse (records_loaders) → delete_by_source →
upsert. Idempotent per source: each refresh clears that source's rows then
reloads, so re-running is safe.

Two intake modes per source, so a gated download never dead-ends the pipeline:
  --fetch          attempt the documented auto-download
  --file <path>    load a manually-downloaded extract (browser → drop on box)

Sources implemented: FL_DBPR (roofing-specific, small — the pilot target) and
FL_SUNBIZ (broad SoS, large — quarterly full via SFTP, deferrable). CA/CO/OH/AZ
register the same way once their fetchers land.

Run on the VPS (network + SFTP live there). Needs SUPABASE_URL +
SUPABASE_SERVICE_ROLE_KEY in env (lead_pipe.env).
"""
from __future__ import annotations

import io
import os
import sys
import zipfile

import requests

from records_loaders import parse_dbpr, parse_sunbiz_cor
from supabase_client import delete_by_source, upsert_public_records

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# --------------------------------------------------------------------------- #
# Fetchers
# --------------------------------------------------------------------------- #

# DBPR construction extracts. The portal has moved repeatedly; we try known
# candidates and validate we actually got a ZIP (not the soft-404 HTML page).
DBPR_CANDIDATES = [
    "https://www2.myfloridalicense.com/sto/file_download/extracts/{f}",
    "https://www.myfloridalicense.com/sto/file_download/extracts/{f}",
    "https://www.myfloridalicense.com/download/{f}",
]
DBPR_FILES = ["con_cert.zip", "con_reg.zip"]


def _looks_like_zip(content: bytes) -> bool:
    return content[:2] == b"PK"


def _unzip_text(content: bytes) -> str:
    """Return concatenated text of all members in a zip (DBPR ships .txt/.csv)."""
    out = []
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for name in z.namelist():
            with z.open(name) as fh:
                out.append(fh.read().decode("latin-1", errors="replace"))
    return "\n".join(out)


def fetch_fl_dbpr() -> str:
    """Download + unzip the DBPR roofing-eligible construction extracts.

    Raises RuntimeError with what-was-tried if every candidate returns HTML
    (portal gated) — caller should fall back to --file.
    """
    chunks, tried = [], []
    for fname in DBPR_FILES:
        got = False
        for tmpl in DBPR_CANDIDATES:
            url = tmpl.format(f=fname)
            tried.append(url)
            try:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            except requests.RequestException:
                continue
            if r.status_code == 200 and _looks_like_zip(r.content):
                chunks.append(_unzip_text(r.content))
                got = True
                break
        if not got:
            raise RuntimeError(
                "DBPR auto-download gated (got HTML, not a zip). Tried:\n  "
                + "\n  ".join(tried)
                + "\nFall back to: download con_cert.zip + con_reg.zip from the "
                "DBPR portal in a browser, drop on the box, run with --file."
            )
    return "\n".join(chunks)


def fetch_fl_sunbiz_quarterly() -> str:
    """Pull the Sunbiz cordata quarterly full over SFTP (public creds).

    LARGE (multi-GB, millions of FL entities). Deferrable — DBPR covers roofers
    directly. Requires paramiko. Returns concatenated cor*.txt text.
    """
    try:
        import paramiko  # noqa
    except ImportError as e:
        raise RuntimeError("Sunbiz SFTP needs paramiko: pip install paramiko") from e
    host, user, pw = "sftp.floridados.gov", "Public", "PubAccess1845!"
    t = paramiko.Transport((host, 22))
    t.connect(username=user, password=pw)
    sftp = paramiko.SFTPClient.from_transport(t)
    try:
        sftp.chdir("doc/quarterly/cor")
        names = [n for n in sftp.listdir() if n.lower().endswith(".zip")]
        if not names:
            raise RuntimeError("no cordata zip found in doc/quarterly/cor")
        buf = io.BytesIO()
        sftp.getfo(names[0], buf)
        return _unzip_text(buf.getvalue())
    finally:
        sftp.close()
        t.close()


SOURCES = {
    "fl_dbpr": {"tag": "FL_DBPR", "fetch": fetch_fl_dbpr, "parse": parse_dbpr},
    "fl_sunbiz": {
        "tag": "FL_SUNBIZ",
        "fetch": fetch_fl_sunbiz_quarterly,
        "parse": lambda text: parse_sunbiz_cor(text.splitlines()),
    },
}


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def refresh(source_key: str, local_file: str | None = None, dry_run: bool = False) -> dict:
    cfg = SOURCES[source_key]
    if local_file:
        with open(local_file, "rb") as fh:
            raw = fh.read()
        text = _unzip_text(raw) if _looks_like_zip(raw) else raw.decode("latin-1", "replace")
    else:
        text = cfg["fetch"]()

    recs = cfg["parse"](text)
    rows = [r.to_row() for r in recs]
    result = {"source": cfg["tag"], "parsed": len(rows)}
    if dry_run:
        result["loaded"] = 0
        result["sample"] = [(r.owner_name, r.biz_name_raw) for r in recs[:5]]
        return result

    delete_by_source(cfg["tag"])
    result["loaded"] = upsert_public_records(rows)
    return result


def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Refresh public_records for a state source")
    ap.add_argument("source", choices=list(SOURCES))
    ap.add_argument("--file", help="local extract (zip/csv/txt) instead of auto-fetch")
    ap.add_argument("--dry-run", action="store_true", help="parse only; no DB writes")
    args = ap.parse_args(argv)
    try:
        res = refresh(args.source, local_file=args.file, dry_run=args.dry_run)
    except Exception as e:
        print(f"REFRESH FAILED [{args.source}]: {e}", file=sys.stderr)
        return 1
    print(f"[{res['source']}] parsed={res['parsed']} loaded={res['loaded']}")
    for owner, biz in res.get("sample", []):
        print(f"    {owner:24} <- {biz[:48]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
