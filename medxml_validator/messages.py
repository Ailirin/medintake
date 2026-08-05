"""Перевод сообщений XSD-валидатора lxml на русский язык."""

from __future__ import annotations

import re


def translate_xsd_message(message: str) -> str:
    """Преобразует типовое сообщение lxml в понятный русский текст."""
    text = message.strip()

    match = re.search(
        r"Element '([^']+)': \[facet 'enumeration'\] The value '([^']*)' "
        r"is not an element of the set \{([^}]+)\}\.",
        text,
    )
    if match:
        element, value, allowed = match.groups()
        return (
            f"Элемент «{element}»: значение «{value}» недопустимо. "
            f"Допустимые значения: {allowed}."
        )

    match = re.search(
        r"Element '([^']+)': \[facet 'pattern'\] The value '([^']*)' "
        r"is not accepted by the pattern '([^']*)'\.",
        text,
    )
    if match:
        element, value, _pattern = match.groups()
        return f"Элемент «{element}»: значение «{value}» не соответствует требуемому формату."

    match = re.search(
        r"Element '([^']+)': '([^']*)' is not a valid value of the atomic type 'xs:date'\.",
        text,
    )
    if match:
        element, value = match.groups()
        return f"Элемент «{element}»: «{value}» — некорректная дата. Ожидается формат ГГГГ-ММ-ДД."

    match = re.search(
        r"Element '([^']+)': '([^']*)' is not a valid value of the atomic type 'xs:boolean'\.",
        text,
    )
    if match:
        element, value = match.groups()
        return f"Элемент «{element}»: «{value}» — некорректное логическое значение. Ожидается true или false."

    if "Missing child element" in text:
        match = re.search(r"Expected is \( (.+) \)\.", text)
        expected = match.group(1) if match else "обязательный элемент"
        return f"Отсутствует обязательный элемент. Ожидается: {expected}."

    match = re.search(r"Element '([^']+)': This element is not expected\.", text)
    if match:
        return f"Элемент «{match.group(1)}» не ожидается в этой позиции."

    match = re.search(
        r"Element '([^']+)': Character content other than whitespace is not allowed "
        r"because the content type is 'element-only'\.",
        text,
    )
    if match:
        return f"Элемент «{match.group(1)}» не может содержать текст — только дочерние элементы."

    match = re.search(r"Attribute '([^']+)': The attribute is not allowed\.", text)
    if match:
        return f"Атрибут «{match.group(1)}» не разрешён."

    match = re.search(r"Attribute '([^']+)': The attribute is required but missing\.", text)
    if match:
        return f"Отсутствует обязательный атрибут «{match.group(1)}»."

    return text
