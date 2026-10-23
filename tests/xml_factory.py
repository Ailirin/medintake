"""Сборка тестовых документов. Дата приёма фиксирована и не зависит от календаря."""

from __future__ import annotations

from datetime import date
from pathlib import Path

FIXED_TODAY = date(2026, 10, 22)
ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "example"


def build_xml(
    *,
    document_id: str = "DOC-2026-001",
    document_date: str = "2026-10-01",
    insurance_type: str = "OMS",
    insurer_code: str = "77001",
    policy_number: str = "7700123456789012",
    organization_code: str = "ORG-001",
    last_name: str = "Иванов",
    first_name: str = "Иван",
    middle_name: str | None = "Иванович",
    birth_date: str = "1985-03-20",
    gender: str = "M",
    snils: str = "112-233-445 95",
    diagnoses: list[tuple[str, str, bool]] | None = None,
    version: str = "1.1",
) -> str:
    if diagnoses is None:
        diagnoses = [("J06.9", "Острая инфекция верхних дыхательных путей", True)]
    middle = ""
    if middle_name is not None:
        middle = f"    <MiddleName>{middle_name}</MiddleName>\n"
    blocks: list[str] = []
    for code, description, primary in diagnoses:
        flag = "true" if primary else "false"
        blocks.append(
            "  <Diagnosis>\n"
            f"    <Icd10Code>{code}</Icd10Code>\n"
            f"    <Description>{description}</Description>\n"
            f"    <IsPrimary>{flag}</IsPrimary>\n"
            "  </Diagnosis>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<MedicalDocument version="{version}">\n'
        f"  <DocumentId>{document_id}</DocumentId>\n"
        f"  <DocumentDate>{document_date}</DocumentDate>\n"
        f"  <InsuranceType>{insurance_type}</InsuranceType>\n"
        f"  <InsurerCode>{insurer_code}</InsurerCode>\n"
        f"  <PolicyNumber>{policy_number}</PolicyNumber>\n"
        f"  <OrganizationCode>{organization_code}</OrganizationCode>\n"
        "  <Patient>\n"
        f"    <LastName>{last_name}</LastName>\n"
        f"    <FirstName>{first_name}</FirstName>\n"
        f"{middle}"
        f"    <BirthDate>{birth_date}</BirthDate>\n"
        f"    <Gender>{gender}</Gender>\n"
        f"    <Snils>{snils}</Snils>\n"
        "  </Patient>\n"
        + "\n".join(blocks)
        + "\n</MedicalDocument>\n"
    )


def write_xml(directory: Path, name: str, content: str | None = None, **kwargs: object) -> Path:
    if content is None:
        content = build_xml(**kwargs)  # type: ignore[arg-type]
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path
