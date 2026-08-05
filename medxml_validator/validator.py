"""Ядро валидатора: XSD-проверка и бизнес-правила."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path

from lxml import etree

from medxml_validator.messages import translate_xsd_message


class Severity(str, Enum):
    """Уровень серьёзности найденной проблемы."""

    ERROR = "ERROR"      # Документ невалиден
    WARNING = "WARNING"  # Замечание, не блокирует валидность


@dataclass
class ValidationIssue:
    """Одна найденная проблема в документе."""

    code: str                              # Машиночитаемый код, напр. XSD_VALIDATION
    message: str                           # Текст для оператора
    severity: Severity = Severity.ERROR
    line: int | None = None                # Номер строки в XML (если известен)
    column: int | None = None              # Номер столбца в XML (если известен)
    xpath: str | None = None               # Путь к элементу, напр. /MedicalDocument/Patient

    def to_dict(self) -> dict:
        """Преобразует ошибку в словарь (для JSON-отчётов)."""
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "line": self.line,
            "column": self.column,
            "xpath": self.xpath,
        }


@dataclass
class ValidationResult:
    """Результат проверки одного XML-файла."""

    file_path: Path
    issues: list[ValidationIssue] = field(default_factory=list)
    document_id: str | None = None         # Идентификатор документа для пакетной проверки

    @property
    def is_valid(self) -> bool:
        """True, если нет ошибок уровня ERROR."""
        return not any(issue.severity == Severity.ERROR for issue in self.issues)

    @property
    def error_count(self) -> int:
        """Количество ошибок."""
        return sum(1 for issue in self.issues if issue.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        """Количество предупреждений."""
        return sum(1 for issue in self.issues if issue.severity == Severity.WARNING)


def _local_name(tag: str) -> str:
    """Возвращает локальное имя тега без XML-пространства имён."""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _element_path(element: etree._Element) -> str:
    """Строит XPath-подобный путь к элементу, напр. /MedicalDocument/Patient/Snils."""
    parts: list[str] = []
    current = element
    while current is not None:
        name = _local_name(current.tag)
        if name:
            parts.append(name)
        current = current.getparent()
    return "/" + "/".join(reversed(parts))


def _parse_date(value: str | None) -> date | None:
    """Безопасно парсит дату в формате ISO (YYYY-MM-DD)."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _validate_snils_checksum(snils: str) -> bool:
    """Проверяет контрольную сумму СНИЛС по официальному алгоритму."""
    digits = "".join(ch for ch in snils if ch.isdigit())
    if len(digits) != 11:
        return False

    number = digits[:9]
    checksum = int(digits[9:11])

    total = sum(int(d) * (9 - i) for i, d in enumerate(number))
    if total < 100:
        expected = total
    elif total in (100, 101):
        expected = 0
    else:
        expected = total % 101
        if expected == 100:
            expected = 0

    return checksum == expected


