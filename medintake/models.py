"""Модели результата проверки. Их используют CLI, API и отчёты."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True)
class BatchItem:
    """Файл внутри сессии проверки."""

    display_name: str
    path: Path | None = None
    too_large: bool = False


@dataclass
class ValidationIssue:
    """Одна проблема в документе."""

    code: str
    message: str
    severity: Severity = Severity.ERROR
    source: str = "business"
    line: int | None = None
    column: int | None = None
    xpath: str | None = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "source": self.source,
            "line": self.line,
            "column": self.column,
            "xpath": self.xpath,
        }


@dataclass
class ValidationResult:
    """Результат проверки одного файла."""

    file_path: Path
    display_name: str | None = None
    issues: list[ValidationIssue] = field(default_factory=list)
    document_id: str | None = None
    content_hash: str | None = None

    @property
    def file_name(self) -> str:
        return self.display_name or self.file_path.name

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == Severity.ERROR for issue in self.issues)

    @property
    def error_count(self) -> int:
        return sum(issue.severity == Severity.ERROR for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == Severity.WARNING for issue in self.issues)

    def to_dict(self) -> dict:
        return {
            "file": self.file_name,
            "document_id": self.document_id,
            "valid": self.is_valid,
            "errors": self.error_count,
            "warnings": self.warning_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class BatchReport:
    """Отчёт по всей сессии."""

    batch_id: str
    results: list[ValidationResult]
    started_at: str
    finished_at: str
    context: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def valid_count(self) -> int:
        return sum(result.is_valid for result in self.results)

    @property
    def invalid_count(self) -> int:
        return self.total - self.valid_count

    @property
    def error_count(self) -> int:
        return sum(result.error_count for result in self.results)

    @property
    def warning_count(self) -> int:
        return sum(result.warning_count for result in self.results)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "context": dict(self.context),
            "summary": {
                "total": self.total,
                "valid": self.valid_count,
                "invalid": self.invalid_count,
                "errors": self.error_count,
                "warnings": self.warning_count,
            },
            "results": [result.to_dict() for result in self.results],
        }
