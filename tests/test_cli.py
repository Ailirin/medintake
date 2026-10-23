"""Коды выхода CLI: 0 — пакет принят, 1 — есть ошибки документов, 2 — пакет не принят."""

import json
from datetime import date
from pathlib import Path

from medintake.cli import main
from tests.xml_factory import build_xml, write_xml


def test_cli_writes_json_and_returns_validation_status(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MEDINTAKE_MAX_FILES", raising=False)
    today = date.today().isoformat()
    folder = tmp_path / "pkg"
    folder.mkdir()
    write_xml(folder, "ok.xml", content=build_xml(document_date=today))
    output = tmp_path / "out" / "report.json"

    assert main([str(folder), "--format", "json", "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["valid"] == 1
    assert "Отчёт сохранён" in capsys.readouterr().out

    bad = write_xml(folder, "bad.xml", content=build_xml(document_date=today, gender="X"))
    assert main([str(bad)]) == 1
    assert "XSD_VALIDATION" in capsys.readouterr().out


def test_cli_report_keeps_paths_as_given(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "pkg"
    folder.mkdir()
    write_xml(folder, "ok.xml", content=build_xml(document_date=date.today().isoformat()))

    assert main(["pkg"]) == 0
    out = capsys.readouterr().out
    assert f"[OK] {Path('pkg', 'ok.xml')}" in out
    assert str(tmp_path) not in out


def test_cli_rejects_oversized_session(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("MEDINTAKE_MAX_FILES", "1")
    today = date.today().isoformat()
    first = write_xml(tmp_path, "a.xml", content=build_xml(document_id="A", document_date=today))
    second = write_xml(tmp_path, "b.xml", content=build_xml(document_id="B", document_date=today))
    assert main([str(first), str(second)]) == 2
    assert "не больше 1" in capsys.readouterr().err


def test_cli_usage_error_without_output(tmp_path, capsys):
    path = write_xml(tmp_path, "a.xml", content=build_xml(document_date=date.today().isoformat()))
    assert main([str(path), "--format", "csv"]) == 2
    assert "--output" in capsys.readouterr().err