class MedicalXmlValidator:
    """Проверяет XML по XSD-схеме и дополнительным бизнес-правилам."""

    def __init__(self, schema_path: Path):
        if not schema_path.is_file():
            raise FileNotFoundError(f"XSD-схема не найдена: {schema_path}")

        schema_doc = etree.parse(str(schema_path))
        self._schema = etree.XMLSchema(schema_doc)

    def validate_file(self, xml_path: Path) -> ValidationResult:
        """Проверяет один XML-файл и возвращает структурированный результат."""
        result = ValidationResult(file_path=xml_path)

        # Шаг 1: синтаксис XML (закрытые теги, кодировка и т.д.)
        try:
            document = etree.parse(str(xml_path))
        except OSError as exc:
            result.issues.append(
                ValidationIssue(
                    code="FILE_READ_ERROR",
                    message=f"Не удалось прочитать файл: {exc}",
                )
            )
            return result
        except etree.XMLSyntaxError as exc:
            result.issues.append(
                ValidationIssue(
                    code="XML_SYNTAX",
                    message=str(exc),
                    line=exc.lineno,
                    column=exc.offset,
                )
            )
            return result

        root = document.getroot()
        result.document_id = self._text(root, "DocumentId")

        # Шаг 2: структура и типы по XSD-схеме
        if not self._schema.validate(root):
            for error in self._schema.error_log:
                result.issues.append(
                    ValidationIssue(
                        code="XSD_VALIDATION",
                        message=translate_xsd_message(error.message),
                        line=error.line,
                        column=error.column,
                    )
                )
            return result

        # Шаг 3: бизнес-правила, которые XSD не покрывает
        result.issues.extend(self._validate_business_rules(root))
        return result

    def _validate_business_rules(self, root: etree._Element) -> list[ValidationIssue]:
        """Проверяет логические правила: пустые поля, даты, СНИЛС, диагнозы."""
        issues: list[ValidationIssue] = []
        issues.extend(self._validate_empty_required_fields(root))

        document_date = _parse_date(self._text(root, "DocumentDate"))
        if document_date and document_date > date.today():
            issues.append(
                ValidationIssue(
                    code="FUTURE_DOCUMENT_DATE",
                    message="Дата документа не может быть в будущем.",
                    xpath="/MedicalDocument/DocumentDate",
                )
            )

        patient = self._find_child(root, "Patient")
        if patient is not None:
            birth_date = _parse_date(self._text(patient, "BirthDate"))
            if birth_date:
                if birth_date > date.today():
                    issues.append(
                        ValidationIssue(
                            code="FUTURE_BIRTH_DATE",
                            message="Дата рождения не может быть в будущем.",
                            xpath=_element_path(self._find_child(patient, "BirthDate") or patient),
                        )
                    )
                if document_date and birth_date > document_date:
                    issues.append(
                        ValidationIssue(
                            code="BIRTH_AFTER_DOCUMENT",
                            message="Дата рождения позже даты документа.",
                            xpath=_element_path(self._find_child(patient, "BirthDate") or patient),
                        )
                    )
                age_years = self._age_in_years(birth_date, document_date or date.today())
                if age_years > 120:
                    issues.append(
                        ValidationIssue(
                            code="UNREALISTIC_AGE",
                            message="Возраст пациента превышает 120 лет.",
                            xpath=_element_path(self._find_child(patient, "BirthDate") or patient),
                        )
                    )

            # XSD проверяет формат СНИЛС, здесь — контрольную сумму
            snils = self._text(patient, "Snils")
            if snils and not _validate_snils_checksum(snils):
                issues.append(
                    ValidationIssue(
                        code="INVALID_SNILS_CHECKSUM",
                        message="Некорректная контрольная сумма СНИЛС.",
                        xpath=_element_path(self._find_child(patient, "Snils") or patient),
                    )
                )

            for name in ("LastName", "FirstName"):
                value = self._text(patient, name)
                if value and not value[0].isupper():
                    issues.append(
                        ValidationIssue(
                            code="NAME_CASE",
                            message=f"Поле {name} должно начинаться с заглавной буквы.",
                            severity=Severity.WARNING,
                            xpath=_element_path(self._find_child(patient, name) or patient),
                        )
                    )

        diagnoses = [child for child in root if _local_name(child.tag) == "Diagnosis"]
        primary_count = sum(
            1 for diagnosis in diagnoses if self._text(diagnosis, "IsPrimary") == "true"
        )
        if diagnoses and primary_count != 1:
            issues.append(
                ValidationIssue(
                    code="PRIMARY_DIAGNOSIS_COUNT",
                    message="Должен быть ровно один основной диагноз (IsPrimary=true).",
                    xpath="/MedicalDocument/Diagnosis",
                )
            )

        seen_codes: set[str] = set()
        for diagnosis in diagnoses:
            code = self._text(diagnosis, "Icd10Code")
            if not code:
                continue
            if code in seen_codes:
                issues.append(
                    ValidationIssue(
                        code="DUPLICATE_ICD10",
                        message=f"Дублирующийся код МКБ-10: {code}.",
                        xpath=_element_path(self._find_child(diagnosis, "Icd10Code") or diagnosis),
                    )
                )
            seen_codes.add(code)

        return issues

    def _validate_empty_required_fields(self, root: etree._Element) -> list[ValidationIssue]:
        """Проверяет, что обязательные поля не пустые (XSD допускает пустые строки)."""
        issues: list[ValidationIssue] = []

        required_root_fields = (
            ("DocumentId", "Идентификатор документа"),
            ("OrganizationCode", "Код организации"),
        )
        for field_name, label in required_root_fields:
            element = self._find_child(root, field_name)
            value = self._text(root, field_name)
            if element is not None and not value:
                issues.append(
                    ValidationIssue(
                        code="EMPTY_REQUIRED_FIELD",
                        message=f"Обязательное поле «{label}» не может быть пустым.",
                        xpath=_element_path(element),
                    )
                )

        patient = self._find_child(root, "Patient")
        if patient is not None:
            for field_name, label in (
                ("LastName", "Фамилия"),
                ("FirstName", "Имя"),
            ):
                element = self._find_child(patient, field_name)
                value = self._text(patient, field_name)
                if element is not None and not value:
                    issues.append(
                        ValidationIssue(
                            code="EMPTY_REQUIRED_FIELD",
                            message=f"Обязательное поле «{label}» не может быть пустым.",
                            xpath=_element_path(element),
                        )
                    )

        for diagnosis in root:
            if _local_name(diagnosis.tag) != "Diagnosis":
                continue
            description_el = self._find_child(diagnosis, "Description")
            description = self._text(diagnosis, "Description")
            if description_el is not None and not description:
                issues.append(
                    ValidationIssue(
                        code="EMPTY_REQUIRED_FIELD",
                        message="Обязательное поле «Описание диагноза» не может быть пустым.",
                        xpath=_element_path(description_el),
                    )
                )

        return issues

    @staticmethod
    def _find_child(parent: etree._Element, name: str) -> etree._Element | None:
        """Ищет дочерний элемент по локальному имени тега."""
        for child in parent:
            if _local_name(child.tag) == name:
                return child
        return None

    @staticmethod
    def _text(parent: etree._Element, name: str) -> str | None:
        """Возвращает текст дочернего элемента или None."""
        child = MedicalXmlValidator._find_child(parent, name)
        if child is None or child.text is None:
            return None
        value = child.text.strip()
        return value or None

    @staticmethod
    def _age_in_years(birth_date: date, on_date: date) -> int:
        """Считает полный возраст в годах на указанную дату."""
        years = on_date.year - birth_date.year
        if (on_date.month, on_date.day) < (birth_date.month, birth_date.day):
            years -= 1
        return years


