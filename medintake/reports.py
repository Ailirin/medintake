"""Отчёты сессии: текст для оператора, JSON и CSV для выгрузки."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from medintake.models import BatchReport

_CSV_FIELDS = (
    "file",
    "document_id",
    "severity",
    "code",
    "source",
    "message",
    "line",
    "column",
    "xpath",
)
_TEXT_OK_LIMIT = 20
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def render_text(report: BatchReport) -> str:
    lines: list[str] = []
    show_clean = report.total <= _TEXT_OK_LIMIT
    published = False
    for result in report.results:
        if result.is_valid and not result.issues and not show_clean:
            continue
        published = True
        status = "OK" if result.is_valid else "FAILED"
        lines.append(f"[{status}] {result.file_name}")
        for issue in result.issues:
            if issue.xpath:
                location = issue.xpath
            elif issue.line:
                location = f"строка {issue.line}"
            else:
                location = "файл"
            lines.append(
                f"  [{issue.severity.value}] {issue.code}: {issue.message} ({location})"
            )
    if not published:
        lines.append("Замечаний нет.")
    lines.append(
        f"Итого: {report.valid_count}/{report.total} без ошибок, "
        f"ошибок {report.error_count}, предупреждений {report.warning_count}."
    )
    return "\n".join(lines)


def report_to_json(report: BatchReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"


def report_to_csv(report: BatchReport) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for result in report.results:
        if not result.issues:
            writer.writerow(_csv_row(result.file_name, result.document_id, severity="OK"))
            continue
        for issue in result.issues:
            writer.writerow(
                _csv_row(
                    result.file_name,
                    result.document_id,
                    severity=issue.severity.value,
                    code=issue.code,
                    source=issue.source,
                    message=issue.message,
                    line=issue.line,
                    column=issue.column,
                    xpath=issue.xpath,
                )
            )
    return buffer.getvalue()


def write_json_report(report: BatchReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report_to_json(report), encoding="utf-8")


def write_csv_report(report: BatchReport, output: Path) -> None:
    """UTF-8 с BOM: отчёт открывается в Excel без ручной смены кодировки."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"\xef\xbb\xbf" + report_to_csv(report).encode("utf-8"))


def _csv_row(
    file_name: str,
    document_id: str | None,
    *,
    severity: str,
    code: str = "",
    source: str = "",
    message: str = "",
    line: int | None = None,
    column: int | None = None,
    xpath: str | None = None,
) -> dict[str, object]:
    return {
        "file": _cell(file_name),
        "document_id": _cell(document_id or ""),
        "severity": severity,
        "code": code,
        "source": source,
        "message": _cell(message),
        "line": line if line is not None else "",
        "column": column if column is not None else "",
        "xpath": _cell(xpath or ""),
    }


def _cell(value: str) -> str:
    """Имя файла и DocumentId приходят от отправителя: Excel не должен исполнить их как формулу."""
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value
