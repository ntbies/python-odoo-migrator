# Copyright (c) 2024 ntbies OSS. MIT License.

import contextlib
import datetime
import sqlite3
from typing import Optional, Dict, Any, List


class ProgressTracker:
    """
    Simple SQLite-backed tracker to record per-record migration status.

    Schema fields:
    - model: Odoo model name (e.g., 'res.partner')
    - src_id: source database record id
    - dst_id: target database record id (nullable)
    - status: 'in_progress' | 'success' | 'updated' | 'skipped' | 'failed'
    - message: optional error/info message
    - attempt: number of attempts performed
    - started_at / finished_at: ISO timestamps
    Unique constraint on (model, src_id) to keep one row per source record.
    """

    def __init__(self, path: str = "migration.db"):
        self.conn = sqlite3.connect(path)
        self._init_schema()

    def _init_schema(self) -> None:
        with contextlib.closing(self.conn.cursor()) as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS migration_log (
                  id INTEGER PRIMARY KEY,
                  model TEXT NOT NULL,
                  src_id INTEGER NOT NULL,
                  dst_id INTEGER,
                  status TEXT NOT NULL,
                  message TEXT,
                  attempt INTEGER NOT NULL DEFAULT 1,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  UNIQUE(model, src_id)
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_migration_log_model_status ON migration_log(model, status)"
            )
        self.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.datetime.utcnow().isoformat()

    def mark_started(self, model: str, src_id: int) -> None:
        now = self._now()
        with contextlib.closing(self.conn.cursor()) as cur:
            cur.execute(
                (
                    "INSERT INTO migration_log(model, src_id, status, started_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(model, src_id) DO UPDATE SET status=excluded.status, started_at=excluded.started_at, attempt=migration_log.attempt+1"
                ),
                (model, int(src_id), "in_progress", now),
            )
        self.conn.commit()

    def mark_success(self, model: str, src_id: int, dst_id: Optional[int], *, updated: bool = False) -> None:
        now = self._now()
        status = "updated" if updated else "success"
        with contextlib.closing(self.conn.cursor()) as cur:
            cur.execute(
                "UPDATE migration_log SET dst_id=?, status=?, finished_at=? WHERE model=? AND src_id=?",
                (int(dst_id) if dst_id else None, status, now, model, int(src_id)),
            )
        self.conn.commit()

    def mark_failed(self, model: str, src_id: int, message: object) -> None:
        now = self._now()
        msg = str(message)
        if msg and len(msg) > 1000:
            msg = msg[:1000]
        with contextlib.closing(self.conn.cursor()) as cur:
            cur.execute(
                (
                    "INSERT INTO migration_log(model, src_id, status, message, started_at, finished_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(model, src_id) DO UPDATE SET status=excluded.status, message=excluded.message, finished_at=excluded.finished_at"
                ),
                (model, int(src_id), "failed", msg, now, now),
            )
        self.conn.commit()

    def mark_skipped(self, model: str, src_id: int, message: Optional[str] = None) -> None:
        now = self._now()
        with contextlib.closing(self.conn.cursor()) as cur:
            cur.execute(
                (
                    "INSERT INTO migration_log(model, src_id, status, message, started_at, finished_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(model, src_id) DO UPDATE SET status=excluded.status, message=excluded.message, finished_at=excluded.finished_at"
                ),
                (model, int(src_id), "skipped", message or "dry-run", now, now),
            )
        self.conn.commit()

    def already_done(self, model: str, src_id: int) -> bool:
        with contextlib.closing(self.conn.cursor()) as cur:
            cur.execute("SELECT status FROM migration_log WHERE model=? AND src_id=?", (model, int(src_id)))
            row = cur.fetchone()
        return bool(row and row[0] in ("success", "updated", "skipped"))

    def get_mapped_id(self, model: str, src_id: int) -> Optional[int]:
        """Return mapped dst_id for given (model, src_id) if known.

        We consider a mapping valid if a row exists with a non-null dst_id.
        Prefer rows that were successfully migrated, but we don't strictly
        require status = success/updated because mapping may be set earlier
        in the pipeline.
        """
        with contextlib.closing(self.conn.cursor()) as cur:
            cur.execute(
                "SELECT dst_id FROM migration_log WHERE model=? AND src_id=? AND dst_id IS NOT NULL",
                (model, int(src_id)),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def set_mapping(self, model: str, src_id: int, dst_id: int) -> None:
        """Upsert mapping into the migration_log table without changing status.

        If a row exists, update dst_id; otherwise create a placeholder row.
        """
        with contextlib.closing(self.conn.cursor()) as cur:
            cur.execute(
                (
                    "INSERT INTO migration_log(model, src_id, dst_id, status, started_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(model, src_id) DO UPDATE SET dst_id=excluded.dst_id"
                ),
                (model, int(src_id), int(dst_id), "in_progress", self._now()),
            )
        self.conn.commit()

    def summary(self, models: Optional[List[str]] = None) -> Dict[str, Any]:
        """Return aggregated counts per model and overall.

        Structure:
        {
          "overall": {status_counts..., "total": N, "done": M},
          "per_model": { "model": {status_counts..., "total": n, "done": m}, ... }
        }
        """
        params: List[Any] = []
        where = ""
        if models:
            placeholders = ",".join(["?"] * len(models))
            where = f"WHERE model IN ({placeholders})"
            params = list(models)
        per_model: Dict[str, Dict[str, int]] = {}
        with contextlib.closing(self.conn.cursor()) as cur:
            cur.execute(
                f"SELECT model, status, COUNT(*) FROM migration_log {where} GROUP BY model, status",
                params,
            )
            for model, status, count in cur.fetchall():
                m: Dict[str, int] = per_model.setdefault(model, {})
                m[status] = int(count)
        def finalize(stats: Dict[str, int]) -> Dict[str, int]:
            total = sum(stats.values())
            done = stats.get("success", 0) + stats.get("updated", 0) + stats.get("skipped", 0)
            out = {"success": stats.get("success", 0),
                   "updated": stats.get("updated", 0),
                   "skipped": stats.get("skipped", 0),
                   "failed": stats.get("failed", 0),
                   "in_progress": stats.get("in_progress", 0),
                   "total": total,
                   "done": done}
            return out
        per_model_final: Dict[str, Dict[str, int]] = {m: finalize(stats) for m, stats in per_model.items()}
        overall_acc: Dict[str, int] = {}
        for stats in per_model.values():
            for k, v in stats.items():
                overall_acc[k] = overall_acc.get(k, 0) + int(v)
        overall = finalize(overall_acc)
        return {"overall": overall, "per_model": per_model_final}


def format_progress_report_table(report: Dict[str, Any]) -> str:
    """Render the summary() dict into a simple table string."""
    headers = [
        "Model",
        "Success",
        "Updated",
        "Skipped",
        "Failed",
        "InProgress",
        "Done",
        "Total",
        "Done%",
    ]
    rows: List[List[str]] = []
    per_model: Dict[str, Dict[str, int]] = report.get("per_model", {}) or {}
    for model in sorted(per_model.keys()):
        s = per_model[model]
        total = s.get("total", 0) or 0
        done = s.get("done", 0) or 0
        pct = (100.0 * done / total) if total else 0.0
        rows.append([
            model,
            str(s.get("success", 0)),
            str(s.get("updated", 0)),
            str(s.get("skipped", 0)),
            str(s.get("failed", 0)),
            str(s.get("in_progress", 0)),
            str(done),
            str(total),
            f"{pct:5.1f}%",
        ])
    o = report.get("overall", {}) or {}
    ototal = o.get("total", 0) or 0
    odone = o.get("done", 0) or 0
    opct = (100.0 * odone / ototal) if ototal else 0.0
    rows.append([
        "TOTAL",
        str(o.get("success", 0)),
        str(o.get("updated", 0)),
        str(o.get("skipped", 0)),
        str(o.get("failed", 0)),
        str(o.get("in_progress", 0)),
        str(odone),
        str(ototal),
        f"{opct:5.1f}%",
    ])

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if len(cell) > widths[i]:
                widths[i] = len(cell)
    def fmt_row(cols: List[str]) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    sep = "-+-".join("-" * w for w in widths)
    out_lines = ["\n", fmt_row(headers), sep]
    for row in rows:
        out_lines.append(fmt_row(row))
    return "\n".join(out_lines)
