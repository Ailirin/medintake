"""Правила документа: СНИЛС, даты, полис, диагнозы, совместный сбор ошибок."""

from datetime import date

import pytest

from medintake.checks import cap_issues, make_issue
from medintake.config import Settings
from medintake.errors import CatalogError
from medintake.icd10 import load_icd10
from medintake.models import Severity
from medintake.rules import snils_checksum_ok
from medintake.validator import DocumentChecker
from tests.xml_factory import EXAMPLES, FIXED_TODAY, build_xml, write_xml


def _codes(path, settings, **kwargs):
    result = DocumentChecker(settings, today=FIXED_TODAY).validate_file(path)
    return result, [issue.code for issue in result.issues]


def test_snils_checksum_branches():
    assert snils_checksum_ok("112-233-445 95") is True
    assert snils_checksum_ok("112-233-445 00") is False
    assert snils_checksum_ok("112233445") is None
    # Сумма 100 и 101 дают контрольное число 00, остаток 100 тоже.
    assert snils_checksum_ok("001-019-989 00") is True
    assert snils_checksum_ok("001-019-989 01") is False
    assert snils_checksum_ok("001-019-998 00") is True
    assert snils_checksum_ok("002-999-989 00") is True
    assert snils_checksum_ok("123-456-789 64") is True
    # До 001-001-998 включительно контрольное число не проверяется.
    assert snils_checksum_ok("001-001-998 55") is True
    assert snils_checksum_ok("001-001-999 55") is False


def test_example_package_is_clean(settings):
    validator = DocumentChecker(settings, today=FIXED_TODAY)
    for name in ("valid.xml", "package/doc_001.xml", "package/doc_002.xml"):
        result = validator.validate_file(EXAMPLES / name)
        assert result.issues == [], name


def test_invalid_example_keeps_xsd_and_catalog_findings(settings):
    result = DocumentChecker(settings, today=FIXED_TODAY).validate_file(EXAMPLES / "invalid.xml")
    codes = [issue.code for issue in result.issues]
    assert codes.count("XSD_VALIDATION") >= 3
    assert "ICD10_UNKNOWN" not in codes
    assert any("недопустимо" in issue.message for issue in result.issues)
    assert result.is_valid is False


def test_empty_document_id(settings, tmp_path):
    result, codes = _codes(EXAMPLES / "empty_document_id.xml", settings)
    assert "EMPTY_REQUIRED_FIELD" in codes
    assert result.document_id is None
    assert result.is_valid is False

    blank = write_xml(tmp_path, "blank-name.xml", content=build_xml(last_name="   "))
    _, codes = _codes(blank, settings)
    assert "EMPTY_REQUIRED_FIELD" in codes


def test_xsd_does_not_hide_business_errors(settings, tmp_path):
    path = write_xml(
        tmp_path,
        "both.xml",
        content=build_xml(gender="X", snils="112-233-445 00"),
    )
    result, codes = _codes(path, settings)
    assert "XSD_VALIDATION" in codes
    assert "INVALID_SNILS_CHECKSUM" in codes
    snils_issue = next(issue for issue in result.issues if issue.code == "INVALID_SNILS_CHECKSUM")
    assert snils_issue.xpath == "/MedicalDocument/Patient/Snils"


def test_errors_do_not_leak_between_files(settings, tmp_path):
    bad = write_xml(tmp_path, "bad.xml", content=build_xml(gender="X"))
    good = write_xml(tmp_path, "good.xml")
    validator = DocumentChecker(settings, today=FIXED_TODAY)
    assert validator.validate_file(bad).is_valid is False
    assert validator.validate_file(good).issues == []


def test_dates_and_age(settings, tmp_path):
    future = write_xml(tmp_path, "future.xml", content=build_xml(document_date="2026-12-01"))
    _, codes = _codes(future, settings)
    assert codes == ["FUTURE_DOCUMENT_DATE"]

    stale = write_xml(tmp_path, "stale.xml", content=build_xml(document_date="2024-01-01"))
    result, codes = _codes(stale, settings)
    assert codes == ["STALE_DOCUMENT_DATE"]
    assert result.is_valid is True

    born_later = write_xml(
        tmp_path,
        "born.xml",
        content=build_xml(birth_date="2026-11-01"),
    )
    _, codes = _codes(born_later, settings)
    assert "BIRTH_AFTER_DOCUMENT" in codes
    assert "FUTURE_BIRTH_DATE" in codes

    old = write_xml(tmp_path, "old.xml", content=build_xml(birth_date="1900-01-01"))
    _, codes = _codes(old, settings)
    assert "UNREALISTIC_AGE" in codes


