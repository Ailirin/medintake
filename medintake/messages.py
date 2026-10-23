"""Перевод типовых сообщений libxml2 на русский.

Неизвестный текст возвращается как есть: лучше сырое сообщение, чем потерянная ошибка.
"""

from __future__ import annotations

import re

# libxml2 пишет либо «Element 'Gender':», либо «Element 'MedicalDocument', attribute 'version':».
_HEAD = r"(?:Element|Attribute) '([^']+)'(?:, attribute '([^']+)')?"


def _field_name(match: re.Match[str]) -> str:
    return match.group(2) or match.group(1)


def translate_xsd_message(message: str) -> str:
    text = message.strip()

    match = re.search(
        _HEAD + r": \[facet 'enumeration'\] The value '([^']*)' "
        r"is not an element of the set \{([^}]+)\}\.",
        text,
    )
    if match:
        value, allowed = match.group(3), match.group(4)
        allowed_clean = re.sub(r"'([^']*)'", r"«\1»", allowed)
        return (
            f"Поле «{_field_name(match)}»: значение «{value}» недопустимо. "
            f"Допустимые значения: {allowed_clean}."
        )

    match = re.search(
        _HEAD + r": \[facet 'pattern'\] The value '([^']*)' "
        r"is not accepted by the pattern '([^']*)'\.",
        text,
    )
    if match:
        return (
            f"Поле «{_field_name(match)}»: значение «{match.group(3)}» "
            "не соответствует требуемому формату."
        )

    match = re.search(
        _HEAD + r": '([^']*)' is not a valid value of the atomic type 'xs:date'\.",
        text,
    )
    if match:
        return (
            f"Поле «{_field_name(match)}»: «{match.group(3)}» — некорректная дата. "
            "Ожидается формат ГГГГ-ММ-ДД."
        )

    match = re.search(
        _HEAD + r": '([^']*)' is not a valid value of the atomic type 'xs:boolean'\.",
        text,
    )
    if match:
        return (
            f"Поле «{_field_name(match)}»: «{match.group(3)}» — некорректное логическое значение. "
            "Ожидается true или false."
        )

    if "Missing child element" in text:
        match = re.search(r"Expected is \( (.+) \)\.", text)
        expected = match.group(1) if match else "обязательный элемент"
        return f"Отсутствует обязательный элемент. Ожидается: {expected}."

    match = re.search(_HEAD + r": This element is not expected\.", text)
    if match:
        return f"Элемент «{match.group(1)}» не ожидается в этой позиции."

    match = re.search(r"The attribute '([^']+)' is required but missing\.", text)
    if match:
        return f"Отсутствует обязательный атрибут «{match.group(1)}»."

    match = re.search(_HEAD + r": The attribute is required but missing\.", text)
    if match:
        return f"Отсутствует обязательный атрибут «{_field_name(match)}»."

    return text
