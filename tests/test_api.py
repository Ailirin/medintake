"""REST: приём пакета, очередь, отчёт, токен, граница каталога inbox."""

import time
from datetime import date

from fastapi.testclient import TestClient

from medintake.api import create_app
from tests.xml_factory import build_xml, write_xml


def _wait(client: TestClient, batch_id: str) -> dict:
    last: dict = {}
    for _ in range(100):
        response = client.get(f"/api/v1/batches/{batch_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"completed", "failed"}:
            return last
        time.sleep(0.05)
    raise AssertionError(last)


def test_upload_batch_produces_json_and_csv(settings):
    settings.ensure_dirs()
    today = date.today().isoformat()
    first = build_xml(document_id="DOC-API-1", document_date=today)
    second = build_xml(document_id="DOC-API-1", document_date=today, last_name="Петров")
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        checks = client.get("/api/v1/checks")
        assert checks.status_code == 200
        assert any(item["code"] == "INVALID_SNILS_CHECKSUM" for item in checks.json()["checks"])

        created = client.post(
            "/api/v1/batches",
            files=[
                ("files", ("one.xml", first.encode("utf-8"), "application/xml")),
                ("files", ("two.xml", second.encode("utf-8"), "application/xml")),
            ],
        )
        assert created.status_code == 202
        body = created.json()
        assert body["status"] == "queued"
        assert body["total"] == 2

        finished = _wait(client, body["id"])
        assert finished["status"] == "completed"
        assert finished["processed"] == 2

        report = client.get(f"/api/v1/batches/{body['id']}/report")
        assert report.status_code == 200
        payload = report.json()
        assert payload["summary"]["invalid"] == 2
        assert payload["results"][0]["file"] == "one.xml"
        assert any(
            issue["code"] == "DUPLICATE_DOCUMENT_ID"
            for issue in payload["results"][0]["issues"]
        )

        csv_report = client.get(f"/api/v1/batches/{body['id']}/report", params={"format": "csv"})
        assert csv_report.status_code == 200
        assert "DUPLICATE_DOCUMENT_ID" in csv_report.text

        missing = client.get("/api/v1/batches/" + "c" * 32)
        assert missing.status_code == 404
        assert missing.json()["error"] == "Пакет не найден."


def test_directory_intake_stays_inside_inbox(settings, tmp_path):
    settings.ensure_dirs()
    incoming = settings.inbox / "2026-10-22"
    incoming.mkdir(parents=True)
    write_xml(incoming, "case.xml", content=build_xml(document_date=date.today().isoformat()))
    outside = tmp_path / "secret.xml"
    outside.write_text(build_xml(), encoding="utf-8")

    app = create_app(settings)
    with TestClient(app) as client:
        escaped = client.post("/api/v1/batches/directory", json={"directory": "../secret"})
        assert escaped.status_code == 400

        absolute = client.post(
            "/api/v1/batches/directory",
            json={"directory": str(outside.parent)},
        )
        assert absolute.status_code == 400

        accepted = client.post(
            "/api/v1/batches/directory",
            json={"directory": "2026-10-22"},
        )
        assert accepted.status_code == 202
        finished = _wait(client, accepted.json()["id"])
        assert finished["status"] == "completed"
        assert finished["error"] is None

        not_xml = client.post(
            "/api/v1/batches",
            files=[("files", ("notes.txt", b"text", "text/plain"))],
        )
        assert not_xml.status_code == 400


def test_token_protects_api_and_leaves_health_open(settings):
    secured = settings.model_copy(update={"api_token": "secret-token"})
    secured.ensure_dirs()
    app = create_app(secured)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/checks").status_code == 401
        denied = client.get(
            "/api/v1/checks",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert denied.status_code == 401
        allowed = client.get(
            "/api/v1/checks",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert allowed.status_code == 200


def test_upload_accepts_more_than_starlette_default_of_1000_files(settings):
    settings.ensure_dirs()
    payload = build_xml().encode("utf-8")
    app = create_app(settings)
    with TestClient(app) as client:
        client.app.state.worker.stop()
        created = client.post(
            "/api/v1/batches",
            files=[("files", (f"d{index}.xml", payload, "application/xml")) for index in range(1001)],
        )
        assert created.status_code == 202, created.text
        assert created.json()["total"] == 1001


def test_upload_over_session_limit_is_rejected_in_russian(settings):
    limited = settings.model_copy(update={"max_files": 2})
    limited.ensure_dirs()
    payload = build_xml().encode("utf-8")
    app = create_app(limited)
    with TestClient(app) as client:
        rejected = client.post(
            "/api/v1/batches",
            files=[("files", (f"d{index}.xml", payload, "application/xml")) for index in range(3)],
        )
        assert rejected.status_code == 400
        assert "не больше 2 файлов" in rejected.json()["error"]
        assert list(limited.batches_dir.iterdir()) == []


def test_input_files_are_removed_after_processing(settings):
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/batches",
            files=[("files", ("one.xml", build_xml().encode("utf-8"), "application/xml"))],
        )
        batch_id = created.json()["id"]
        assert _wait(client, batch_id)["status"] == "completed"
        batch_dir = settings.batches_dir / batch_id
        assert not (batch_dir / "input").exists()
        assert (batch_dir / "report.json").is_file()


def test_health_reports_stopped_worker(settings):
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").json()["worker"] == "running"
        client.app.state.worker.stop()
        degraded = client.get("/health")
        assert degraded.status_code == 503
        assert degraded.json()["worker"] == "stopped"


def test_running_batch_has_no_report_yet(settings):
    """Пока обработчик не забрал сессию, отчёт не отдаётся."""
    settings.ensure_dirs()
    app = create_app(settings)
    with TestClient(app) as client:
        # Останавливаем обработчик, чтобы сессия осталась в очереди.
        client.app.state.worker.stop()
        created = client.post(
            "/api/v1/batches",
            files=[("files", ("one.xml", build_xml().encode("utf-8"), "application/xml"))],
        )
        assert created.status_code == 202
        report = client.get(f"/api/v1/batches/{created.json()['id']}/report")
        assert report.status_code == 409
        assert report.json()["status"] == "queued"
