"""Автотесты валидатора медицинских XML-документов."""

from pathlib import Path

from medxml_validator.messages import translate_xsd_message
from medxml_validator.validator import MedicalXmlValidator, validate_files

# Пути относительно корня проекта (pytest запускается из D:\Project\Utility)
SCHEMA = Path("schemas/medical_document.xsd")
EXAMPLES = Path("example")


def test_valid_xml_passes():
    """Корректный документ должен проходить все проверки."""
    validator = MedicalXmlValidator(SCHEMA)
    result = validator.validate_file(EXAMPLES / "valid.xml")
    assert result.is_valid


def test_invalid_xml_fails():
    """Документ с ошибками XSD должен быть отклонён."""
    validator = MedicalXmlValidator(SCHEMA)
    result = validator.validate_file(EXAMPLES / "invalid.xml")
    assert not result.is_valid
    assert result.error_count == 3


def test_xsd_messages_are_translated_to_russian():
    """Сообщения XSD должны переводиться на русский."""
    english = (
        "Element 'Gender': [facet 'enumeration'] The value 'X' "
        "is not an element of the set {'M', 'F'}."
    )
    russian = translate_xsd_message(english)
    assert "Элемент «Gender»" in russian
    assert "недопустимо" in russian


def test_empty_document_id_fails():
    """Пустой DocumentId должен отклоняться бизнес-правилом."""
    validator = MedicalXmlValidator(SCHEMA)
    result = validator.validate_file(EXAMPLES / "empty_document_id.xml")
    assert not result.is_valid
    assert any(issue.code == "EMPTY_REQUIRED_FIELD" for issue in result.issues)


def test_duplicate_document_id_between_files():
    """Одинаковый DocumentId в разных файлах должен быть ошибкой."""
    results = validate_files(
        [EXAMPLES / "duplicate_a.xml", EXAMPLES / "duplicate_b.xml"],
        SCHEMA,
    )
    assert all(not result.is_valid for result in results)
    assert all(
        any(issue.code == "DUPLICATE_DOCUMENT_ID" for issue in result.issues)
        for result in results
    )