def _check_duplicate_document_ids(results: list[ValidationResult]) -> None:
    """Проверяет уникальность DocumentId между файлами в одной пакетной проверке."""
    id_to_files: dict[str, list[Path]] = {}
    for result in results:
        if not result.document_id:
            continue
        id_to_files.setdefault(result.document_id, []).append(result.file_path)

    for document_id, files in id_to_files.items():
        if len(files) < 2:
            continue

        other_files = ", ".join(str(path) for path in files)
        for result in results:
            if result.file_path not in files:
                continue
            result.issues.append(
                ValidationIssue(
                    code="DUPLICATE_DOCUMENT_ID",
                    message=(
                        f"Дублирующийся идентификатор документа «{document_id}». "
                        f"Встречается в файлах: {other_files}."
                    ),
                    xpath="/MedicalDocument/DocumentId",
                )
            )


def validate_files(
    xml_files: list[Path],
    schema_path: Path,
) -> list[ValidationResult]:
    """Проверяет список XML-файлов одной XSD-схемой."""
    validator = MedicalXmlValidator(schema_path)
    results = [validator.validate_file(path) for path in xml_files]
    _check_duplicate_document_ids(results)
    return results


# Быстрый ручной запуск: python -m medxml_validator.validator
if __name__ == "__main__":
    schema = Path("schemas/medical_document.xsd")
    validator = MedicalXmlValidator(schema)

    for name in ("example/valid.xml", "example/invalid.xml"):
        result = validator.validate_file(Path(name))
        status = "OK" if result.is_valid else "FAILED"
        print(f"\n[{status}] {name}")
        for issue in result.issues:
            print(f"  {issue.code}: {issue.message}")