def test_policy_and_name(settings, tmp_path):
    policy = write_xml(tmp_path, "policy.xml", content=build_xml(policy_number="DMS-2026-1"))
    _, codes = _codes(policy, settings)
    assert codes == ["INVALID_POLICY_NUMBER"]

    short = write_xml(tmp_path, "short.xml", content=build_xml(policy_number="123"))
    _, codes = _codes(short, settings)
    assert "XSD_VALIDATION" in codes
    assert "INVALID_POLICY_NUMBER" not in codes

    dms = write_xml(
        tmp_path,
        "dms.xml",
        content=build_xml(insurance_type="DMS", policy_number="DMS-2026-1042"),
    )
    assert _codes(dms, settings)[1] == []

    name = write_xml(tmp_path, "name.xml", content=build_xml(first_name="иван"))
    result, codes = _codes(name, settings)
    assert codes == ["NAME_CASE"]
    assert result.is_valid is True


def test_diagnoses(settings, tmp_path):
    duplicated = write_xml(
        tmp_path,
        "dup.xml",
        content=build_xml(
            diagnoses=[
                ("J06.9", "ОРВИ", True),
                ("J06.9", "Повтор", False),
            ]
        ),
    )
    _, codes = _codes(duplicated, settings)
    assert "DUPLICATE_ICD10" in codes

    no_primary = write_xml(
        tmp_path,
        "none.xml",
        content=build_xml(diagnoses=[("J06.9", "ОРВИ", False)]),
    )
    _, codes = _codes(no_primary, settings)
    assert "PRIMARY_DIAGNOSIS_COUNT" in codes

    unknown = write_xml(
        tmp_path,
        "unknown.xml",
        content=build_xml(diagnoses=[("Z99.99", "Нет в справочнике", True)]),
    )
    result, codes = _codes(unknown, settings)
    assert codes == ["ICD10_UNKNOWN"]
    assert result.is_valid is True

    strict = settings.model_copy(update={"icd10_mode": "error"})
    result = DocumentChecker(strict, today=FIXED_TODAY).validate_file(unknown)
    assert result.is_valid is False
    assert result.issues[0].severity.value == "ERROR"

    disabled = settings.model_copy(update={"icd10_mode": "off"})
    result = DocumentChecker(disabled, today=date(2026, 10, 22)).validate_file(unknown)
    assert result.issues == []


def test_primary_flag_accepts_numeric_boolean(settings, tmp_path):
    path = write_xml(
        tmp_path,
        "bit.xml",
        content=build_xml(diagnoses=[("J06.9", "ОРВИ", True)]).replace(
            "<IsPrimary>true</IsPrimary>",
            "<IsPrimary>1</IsPrimary>",
        ),
    )
    assert _codes(path, settings)[1] == []


def test_catalog_rejects_duplicates_and_missing_columns(tmp_path):
    broken = tmp_path / "dup.csv"
    broken.write_text("code,title\nA00,Холера\nA00,Повтор\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="повторяется"):
        load_icd10(broken)

    header = tmp_path / "bad.csv"
    header.write_text("id,name\nA00,Холера\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="колонки"):
        load_icd10(header)


def test_issue_list_is_capped():
    issues = [make_issue("NAME_CASE", message=str(index)) for index in range(250)]
    capped = cap_issues(issues)
    assert len(capped) == 201
    assert capped[-1].code == "ISSUES_TRUNCATED"
    assert capped[-1].severity == Severity.WARNING

    issues.append(make_issue("XSD_VALIDATION", message="за пределами окна"))
    assert cap_issues(issues)[-1].severity == Severity.ERROR


def test_today_follows_configured_timezone(settings):
    moscow = settings.model_copy(update={"timezone": "Europe/Moscow"})
    samoa = settings.model_copy(update={"timezone": "Pacific/Kiritimati"})
    assert (samoa.today() - moscow.today()).days in (0, 1)
    with pytest.raises(ValueError, match="часовой пояс"):
        Settings(timezone="Mars/Olympus")
