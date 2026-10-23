"""Реестр проверок.

Аналитик описывает здесь смысл кода, разработчик реализует его в rules.py.
Список отдаётся в API, чтобы QA сверяла отчёт с контрактом, а не с исходниками.
"""

from __future__ import annotations

from dataclasses import dataclass

from medintake.models import Severity, ValidationIssue


@dataclass(frozen=True)
class Check:
    code: str
    severity: Severity
    source: str
    title: str
    description: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "source": self.source,
            "title": self.title,
            "description": self.description,
        }


ALL_CHECKS: tuple[Check, ...] = (
    Check(
        "FILE_READ_ERROR",
        Severity.ERROR,
        "io",
        "Чтение файла",
        "Файл не открывается: нет доступа или он исчез во время приёма.",
    ),
    Check(
        "FILE_TOO_LARGE",
        Severity.ERROR,
        "io",
        "Размер файла",
        "Файл больше лимита сессии и не разбирается, чтобы пакет не занимал память.",
    ),
    Check(
        "XML_SYNTAX",
        Severity.ERROR,
        "xml",
        "Синтаксис XML",
        "Документ не является корректным XML: незакрытый тег, кодировка, недопустимый символ.",
    ),
    Check(
        "XSD_VALIDATION",
        Severity.ERROR,
        "xsd",
        "Структура по XSD",
        "Нарушены состав элементов, обязательность, формат даты, пола, СНИЛС, кода МКБ-10 или полиса.",
    ),
    Check(
        "ISSUES_TRUNCATED",
        Severity.ERROR,
        "io",
        "Список замечаний обрезан",
        "По файлу накоплено слишком много замечаний. В отчёте остаётся начало списка. "
        "Уровень — предупреждение, если среди всех замечаний файла не было ошибок.",
    ),
    Check(
        "EMPTY_REQUIRED_FIELD",
        Severity.ERROR,
        "business",
        "Пустое обязательное поле",
        "Элемент есть, но значение пустое или состоит из пробелов. XSD такую строку пропускает.",
    ),
    Check(
        "INVALID_DATE",
        Severity.ERROR,
        "business",
        "Неразобранная дата",
        "Дата прошла XSD, но не приводится к календарной дате без часового пояса (ГГГГ-ММ-ДД).",
    ),
    Check(
        "FUTURE_DOCUMENT_DATE",
        Severity.ERROR,
        "business",
        "Дата документа в будущем",
        "Дата документа позже даты приёма пакета.",
    ),
    Check(
        "STALE_DOCUMENT_DATE",
        Severity.WARNING,
        "business",
        "Документ старше окна подачи",
        "Дата документа старше MEDINTAKE_MAX_DOCUMENT_AGE_DAYS. Файл не отклоняется, но попадает в отчёт.",
    ),
    Check(
        "FUTURE_BIRTH_DATE",
        Severity.ERROR,
        "business",
        "Дата рождения в будущем",
        "Дата рождения позже даты приёма пакета.",
    ),
    Check(
        "BIRTH_AFTER_DOCUMENT",
        Severity.ERROR,
        "business",
        "Рождение позже документа",
        "Дата рождения позже даты документа.",
    ),
    Check(
        "UNREALISTIC_AGE",
        Severity.ERROR,
        "business",
        "Неправдоподобный возраст",
        "На дату документа пациенту больше 120 полных лет.",
    ),
    Check(
        "INVALID_SNILS_CHECKSUM",
        Severity.ERROR,
        "business",
        "Контрольное число СНИЛС",
        "Формат СНИЛС верный, но контрольное число не сходится с алгоритмом ПФР.",
    ),
    Check(
        "NAME_CASE",
        Severity.WARNING,
        "business",
        "Регистр ФИО",
        "Фамилия, имя или отчество не начинаются с заглавной буквы.",
    ),
    Check(
        "INVALID_POLICY_NUMBER",
        Severity.ERROR,
        "business",
        "Номер полиса",
        "Для ОМС номер полиса — 16 цифр. Для ДМС достаточно формата из XSD.",
    ),
    Check(
        "PRIMARY_DIAGNOSIS_COUNT",
        Severity.ERROR,
        "business",
        "Основной диагноз",
        "В документе должен быть ровно один диагноз с IsPrimary = true.",
    ),
    Check(
        "DUPLICATE_ICD10",
        Severity.ERROR,
        "business",
        "Повтор кода МКБ-10",
        "Один и тот же код МКБ-10 указан в документе больше одного раза.",
    ),
    Check(
        "ICD10_UNKNOWN",
        Severity.WARNING,
        "business",
        "Код вне справочника МКБ-10",
        "Код формально похож на МКБ-10, но его нет в загруженном справочнике. "
        "Пока выгрузка НСИ неполная, режим warn не блокирует файл. "
        "После загрузки полного справочника включают MEDINTAKE_ICD10_MODE=error.",
    ),
    Check(
        "DUPLICATE_DOCUMENT_ID",
        Severity.ERROR,
        "batch",
        "Повтор идентификатора документа",
        "DocumentId встречается в сессии больше одного раза.",
    ),
    Check(
        "DUPLICATE_CONTENT",
        Severity.ERROR,
        "batch",
        "Одинаковое содержимое файлов",
        "В сессии есть разные файлы с совпадающим SHA-256 содержимого.",
    ),
)

CHECKS: dict[str, Check] = {item.code: item for item in ALL_CHECKS}

MAX_ISSUES_PER_FILE = 200


def make_issue(
    code: str,
    *,
    message: str | None = None,
    severity: Severity | None = None,
    line: int | None = None,
    column: int | None = None,
    xpath: str | None = None,
) -> ValidationIssue:
    spec = CHECKS[code]
    return ValidationIssue(
        code=spec.code,
        message=message or spec.description,
        severity=severity or spec.severity,
        source=spec.source,
        line=line,
        column=column,
        xpath=xpath,
    )


def cap_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    if len(issues) <= MAX_ISSUES_PER_FILE:
        return issues
    total = len(issues)
    has_errors = any(issue.severity == Severity.ERROR for issue in issues)
    kept = issues[:MAX_ISSUES_PER_FILE]
    kept.append(
        make_issue(
            "ISSUES_TRUNCATED",
            message=f"Показаны первые {MAX_ISSUES_PER_FILE} замечаний из {total}.",
            severity=Severity.ERROR if has_errors else Severity.WARNING,
        )
    )
    return kept
