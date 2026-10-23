"""Проверка одного документа: чтение, XSD, бизнес-правила."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from lxml import etree

from medintake.checks import cap_issues, make_issue
from medintake.config import Settings
from medintake.icd10 import load_icd10
from medintake.messages import translate_xsd_message
from medintake.models import ValidationIssue, ValidationResult
from medintake.rules import RuleContext, collect_business_issues
from medintake.xmlutil import secure_parser, short_text, text_of


def _schema_parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True)


class DocumentChecker:
    """Проверяет файлы одной сессии. Экземпляр не шарится между потоками."""

    def __init__(self, settings: Settings, *, today: date | None = None) -> None:
        if not settings.schema_path.is_file():
            raise FileNotFoundError(f"XSD-схема не найдена: {settings.schema_path}")
        try:
            schema_doc = etree.parse(str(settings.schema_path), _schema_parser())
            self._schema = etree.XMLSchema(schema_doc)
        except etree.XMLSchemaParseError as exc:
            raise ValueError(f"XSD-схема повреждена: {settings.schema_path}") from exc

        self._settings = settings
        self._today = today or settings.today()
        self._icd10 = {} if settings.icd10_mode == "off" else load_icd10(settings.icd10_path)

    @property
    def today(self) -> date:
        return self._today

    def validate_file(
        self,
        xml_path: Path,
        *,
        display_name: str | None = None,
    ) -> ValidationResult:
        result = ValidationResult(file_path=xml_path, display_name=display_name)
        try:
            payload = xml_path.read_bytes()
        except OSError as exc:
            result.issues.append(
                make_issue(
                    "FILE_READ_ERROR",
                    message=f"Не удалось прочитать файл: {exc.strerror or 'ошибка ввода-вывода'}.",
                )
            )
            return result

        if len(payload) > self._settings.max_file_bytes:
            result.issues.append(too_large_issue(self._settings.max_file_bytes))
            return result

        result.content_hash = hashlib.sha256(payload).hexdigest()
        try:
            root = etree.fromstring(payload, secure_parser())
        except etree.XMLSyntaxError as exc:
            detail = getattr(exc, "msg", None) or str(exc)
            result.issues.append(
                make_issue(
                    "XML_SYNTAX",
                    message=f"Синтаксическая ошибка XML: {short_text(detail)}",
                    line=exc.lineno,
                    column=getattr(exc, "offset", None),
                )
            )
            return result

        if not isinstance(root, etree._Element):
            result.issues.append(
                make_issue("XML_SYNTAX", message="В файле нет корневого элемента XML.")
            )
            return result

        result.document_id = text_of(root, "DocumentId")
        issues: list[ValidationIssue] = []
        issues.extend(self._xsd_issues(root))
        issues.extend(
            collect_business_issues(
                root,
                RuleContext(
                    today=self._today,
                    icd10=self._icd10,
                    icd10_mode=self._settings.icd10_mode,
                    max_document_age_days=self._settings.max_document_age_days,
                ),
            )
        )
        result.issues = cap_issues(issues)
        return result

    def _xsd_issues(self, root: etree._Element) -> list[ValidationIssue]:
        error_log = self._schema.error_log
        clear = getattr(error_log, "clear", None)
        if callable(clear):
            clear()
        if self._schema.validate(root):
            return []

        issues: list[ValidationIssue] = []
        for error in self._schema.error_log:
            path = getattr(error, "path", None) or None
            issues.append(
                make_issue(
                    "XSD_VALIDATION",
                    message=translate_xsd_message(error.message),
                    line=error.line or None,
                    column=error.column or None,
                    xpath=path,
                )
            )
        return issues


def too_large_issue(limit: int) -> ValidationIssue:
    return make_issue(
        "FILE_TOO_LARGE",
        message=f"Размер файла превышает допустимые {byte_limit_text(limit)}.",
    )


def byte_limit_text(limit: int) -> str:
    mebibyte = 1024 * 1024
    if limit % mebibyte == 0:
        return f"{limit // mebibyte} МБ"
    if limit % 1024 == 0:
        return f"{limit // 1024} КБ"
    return f"{limit} байт"
