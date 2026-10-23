"""Очередь сессий на SQLite и один фоновый обработчик.

Этого достаточно для внутренней установки без отдельного брокера:
состояние переживает перезапуск, прогресс виден через API.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from medintake.batch import utc_now, validate_batch
from medintake.config import Settings
from medintake.errors import AppError
from medintake.intake import load_manifest
from medintake.reports import write_csv_report, write_json_report

logger = logging.getLogger("medintake.jobs")

_IDLE_POLL_SECONDS = 0.1
_FAILURE_BACKOFF_SECONDS = 2.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    total INTEGER NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    report_path TEXT,
    input_dir TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs (status, created_at);
"""


@dataclass(frozen=True)
class Job:
    id: str
    status: str
    source: str
    total: int
    processed: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    report_path: str | None
    input_dir: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "source": self.source,
            "total": self.total,
            "processed": self.processed,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class JobStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            database_path,
            check_same_thread=False,
            timeout=5,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def recover_interrupted(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    finished_at = ?,
                    error = 'Обработка прервана перезапуском сервиса.'
                WHERE status = 'running'
                """,
                (utc_now(),),
            )
            self._conn.commit()

    def enqueue(self, *, job_id: str, source: str, total: int, input_dir: Path) -> Job:
        created_at = utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (id, status, source, total, processed, created_at, input_dir)
                VALUES (?, 'queued', ?, ?, 0, ?, ?)
                """,
                (job_id, source, total, created_at, str(input_dir)),
            )
            self._conn.commit()
            job = self._get(job_id)
        if job is None:
            raise RuntimeError(f"Сессия {job_id} не сохранилась в очереди.")
        logger.info("Сессия %s поставлена в очередь, файлов: %s", job_id, total)
        return job

    def claim_next(self) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at, rowid
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            job_id = str(row["id"])
            updated = self._conn.execute(
                """
                UPDATE jobs
                SET status = 'running', started_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (utc_now(), job_id),
            )
            self._conn.commit()
            if updated.rowcount != 1:
                return None
            return self._get(job_id)

    def update_progress(self, job_id: str, processed: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET processed = ? WHERE id = ? AND status = 'running'",
                (processed, job_id),
            )
            self._conn.commit()

    def complete(self, job_id: str, report_path: Path, processed: int) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                SET status = 'completed',
                    processed = ?,
                    finished_at = ?,
                    report_path = ?,
                    error = NULL
                WHERE id = ?
                """,
                (processed, utc_now(), str(report_path), job_id),
            )
            self._conn.commit()

    def fail(self, job_id: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', finished_at = ?, error = ?
                WHERE id = ?
                """,
                (utc_now(), message, job_id),
            )
            self._conn.commit()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._get(job_id)

    def list_recent(self, limit: int) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_job(row) for row in rows]

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return False
        return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _get(self, job_id: str) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return _job(row)


class BatchWorker:
    def __init__(self, store: JobStore, settings: Settings) -> None:
        self._store = store
        self._settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._store.recover_interrupted()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="medintake-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("Обработчик не завершился за %s с, сессия будет прервана", timeout)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._store.claim_next()
                if job is None:
                    self._stop.wait(_IDLE_POLL_SECONDS)
                    continue
                self._execute(job)
            except Exception:
                logger.exception(
                    "Сбой цикла очереди, повтор через %s с", _FAILURE_BACKOFF_SECONDS
                )
                self._stop.wait(_FAILURE_BACKOFF_SECONDS)

    def _execute(self, job: Job) -> None:
        try:
            self._process(job)
        finally:
            if not self._settings.keep_input:
                shutil.rmtree(job.input_dir, ignore_errors=True)

    def _process(self, job: Job) -> None:
        try:
            items = load_manifest(Path(job.input_dir))
            report = validate_batch(
                items,
                self._settings,
                batch_id=job.id,
                on_progress=lambda done, _total: self._store.update_progress(job.id, done),
            )
            report_path = Path(job.input_dir).parent / "report.json"
            write_json_report(report, report_path)
            write_csv_report(report, report_path.with_suffix(".csv"))
            self._store.complete(job.id, report_path, report.total)
            logger.info(
                "Сессия %s завершена: без ошибок %s из %s",
                job.id,
                report.valid_count,
                report.total,
            )
        except (OSError, ValueError, AppError) as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            logger.error("Сессия %s отклонена: %s", job.id, message)
            self._store.fail(job.id, message)
        except Exception:
            logger.exception("Сессия %s завершилась сбоем", job.id)
            self._store.fail(
                job.id,
                "Не удалось обработать пакет. Подробности в журнале сервиса.",
            )


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=str(row["id"]),
        status=str(row["status"]),
        source=str(row["source"]),
        total=int(row["total"]),
        processed=int(row["processed"]),
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error=row["error"],
        report_path=row["report_path"],
        input_dir=str(row["input_dir"]),
    )
