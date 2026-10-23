"""Очередь сессий: порядок, прогресс и восстановление после перезапуска."""

import sqlite3
import time

from medintake import jobs
from medintake.jobs import BatchWorker, JobStore
from tests.xml_factory import build_xml, write_xml


def test_worker_survives_database_error(settings, monkeypatch):
    settings.ensure_dirs()
    monkeypatch.setattr(jobs, "_FAILURE_BACKOFF_SECONDS", 0.01)
    store = JobStore(settings.database_path)
    original = store.claim_next
    calls = {"count": 0}

    def flaky_claim():
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return original()

    monkeypatch.setattr(store, "claim_next", flaky_claim)
    worker = BatchWorker(store, settings)
    worker.start()
    try:
        deadline = time.monotonic() + 2
        while calls["count"] < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls["count"] >= 3
        assert worker.is_alive
    finally:
        worker.stop()
        store.close()


def test_claim_order_and_restart_recovery(settings, tmp_path):
    settings.ensure_dirs()
    store = JobStore(settings.database_path)
    first_dir = tmp_path / "one"
    second_dir = tmp_path / "two"
    first_dir.mkdir()
    second_dir.mkdir()
    write_xml(first_dir, "a.xml")
    write_xml(second_dir, "b.xml", content=build_xml(document_id="DOC-2"))

    store.enqueue(job_id="a" * 32, source="upload", total=1, input_dir=first_dir)
    store.enqueue(job_id="b" * 32, source="upload", total=1, input_dir=second_dir)

    claimed = store.claim_next()
    assert claimed is not None
    assert claimed.id == "a" * 32
    assert claimed.status == "running"

    store.recover_interrupted()
    recovered = store.get("a" * 32)
    assert recovered is not None
    assert recovered.status == "failed"
    assert "перезапуском" in (recovered.error or "")

    nxt = store.claim_next()
    assert nxt is not None
    assert nxt.id == "b" * 32
    store.complete(nxt.id, tmp_path / "report.json", processed=1)
    done = store.get(nxt.id)
    assert done is not None
    assert done.status == "completed"
    assert done.processed == 1
    assert store.list_recent(10)[0].id == nxt.id
    store.close()
