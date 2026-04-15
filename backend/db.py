import sqlite3
import json
from pathlib import Path


class DB:
    def __init__(self, path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                name TEXT,
                status TEXT,
                started_at REAL,
                finished_at REAL,
                processed INTEGER,
                total INTEGER,
                duplicates INTEGER,
                tier_counts TEXT,
                output_path TEXT
            )
        """)
        self.conn.commit()

    def save_run(self, run):
        s = run.get("summary") or {}
        self.conn.execute("""
            INSERT OR REPLACE INTO runs
            (id, name, status, started_at, finished_at, processed, total, duplicates, tier_counts, output_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run["id"],
            run.get("name", ""),
            run.get("status", ""),
            run.get("started_at"),
            run.get("finished_at"),
            run.get("processed", 0),
            run.get("total", 0),
            s.get("duplicates", 0),
            json.dumps(s.get("tier_distribution") or run.get("tier_counts") or {}),
            run.get("output_path", ""),
        ))
        self.conn.commit()

    def get_run(self, run_id):
        c = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = c.fetchone()
        return self._row(row, c.description) if row else None

    def list_runs(self):
        c = self.conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 100")
        return [self._row(r, c.description) for r in c.fetchall()]

    def _row(self, row, description):
        d = {col[0]: row[i] for i, col in enumerate(description)}
        if d.get("tier_counts"):
            try:
                d["tier_counts"] = json.loads(d["tier_counts"])
            except Exception:
                d["tier_counts"] = {}
        return d
