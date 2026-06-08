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

from records_loaders import (
    ROOFING_NAME_RE,
    iter_sunbiz_records,
    parse_dbpr,
    parse_sunbiz_cor,
)
from supabase_client import delete_by_source, upsert_public_records

SUNBIZ_HOST = "sftp.floridados.gov"
SUNBIZ_CRED = ("Public", "PubAccess1845!")
SUNBIZ_QUARTERLY = "doc/quarterly/cor/cordata.zip"

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


def _sftp_download(remote: str, local: str) -> None:
    """Stream a file off the Sunbiz public SFTP to local disk."""
    try:
        import paramiko
    except ImportError as e:
        raise RuntimeError("Sunbiz SFTP needs paramiko (add to requirements.txt)") from e
    t = paramiko.Transport((SUNBIZ_HOST, 22))
    t.connect(username=SUNBIZ_CRED[0], password=SUNBIZ_CRED[1])
    try:
        # prefetch=False is REQUIRED: the shared public account refuses paramiko's
        # default parallel prefetch ("insufficient resources") on large files.
        # Sequential is slower (~0.3 MB/s) but completes the 1.7GB quarterly.
        paramiko.SFTPClient.from_transport(t).get(remote, local, prefetch=False)
    finally:
        t.close()


def refresh_fl_sunbiz(dry_run: bool = False, batch: int = 1000,
                      zip_path: str = "/tmp/cordata.zip",
                      name_filter=ROOFING_NAME_RE, sample_only: int = 0) -> dict:
    """Stream the 1.7GB cordata quarterly → roofing filter → batched upsert.

    Memory-safe: downloads to disk, streams zip members line-by-line, never
    materializes the full file. dry_run counts + samples without DB writes.
    sample_only>0 stops after N matches (fast offset/quality check).
    """
    if not os.path.exists(zip_path) or os.path.getsize(zip_path) < 1_000_000:
        _sftp_download(SUNBIZ_QUARTERLY, zip_path)

    parsed = loaded = 0
    buf, samples = [], []
    if not dry_run:
        delete_by_source("FL_SUNBIZ")
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            with z.open(member) as fh:
                stream = io.TextIOWrapper(fh, encoding="latin-1", errors="replace", newline="")
                for rec in iter_sunbiz_records((ln.rstrip("\r\n") for ln in stream), name_filter):
                    parsed += 1
                    if len(samples) < 8:
                        samples.append((rec.owner_name, rec.owner_title, rec.biz_name_raw))
                    if not dry_run:
                        buf.append(rec.to_row())
                        if len(buf) >= batch:
                            loaded += upsert_public_records(buf)
                            buf = []
                    if sample_only and parsed >= sample_only:
                        return {"source": "FL_SUNBIZ", "parsed": parsed,
                                "loaded": loaded, "sample": samples, "partial": True}
    if buf:
        loaded += upsert_public_records(buf)
    return {"source": "FL_SUNBIZ", "parsed": parsed, "loaded": loaded, "sample": samples}


SOURCES = {
    "fl_dbpr": {"tag": "FL_DBPR", "fetch": fetch_fl_dbpr, "parse": parse_dbpr},
    # fl_sunbiz is handled by refresh_fl_sunbiz (streaming), not the generic path.
}


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def refresh(source_key: str, local_file: str | None = None, dry_run: bool = False,
            sample_only: int = 0) -> dict:
    if source_key == "fl_sunbiz":
        return refresh_fl_sunbiz(dry_run=dry_run, sample_only=sample_only)

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
    ap.add_argument("source", choices=list(SOURCES) + ["fl_sunbiz"])
    ap.add_argument("--file", help="local extract (zip/csv/txt) instead of auto-fetch")
    ap.add_argument("--dry-run", action="store_true", help="parse only; no DB writes")
    ap.add_argument("--sample-only", type=int, default=0,
                    help="(fl_sunbiz) stop after N matches — fast offset/quality check")
    args = ap.parse_args(argv)
    try:
        res = refresh(args.source, local_file=args.file, dry_run=args.dry_run,
                      sample_only=args.sample_only)
    except Exception as e:
        print(f"REFRESH FAILED [{args.source}]: {e}", file=sys.stderr)
        return 1
    tag = " (partial)" if res.get("partial") else ""
    print(f"[{res['source']}] parsed={res['parsed']} loaded={res['loaded']}{tag}")
    for s in res.get("sample", []):
        if len(s) == 3:
            print(f"    {s[0]:22} ({s[1] or '-':>6})  <- {s[2][:46]}")
        else:
            print(f"    {s[0]:22} <- {s[1][:46]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
