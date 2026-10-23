"""Разбор дерева XML без привязки к конкретным правилам."""

from __future__ import annotations

from lxml import etree


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def child(parent: etree._Element, name: str) -> etree._Element | None:
    for item in parent:
        if local_name(item.tag) == name:
            return item
    return None


def children(parent: etree._Element, name: str) -> list[etree._Element]:
    return [item for item in parent if local_name(item.tag) == name]


def text_of(parent: etree._Element, name: str) -> str | None:
    element = child(parent, name)
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def element_path(element: etree._Element) -> str:
    parts: list[str] = []
    current: etree._Element | None = element
    while current is not None:
        name = local_name(current.tag)
        parent = current.getparent()
        if name and parent is not None:
            siblings = [item for item in parent if local_name(item.tag) == name]
            if len(siblings) > 1:
                name = f"{name}[{siblings.index(current) + 1}]"
        if name:
            parts.append(name)
        current = parent
    return "/" + "/".join(reversed(parts))


def secure_parser() -> etree.XMLParser:
    """Парсер для внешних документов: без внешних сущностей и без сети."""
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def short_text(value: str, limit: int = 400) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
