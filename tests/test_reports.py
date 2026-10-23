"""Форматы отчёта, которыми пользуются оператор и аналитик."""

import csv
import io
import json

from medintake.batch import validate_batch
from medintake.reports import render_text, report_to_csv, write_csv_report, write_json_report
from tests.xml_factory import FIXED_TODAY, build_xml, write_xml


def test_reports_roundtrip(settings, tmp_path):
    good = write_xml(tmp_path, "good.xml")
    bad = write_xml(tmp_path, "bad.xml", content=build_xml(document_id="", gender="X"))
    report = validate_batch([good, bad], settings, today=FIXED_TODAY, batch_id="batch-1")

    text = render_text(report)
    assert "[OK] " in text
    assert "[FAILED] " in text
    assert "XSD_VALIDATION" in text
    assert "Итого: 1/2" in text

    json_path = tmp_path / "report.json"
    csv_path = tmp_path / "report.csv"
    write_json_report(report, json_path)
    write_csv_report(report, csv_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["batch_id"] == "batch-1"
    assert payload["context"]["checked_on"] == FIXED_TODAY.isoformat()
    assert payload["context"]["icd10_mode"] == "warn"
    assert payload["summary"]["valid"] == 1
    assert payload["summary"]["invalid"] == 1
    assert "Иванов" not in json_path.read_text(encoding="utf-8")

    raw_csv = csv_path.read_bytes()
    assert raw_csv.startswith(b"\xef\xbb\xbf")
    csv_text = raw_csv.decode("utf-8-sig")
    assert "document_id" in csv_text
    assert "XSD_VALIDATION" in csv_text
    assert "EMPTY_REQUIRED_FIELD" in csv_text


def test_csv_does_not_let_excel_run_formulas(settings, tmp_path):
    hostile = write_xml(
        tmp_path,
        "=1+1.xml",
        content=build_xml(document_id='=HYPERLINK("http://example.invalid","x")'),
    )
    report = validate_batch([hostile], settings, today=FIXED_TODAY)
    report.results[0].display_name = "=1+1.xml"
    rows = list(csv.DictReader(io.StringIO(report_to_csv(report))))
    assert rows[0]["file"] == "'=1+1.xml"
    assert rows[0]["document_id"].startswith("'=HYPERLINK")
