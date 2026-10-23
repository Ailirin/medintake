"""Сессия проверки: лимит пакета, правила между файлами, отчёт."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from medintake import __version__
from medintake.checks import make_issue
from medintake.config import Settings
from medintake.errors import BatchLimitExceeded, EmptyBatch
from medintake.models import BatchItem, BatchReport, ValidationIssue, ValidationResult
from medintake.validator import DocumentChecker, too_large_issue

Progress = Callable[[int, int], None]
_LIST_LIMIT = 5


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def validate_batch(
    items: Sequence[BatchItem | Path],
    settings: Settings,
    *,
    batch_id: str | None = None,
    today: date | None = None,
    on_progress: Progress | None = None,
) -> BatchReport:
    normalized = [_as_item(item) for item in items]
    if not normalized:
        raise EmptyBatch()
    if len(normalized) > settings.max_files:
        raise BatchLimitExceeded(len(normalized), settings.max_files)

    started_at = utc_now()
    validator = DocumentChecker(settings, today=today)
    results: list[ValidationResult] = []
    total = len(normalized)
    for index, item in enumerate(normalized, start=1):
        results.append(_validate_item(validator, item, settings.max_file_bytes))
        if on_progress and (index == total or index % 50 == 0):
            on_progress(index, total)

    _mark_duplicate_ids(results)
    _mark_duplicate_content(results)
    return BatchReport(
        batch_id=batch_id or uuid4().hex,
        results=results,
        started_at=started_at,
        finished_at=utc_now(),
        context={
            "service_version": __version__,
            "checked_on": validator.today.isoformat(),
            "timezone": settings.timezone,
            "icd10_mode": settings.icd10_mode,
        },
    )


def _as_item(item: BatchItem | Path) -> BatchItem:
    if isinstance(item, BatchItem):
        return item
    return BatchItem(path=item, display_name=str(item))


def _validate_item(
    validator: DocumentChecker,
    item: BatchItem,
    max_file_bytes: int,
) -> ValidationResult:
    if item.too_large:
        return ValidationResult(
            file_path=item.path or Path(item.display_name),
            display_name=item.display_name,
            issues=[too_large_issue(max_file_bytes)],
        )
    if item.path is None:
        return ValidationResult(
            file_path=Path(item.display_name),
            display_name=item.display_name,
            issues=[too_large_issue(max_file_bytes)],
        )
    return validator.validate_file(item.path, display_name=item.display_name)


def _mark_duplicate_ids(results: list[ValidationResult]) -> None:
    grouped: dict[str, list[ValidationResult]] = defaultdict(list)
    for result in results:
        if result.document_id:
            grouped[result.document_id].append(result)

    for document_id, group in grouped.items():
        if len(group) < 2:
            continue
        names = _join_names(group)
        for result in group:
            result.issues.append(
                _duplicate_issue(
                    "DUPLICATE_DOCUMENT_ID",
                    (
                        f"Идентификатор документа «{document_id}» повторяется в пакете. "
                        f"Файлы: {names}."
                    ),
                    "/MedicalDocument/DocumentId",
                )
            )


def _mark_duplicate_content(results: list[ValidationResult]) -> None:
    grouped: dict[str, list[ValidationResult]] = defaultdict(list)
    for result in results:
        if result.content_hash:
            grouped[result.content_hash].append(result)

    for group in grouped.values():
        if len(group) < 2:
            continue
        names = _join_names(group)
        for result in group:
            result.issues.append(
                _duplicate_issue(
                    "DUPLICATE_CONTENT",
                    f"Содержимое файла совпадает с другими файлами пакета: {names}.",
                    "/MedicalDocument",
                )
            )


def _duplicate_issue(code: str, message: str, xpath: str) -> ValidationIssue:
    return make_issue(code, message=message, xpath=xpath)


def _join_names(results: Sequence[ValidationResult]) -> str:
    labels = [result.file_name for result in results]
    shown = ", ".join(labels[:_LIST_LIMIT])
    extra = len(labels) - _LIST_LIMIT
    if extra > 0:
        shown += f" и ещё {extra}"
    return shown
