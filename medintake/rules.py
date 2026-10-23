"""Бизнес-правила поверх XSD.

Новая проверка: функция с той же сигнатурой и строка в _CHECKS внизу файла,
плюс описание кода в checks.py.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from lxml import etree

from medintake.checks import make_issue
from medintake.models import Severity, ValidationIssue
from medintake.xmlutil import child, children, element_path, text_of

_POLICY_SHAPE = re.compile(r"[A-Za-z0-9-]{6,30}")
_POLICY_OMS = re.compile(r"\d{16}")
_ICD_SHAPE = re.compile(r"[A-Z]\d{2}(\.\d{1,2})?")
_MAX_AGE_YEARS = 120
_SNILS_CHECKED_FROM = 1_001_998


@dataclass(frozen=True)
class RuleContext:
    today: date
    icd10: Mapping[str, str]
    icd10_mode: str
    max_document_age_days: int


Rule = Callable[[etree._Element, RuleContext], list[ValidationIssue]]


def snils_checksum_ok(snils: str) -> bool | None:
    """Контрольное число СНИЛС. None — в строке не 11 цифр, формат проверяет XSD.

    Номера до 001-001-998 включительно выданы до введения контрольного числа
    и по правилам ПФР не проверяются.
    """
    digits = "".join(character for character in snils if character.isdigit())
    if len(digits) != 11:
        return None

    number = digits[:9]
    if int(number) <= _SNILS_CHECKED_FROM:
        return True
    checksum = int(digits[9:])
    total = sum(int(digit) * (9 - index) for index, digit in enumerate(number))
    if total < 100:
        expected = total
    elif total in (100, 101):
        expected = 0
    else:
        expected = total % 101
        if expected == 100:
            expected = 0
    return checksum == expected


def parse_iso_date(value: str | None) -> date | None:
    if value is None or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def age_in_years(birth_date: date, on_date: date) -> int:
    years = on_date.year - birth_date.year
    if (on_date.month, on_date.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def _xpath(parent: etree._Element, name: str) -> str:
    element = child(parent, name)
    if element is None:
        return element_path(parent)
    return element_path(element)


def _dated_issue(
    parent: etree._Element,
    field_name: str,
    raw: str | None,
) -> tuple[date | None, ValidationIssue | None]:
    if raw is None:
        return None, None
    parsed = parse_iso_date(raw)
    if parsed is not None:
        return parsed, None
    element = child(parent, field_name)
    return None, make_issue(
        "INVALID_DATE",
        message=f"Поле «{field_name}» должно быть датой в формате ГГГГ-ММ-ДД без часового пояса.",
        xpath=element_path(element) if element is not None else None,
    )


def check_required_values(root: etree._Element, _ctx: RuleContext) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    fields = (
        (root, "DocumentId", "Идентификатор документа"),
        (root, "OrganizationCode", "Код медицинской организации"),
    )
    patient = child(root, "Patient")
    if patient is not None:
        fields = (
            *fields,
            (patient, "LastName", "Фамилия"),
            (patient, "FirstName", "Имя"),
        )
    for parent, field_name, label in fields:
        element = child(parent, field_name)
        if element is None or text_of(parent, field_name):
            continue
        issues.append(
            make_issue(
                "EMPTY_REQUIRED_FIELD",
                message=f"Обязательное поле «{label}» не заполнено.",
                xpath=element_path(element),
            )
        )

    for diagnosis in children(root, "Diagnosis"):
        element = child(diagnosis, "Description")
        if element is None or text_of(diagnosis, "Description"):
            continue
        issues.append(
            make_issue(
                "EMPTY_REQUIRED_FIELD",
                message="Обязательное поле «Описание диагноза» не заполнено.",
                xpath=element_path(element),
            )
        )
    return issues


def check_dates(root: etree._Element, ctx: RuleContext) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    document_raw = text_of(root, "DocumentDate")
    document_date, date_issue = _dated_issue(root, "DocumentDate", document_raw)
    if date_issue:
        issues.append(date_issue)

    if document_date is not None:
        if document_date > ctx.today:
            issues.append(
                make_issue(
                    "FUTURE_DOCUMENT_DATE",
                    xpath=_xpath(root, "DocumentDate"),
                )
            )
        elif (ctx.today - document_date).days > ctx.max_document_age_days:
            issues.append(
                make_issue(
                    "STALE_DOCUMENT_DATE",
                    message=(
                        "Дата документа старше "
                        f"{ctx.max_document_age_days} дней относительно даты приёма."
                    ),
                    xpath=_xpath(root, "DocumentDate"),
                )
            )

    patient = child(root, "Patient")
    if patient is None:
        return issues

    birth_raw = text_of(patient, "BirthDate")
    birth_date, birth_issue = _dated_issue(patient, "BirthDate", birth_raw)
    if birth_issue:
        issues.append(birth_issue)
        return issues
    if birth_date is None:
        return issues

    birth_path = _xpath(patient, "BirthDate")
    if birth_date > ctx.today:
        issues.append(make_issue("FUTURE_BIRTH_DATE", xpath=birth_path))
    if document_date is not None and birth_date > document_date:
        issues.append(make_issue("BIRTH_AFTER_DOCUMENT", xpath=birth_path))

    on_date = document_date or ctx.today
    if age_in_years(birth_date, on_date) > _MAX_AGE_YEARS:
        issues.append(
            make_issue(
                "UNREALISTIC_AGE",
                message=f"На дату документа пациенту больше {_MAX_AGE_YEARS} лет.",
                xpath=birth_path,
            )
        )
    return issues


def check_snils(root: etree._Element, _ctx: RuleContext) -> list[ValidationIssue]:
    patient = child(root, "Patient")
    if patient is None:
        return []
    snils = text_of(patient, "Snils")
    if not snils:
        return []
    valid = snils_checksum_ok(snils)
    if valid is None or valid:
        return []
    return [
        make_issue(
            "INVALID_SNILS_CHECKSUM",
            xpath=_xpath(patient, "Snils"),
        )
    ]


def check_name_case(root: etree._Element, _ctx: RuleContext) -> list[ValidationIssue]:
    patient = child(root, "Patient")
    if patient is None:
        return []
    issues: list[ValidationIssue] = []
    labels = {
        "LastName": "Фамилия должна",
        "FirstName": "Имя должно",
        "MiddleName": "Отчество должно",
    }
    for field_name, label in labels.items():
        value = text_of(patient, field_name)
        if not value or value[0].isupper():
            continue
        issues.append(
            make_issue(
                "NAME_CASE",
                message=f"{label} начинаться с заглавной буквы.",
                xpath=_xpath(patient, field_name),
            )
        )
    return issues


def check_policy(root: etree._Element, _ctx: RuleContext) -> list[ValidationIssue]:
    insurance_type = text_of(root, "InsuranceType")
    policy = text_of(root, "PolicyNumber")
    if insurance_type != "OMS" or not policy or not _POLICY_SHAPE.fullmatch(policy):
        return []
    if _POLICY_OMS.fullmatch(policy):
        return []
    return [
        make_issue(
            "INVALID_POLICY_NUMBER",
            message="Для ОМС номер полиса должен состоять из 16 цифр.",
            xpath=_xpath(root, "PolicyNumber"),
        )
    ]


def check_diagnoses(root: etree._Element, ctx: RuleContext) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    diagnoses = children(root, "Diagnosis")
    if diagnoses:
        primary = sum(
            (text_of(diagnosis, "IsPrimary") or "").lower() in {"true", "1"}
            for diagnosis in diagnoses
        )
        if primary != 1:
            issues.append(
                make_issue(
                    "PRIMARY_DIAGNOSIS_COUNT",
                    message=(
                        "Должен быть ровно один основной диагноз "
                        f"(IsPrimary = true). Сейчас: {primary}."
                    ),
                    xpath=element_path(diagnoses[0]),
                )
            )

    seen: set[str] = set()
    for diagnosis in diagnoses:
        raw_code = text_of(diagnosis, "Icd10Code")
        if not raw_code:
            continue
        code = raw_code.upper()
        code_element = child(diagnosis, "Icd10Code")
        if code_element is None:
            code_element = diagnosis
        if code in seen:
            issues.append(
                make_issue(
                    "DUPLICATE_ICD10",
                    message=f"Код МКБ-10 «{code}» указан повторно.",
                    xpath=element_path(code_element),
                )
            )
        seen.add(code)

        if ctx.icd10_mode == "off" or not _ICD_SHAPE.fullmatch(code):
            continue
        if code in ctx.icd10:
            continue
        severity = Severity.ERROR if ctx.icd10_mode == "error" else Severity.WARNING
        issues.append(
            make_issue(
                "ICD10_UNKNOWN",
                message=f"Код МКБ-10 «{code}» отсутствует в справочнике.",
                severity=severity,
                xpath=element_path(code_element),
            )
        )
    return issues


_CHECKS: tuple[Rule, ...] = (
    check_required_values,
    check_dates,
    check_snils,
    check_name_case,
    check_policy,
    check_diagnoses,
)


def collect_business_issues(root: etree._Element, ctx: RuleContext) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for rule in _CHECKS:
        issues.extend(rule(root, ctx))
    return issues
